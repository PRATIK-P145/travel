"""Workflow: validate -> research -> AI itinerary -> normalize -> budget -> result.

Also holds the deterministic fallback planner used when Gemini is unavailable.
"""
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import settings
from models import PlanResponse, TripRequest
from services import gemini, groq, travel_research
from services.gemini import GeminiError
from services.groq import GroqError
from services.travel_research import ResearchError

log = logging.getLogger("travel_genie.planner")

CATEGORIES = [
    ("transportation", "Transportation"),
    ("accommodation", "Accommodation"),
    ("food", "Food"),
    ("activities", "Activities and sightseeing"),
    ("miscellaneous", "Miscellaneous"),
]

# Rough, fixed conversion rates from INR. NOT live exchange rates.
RATES_FROM_INR = {"INR": 1.0, "USD": 0.012, "EUR": 0.011, "GBP": 0.0095, "AED": 0.044, "SGD": 0.016, "THB": 0.40}

# Rough planning costs in INR, per style. These are assumptions, not quotes.
STYLE_COSTS = {
    "budget":   {"intercity": 2000, "local_day": 400,  "room": 1200, "food": 500,  "act": 300},
    "balanced": {"intercity": 3500, "local_day": 700,  "room": 2500, "food": 800,  "act": 600},
    "luxury":   {"intercity": 8000, "local_day": 2000, "room": 7000, "food": 2500, "act": 2000},
}
MISC_RATE = 0.08
LEVEL_MULT = {0: 0.0, 1: 0.4, 2: 0.8, 3: 1.4}  # activity cost level -> multiple of per-person daily activity rate

DISCLAIMER = (
    "All costs are estimates and suggestions, not quotes. Prices, opening hours and availability "
    "have not been verified, and nothing has been booked."
)


# --------------------------------------------------------------------------- helpers
def _step(currency: str) -> int:
    return 10 if RATES_FROM_INR.get(currency, 1.0) >= 0.3 else 1


def _round_money(value: float, currency: str) -> int:
    step = _step(currency)
    return int(round(value / step) * step)


def _num(value: Any) -> Optional[float]:
    """Non-negative finite number or None."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x) or x < 0:
        return None
    return x


def _text(value: Any, default: str = "", maxlen: int = 600) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:maxlen]
    return default


def _text_list(value: Any, default: list[str], limit: int = 8) -> list[str]:
    if isinstance(value, list):
        items = [_text(v, "", 300) for v in value]
        items = [i for i in items if i][:limit]
        if items:
            return items
    return default


def _money(n: int, currency: str) -> str:
    return f"{currency} {n:,}"


# --------------------------------------------------------------------------- budget
def heuristic_lines(trip: TripRequest) -> dict[str, int]:
    """Rule-of-thumb category estimates in the trip currency (used for fallback and gap filling)."""
    s = STYLE_COSTS[trip.travel_style]
    fx = RATES_FROM_INR.get(trip.currency, 1.0)
    d, n = trip.duration_days, trip.travelers
    same_city = trip.start_city.strip().lower() == trip.destination.strip().lower()
    nights = max(d - 1, 0)
    rooms = math.ceil(n / 2)
    raw = {
        "transportation": (0 if same_city else s["intercity"] * n) + s["local_day"] * d,
        "accommodation": s["room"] * rooms * nights,
        "food": s["food"] * n * d,
        "activities": s["act"] * n * d,
    }
    raw["miscellaneous"] = MISC_RATE * sum(raw.values())
    return {k: _round_money(v * fx, trip.currency) for k, v in raw.items()}


def compute_budget(lines: dict[str, int], trip: TripRequest) -> dict:
    """Backend-owned budget math: the total is always the sum of the category lines."""
    cleaned = {k: max(int(lines.get(k, 0)), 0) for k, _ in CATEGORIES}
    total = sum(cleaned.values())
    budget = int(round(trip.budget))
    diff = budget - total
    within = diff >= 0

    suggestions: list[str] = []
    if not within:
        over = -diff
        suggestions.append(f"The estimate is about {_money(over, trip.currency)} over your budget.")
        lower = {"luxury": "balanced", "balanced": "budget"}.get(trip.travel_style)
        if lower:
            alt = sum(heuristic_lines(trip.model_copy(update={"travel_style": lower})).values())
            suggestions.append(
                f"Switching to a {lower} travel style would bring a rough estimate to about {_money(alt, trip.currency)}."
            )
        if cleaned["transportation"] > 0.25 * total:
            suggestions.append(
                f"Compare trains or buses with flights for the {trip.start_city} to {trip.destination} leg, and book early."
            )
        if trip.duration_days > 1:
            saving = _round_money(total / trip.duration_days, trip.currency)
            suggestions.append(f"Cutting one day would save roughly {_money(saving, trip.currency)}.")
        suggestions.append("Pick guesthouses or hostels, eat where locals eat, and favour free or low-cost sights.")
    else:
        suggestions.append(
            f"Estimated spend leaves about {_money(diff, trip.currency)} of your budget as a buffer. "
            "Keep some for surprises or use it to upgrade a stay or activity."
        )

    return {
        "currency": trip.currency,
        "lines": [{"key": k, "label": label, "amount": cleaned[k]} for k, label in CATEGORIES],
        "estimated_total": total,
        "user_budget": budget,
        "difference": diff,
        "within_budget": within,
        "per_person": int(round(total / trip.travelers)),
        "suggestions": suggestions,
        "note": "Estimates only. Based on typical cost ranges for your travel style, not live prices.",
    }


# --------------------------------------------------------------------------- fallback content
# (title, description, cost level 0-3). All are suggestions to be verified by the traveller.
GOA_POOL = {
    "beaches": [
        ("Baga and Calangute beach time", "Swim and stroll on the busy North Goa beaches. Stay within lifeguard-patrolled areas and obey flag warnings.", 0),
        ("Vagator beach and Chapora Fort viewpoint", "A quieter beach plus a short climb to the fort ruins for coastal views.", 0),
        ("Palolem or Colva beach day", "Calmer South Goa beaches suited to a slow, relaxed day.", 0),
    ],
    "food": [
        ("Goan seafood meal at a local eatery", "Try a traditional fish-curry-and-rice meal. Check recent reviews to choose a place.", 1),
        ("Fontainhas café and bakery walk, Panjim", "Combine the colourful heritage lanes with cafés and local bakeries.", 1),
        ("Seafood meal at a beach shack", "Casual seafood by the sea. Confirm prices before ordering.", 2),
    ],
    "history": [
        ("Old Goa churches", "Visit the Basilica of Bom Jesus and nearby Se Cathedral. Dress modestly.", 0),
        ("Fort Aguada and lighthouse area", "A Portuguese-era fort with sea views. Check entry rules before you go.", 1),
        ("Fontainhas Latin Quarter walk", "Wander the Portuguese-era lanes of Panjim.", 0),
    ],
    "nature": [
        ("Dudhsagar Falls excursion", "A popular waterfall trip. Access and jeep arrangements vary by season, so confirm before you go.", 3),
        ("Salim Ali Bird Sanctuary", "Mangrove walks and birdwatching near Old Goa. Check opening times.", 1),
        ("Quiet beach or headland viewpoint", "Pick a less crowded spot and arrive early for the light.", 0),
    ],
    "adventure": [
        ("Water sports at a North Goa beach", "Operators offer parasailing and similar activities. Compare operators and check safety gear.", 2),
        ("Kayaking or mangrove boat trip", "Guided trips run in some areas. Check operator reviews first.", 2),
        ("Inland village scooter ride", "Only with a valid licence and a helmet. Avoid riding at night.", 1),
    ],
    "shopping": [
        ("Mapusa market", "Local market for spices, cashews and handicrafts. Check operating days.", 1),
        ("Anjuna flea market", "A seasonal market. Confirm it is running on your dates.", 1),
        ("Panjim shopping streets", "Handicrafts, textiles and local snacks.", 1),
    ],
    "culture": [
        ("Spice plantation visit", "Guided tours often include a meal. Compare operators.", 2),
        ("Panjim heritage and museum stop", "Learn about Goa's Portuguese and local history. Check opening days.", 1),
        ("Local village and temple visit", "Respect dress codes and photography rules.", 0),
    ],
    "nightlife": [
        ("Beach shacks and clubs along Baga and Calangute", "A lively evening strip. Keep valuables secure and plan a safe ride back.", 2),
        ("Sunset drinks at a beach shack", "A relaxed evening option.", 1),
        ("Live music night in North Goa", "Check venue schedules before heading out.", 2),
    ],
}

GENERIC_POOL = {
    "beaches": [("Beach or waterfront time in {d}", "Relax by the water. Check safety conditions and local rules.", 0), ("Shoreline walk in {d}", "Pick a viewpoint and go early to avoid crowds.", 0), ("Boat or ferry ride near {d}", "Compare operators and check reviews.", 2)],
    "food": [("Local cuisine tasting in {d}", "Try regional specialties at well-reviewed local eateries.", 1), ("Street food and market walk in {d}", "Go where locals queue. Choose busy stalls.", 1), ("Cooking class or food tour in {d}", "Optional guided experience. Compare options and prices.", 2)],
    "history": [("Heritage sites and old town walk in {d}", "Visit the main historic landmarks. Check entry fees.", 1), ("Local museum visit in {d}", "A good rainy-day or midday option. Check opening days.", 1), ("Historic neighbourhood stroll in {d}", "Wander older lanes and architecture.", 0)],
    "nature": [("Scenic viewpoint or nature walk near {d}", "Choose an easy route. Check weather and trail access.", 0), ("Park or garden visit in {d}", "A relaxed outdoor stop.", 0), ("Day trip to a nearby natural attraction", "Check transport and timings before committing.", 2)],
    "adventure": [("Guided outdoor activity near {d}", "Options vary by season. Choose licensed operators.", 2), ("Cycling or hiking route near {d}", "Match the route to your fitness level.", 1), ("Water or adventure sport near {d}", "Check safety standards and cancellation terms.", 2)],
    "shopping": [("Local market in {d}", "Browse crafts, clothing and food items. Bargain politely.", 1), ("Handicraft and souvenir shopping in {d}", "Look for local artisans.", 1), ("Main shopping street in {d}", "Compare prices before buying.", 1)],
    "culture": [("Cultural landmark or temple visit in {d}", "Respect dress codes and photography rules.", 0), ("Local arts, crafts or performance in {d}", "Check schedules in advance.", 1), ("Neighbourhood walk with a local guide in {d}", "A guided walk is a good way to learn the area.", 2)],
    "nightlife": [("Evening dining and live music in {d}", "Pick a well-reviewed venue.", 2), ("Night market or lively district in {d}", "Stay in busy areas and plan a safe ride back.", 1), ("Rooftop or lounge evening in {d}", "Check prices and dress codes.", 2)],
}

THEMES = {
    "beaches": "Beaches and relaxation", "food": "Food and flavours", "history": "History and heritage",
    "nature": "Nature and scenery", "adventure": "Adventure day", "shopping": "Markets and shopping",
    "culture": "Culture and local life", "nightlife": "Evenings out",
}
BREAKFASTS = ["Hotel or local café breakfast", "Regional breakfast at a popular local spot", "Bakery or café near your stay"]
LUNCHES = ["Regional specialty at a well-reviewed local eatery", "Casual lunch near the day's sights", "Local thali or set meal"]
DINNERS = ["Dinner at a well-reviewed local restaurant", "Casual dinner near your stay", "Try a local specialty for dinner"]
GOA_BREAKFASTS = ["Goan-style breakfast such as poi bread with bhaji, or a café near your stay", "Local café breakfast", "Hotel breakfast"]
GOA_LUNCHES = ["Goan fish thali at a local eatery", "Casual lunch at a beach shack", "Xacuti or vindaloo at a local restaurant"]
GOA_DINNERS = ["Seafood dinner at a beach shack", "Dinner in Panjim or a village restaurant", "Casual dinner near your stay"]


def _is_goa(trip: TripRequest) -> bool:
    return "goa" in trip.destination.lower()


def fallback_plan(trip: TripRequest) -> dict:
    """Deterministic itinerary built from templates. The same trip always gives the same plan."""
    dest, start = trip.destination, trip.start_city
    goa = _is_goa(trip)
    pool = GOA_POOL if goa else GENERIC_POOL
    interests = list(trip.interests) or ["culture", "food", "nature"]
    s = STYLE_COSTS[trip.travel_style]
    fx = RATES_FROM_INR.get(trip.currency, 1.0)
    same_city = start.strip().lower() == dest.strip().lower()
    counters: dict[str, int] = {}
    cursor = [0]
    evenings = [0]
    d_total = trip.duration_days

    def pick(slot: str = "afternoon") -> tuple[str, dict]:
        # Nightlife ideas only make sense in the evening; on alternate evenings prefer them
        # when requested, and never schedule them in other slots.
        interest = None
        if slot == "evening":
            evenings[0] += 1
            if "nightlife" in interests and evenings[0] % 2 == 1:
                interest = "nightlife"
        if interest is None:
            for _ in range(len(interests)):
                candidate = interests[cursor[0] % len(interests)]
                cursor[0] += 1
                if candidate != "nightlife" or slot == "evening":
                    interest = candidate
                    break
            else:
                interest = "culture"
        entries = pool[interest]
        title, desc, level = entries[counters.get(interest, 0) % len(entries)]
        counters[interest] = counters.get(interest, 0) + 1
        cost = _round_money(LEVEL_MULT[level] * s["act"] * trip.travelers * fx, trip.currency)
        return interest, {"title": title.format(d=dest), "description": desc, "est_cost": cost}

    def free(title: str, desc: str) -> dict:
        return {"title": title, "description": desc, "est_cost": 0}

    days = []
    for i in range(d_total):
        n = i + 1
        date_str = (trip.departure_date + timedelta(days=i)).isoformat()
        first, last = i == 0, i == d_total - 1
        theme_interest = None

        if d_total == 1:
            theme_interest, morning = pick("morning")
            _, afternoon = pick("afternoon")
            _, evening = pick("evening")
            title = f"Day trip: {THEMES[theme_interest]}"
            transport = f"Plan travel between {start} and {dest} early. Compare train, bus and flight options." if not same_city else "Use local taxis, ride-hailing apps or public transport. Agree on fares before riding."
        elif first:
            morning = free(f"Travel from {start} to {dest}", "Compare train, bus and flight options and book early. Travel time depends on the mode." if not same_city else "Start the trip and head to your stay.")
            afternoon = free("Check in and settle", "Drop bags, freshen up and get a feel for the neighbourhood.")
            theme_interest, evening = pick("evening")
            title = "Arrive and settle in"
            transport = f"Intercity travel from {start}. Compare options and book early (prices not verified)." if not same_city else "Use local transport to reach your stay."
        elif last:
            theme_interest, morning = pick("morning")
            shop = "shopping" in interests
            afternoon = free(
                "Souvenirs and last bites" if shop else "Free time, packing and check-out",
                "Pick up local gifts and snacks, then check out." if shop else "Relax, pack and check out. Keep luggage safe if you leave late.",
            )
            evening = free(f"Return to {start}", "Allow buffer time before departure. Confirm your return booking details." if not same_city else "Head home and wrap up the trip.")
            title = "Last morning and departure"
            transport = f"Return journey to {start}. Leave buffer time for delays." if not same_city else "Local transport back."
        else:
            theme_interest, morning = pick("morning")
            _, afternoon = pick("afternoon")
            _, evening = pick("evening")
            title = THEMES[theme_interest]
            transport = "Use local taxis, ride-hailing apps or public transport. Agree on fares before riding."

        b = GOA_BREAKFASTS if goa else BREAKFASTS
        l = GOA_LUNCHES if goa else LUNCHES
        dn = GOA_DINNERS if goa else DINNERS
        slots = {"morning": morning, "afternoon": afternoon, "evening": evening}
        days.append(
            {
                "day": n,
                "date": date_str,
                "title": title,
                **slots,
                "meals": {"breakfast": b[i % 3], "lunch": l[i % 3], "dinner": dn[i % 3]},
                "transport": transport,
                "est_day_cost": sum(x["est_cost"] for x in slots.values()),
            }
        )

    pref = f" You asked for: {trip.accommodation}. Filter for it when you search." if trip.accommodation else ""
    room = _round_money(s["room"] * fx, trip.currency)
    if goa:
        stays = [
            {"area": "Calangute, Baga or Candolim (North Goa)", "why": "Lively, close to beaches, food and nightlife." + pref, "est_per_night": room},
            {"area": "Panjim and Fontainhas", "why": "Central, walkable heritage area, good for culture and food." + pref, "est_per_night": room},
            {"area": "Palolem or Colva (South Goa)", "why": "Calmer beaches and a slower pace." + pref, "est_per_night": room},
        ]
    else:
        stays = [
            {"area": f"Central {dest}", "why": "Close to main sights, food and transport." + pref, "est_per_night": room},
            {"area": f"Quieter neighbourhood in {dest}", "why": "Often better value and calmer evenings." + pref, "est_per_night": _round_money(room * 0.85, trip.currency)},
        ]

    tips = [
        "Book intercity transport and stays early, since prices usually rise close to travel dates.",
        "Carry a government photo ID and keep digital copies of tickets and bookings.",
        "Check opening days and entry fees on official or map listings before each visit.",
        "Keep a buffer of about 10 percent of your budget for unexpected costs.",
    ]
    caveats = [
        "This is a template plan. Activities are suggestions and have not been checked against current conditions.",
        "Costs are rough estimates for your travel style, not quotes or live prices.",
        "Weather, crowds and closures vary by season. Check forecasts before you go.",
    ]
    if goa:
        tips.append("Respect lifeguard flags and avoid swimming after dark.")
        if trip.departure_date.month in (6, 7, 8, 9):
            caveats.append("Goa's monsoon season (roughly June to September) can close beach shacks and water sports. Check conditions.")

    overview = (
        f"A {d_total}-day {trip.travel_style} trip from {start} to {dest} for {trip.travelers} "
        f"traveler{'s' if trip.travelers != 1 else ''}, focused on {', '.join(interests)}. "
        "This is a sample itinerary built from templates."
    )
    return {
        "overview": overview,
        "days": days,
        "accommodation": stays,
        "tips": tips,
        "caveats": caveats,
        "budget_lines": heuristic_lines(trip),
    }


# --------------------------------------------------------------------------- AI normalization
def _slot(raw: Any, fallback: dict, currency: str) -> tuple[dict, bool]:
    """Return (slot, used_fallback)."""
    if isinstance(raw, dict) and _text(raw.get("title")):
        cost = _num(raw.get("est_cost"))
        return (
            {
                "title": _text(raw.get("title"), maxlen=150),
                "description": _text(raw.get("description"), maxlen=400),
                "est_cost": _round_money(cost or 0, currency),
            },
            False,
        )
    return dict(fallback), True


def normalize_ai_plan(raw: Any, trip: TripRequest, fb: dict) -> tuple[dict, list[str]]:
    """Validate Gemini's JSON and coerce it to the plan shape, filling gaps from the fallback.

    Raises ValueError when the response is unusable (for example, no days at all).
    """
    if not isinstance(raw, dict):
        raise ValueError("AI response is not an object")
    ai_days = raw.get("days")
    if not isinstance(ai_days, list) or not ai_days:
        raise ValueError("AI response has no days")

    notes: list[str] = []
    days, filled = [], 0
    for i in range(trip.duration_days):
        fbd = fb["days"][i]
        rd = ai_days[i] if i < len(ai_days) and isinstance(ai_days[i], dict) else None
        if rd is None:
            days.append(fbd)
            filled += 1
            continue
        day = {"day": i + 1, "date": fbd["date"], "title": _text(rd.get("title"), fbd["title"], 120)}
        for name in ("morning", "afternoon", "evening"):
            slot, used_fb = _slot(rd.get(name), fbd[name], trip.currency)
            day[name] = slot
            filled += used_fb
        raw_meals = rd.get("meals") if isinstance(rd.get("meals"), dict) else {}
        day["meals"] = {m: _text(raw_meals.get(m), fbd["meals"][m], 300) for m in ("breakfast", "lunch", "dinner")}
        day["transport"] = _text(rd.get("transport"), fbd["transport"], 300)
        day["est_day_cost"] = sum(day[n]["est_cost"] for n in ("morning", "afternoon", "evening"))
        days.append(day)
    if filled:
        notes.append(f"{filled} part(s) of the AI itinerary were missing and were filled in with template suggestions.")

    stays = []
    for s in raw.get("accommodation") or []:
        if isinstance(s, dict) and _text(s.get("area")):
            stays.append(
                {
                    "area": _text(s.get("area"), maxlen=120),
                    "why": _text(s.get("why"), maxlen=300),
                    "est_per_night": _round_money(_num(s.get("est_per_night")) or 0, trip.currency),
                }
            )
    stays = stays[:5] or fb["accommodation"]

    # Budget lines: use AI numbers where valid, heuristic estimates otherwise.
    ai_budget = raw.get("budget_estimate") if isinstance(raw.get("budget_estimate"), dict) else {}
    lines, gap = {}, False
    for key, _ in CATEGORIES:
        v = _num(ai_budget.get(key))
        if v is None:
            lines[key] = fb["budget_lines"][key]
            gap = True
        else:
            lines[key] = _round_money(v, trip.currency)
    # Activities can't be lower than the sum of itemised activity costs.
    lines["activities"] = max(lines["activities"], sum(d["est_day_cost"] for d in days))
    if gap:
        notes.append("Some budget categories were missing from the AI response and use rule-of-thumb estimates.")

    plan = {
        "overview": _text(raw.get("overview"), fb["overview"], 800),
        "days": days,
        "accommodation": stays,
        "tips": _text_list(raw.get("tips"), fb["tips"]),
        "caveats": _text_list(raw.get("caveats"), fb["caveats"]),
        "budget_lines": lines,
    }
    return plan, notes


# --------------------------------------------------------------------------- orchestration
def redact_provider_error(provider_name: str, error: Exception) -> str:
    """Redact exception details before logging them."""
    if provider_name == "groq":
        return groq.redact(error)
    return gemini.redact(error)


async def build_plan(trip: TripRequest) -> PlanResponse:
    warnings: list[str] = []
    fb = fallback_plan(trip)

    # 1. Optional live research
    research_source, sources, context = "not_configured", [], ""
    if settings.serpapi_key():
        try:
            res = await travel_research.fetch_research(trip)
            sources, context = res["sources"], res["context"]
            research_source = "serpapi" if sources else "unavailable"
            if not sources:
                warnings.append("Live search ran but returned no usable results.")
        except ResearchError as e:
            research_source = "unavailable"
            log.warning("Research failed: %s", e)
            warnings.append(f"Live research was unavailable: {e}")
        except Exception:  # never let research break a plan
            log.exception("Unexpected research failure")
            research_source = "unavailable"
            warnings.append("Live research was unavailable because of an unexpected error.")
    else:
        warnings.append("No live research was performed because SERPAPI_API_KEY is not set.")

    # 2. Try Gemini first, then Groq, then the deterministic demo fallback.
    plan, itinerary_source, model = fb, "fallback", None
    ai_status, ai_error = "not_configured", None
    providers = []
    if settings.gemini_key():
        providers.append(("gemini", settings.gemini_model(), gemini.generate_itinerary))
    if settings.groq_key():
        providers.append(("groq", settings.groq_model(), groq.generate_itinerary))

    if not providers:
        ai_error = "Neither GEMINI_API_KEY nor GROQ_API_KEY is set"
        warnings.append("No AI provider key is configured, so this is a demo itinerary built from templates.")
    else:
        for provider_name, provider_model, generate in providers:
            try:
                raw = await generate(trip, context)
                plan, notes = normalize_ai_plan(raw, trip, fb)
                warnings += notes
                itinerary_source, model, ai_status = provider_name, provider_model, "ok"
                if provider_name == "groq":
                    warnings.append("Gemini was unavailable or returned an unusable response; Groq generated this itinerary as the backup provider.")
                break
            except (GeminiError, GroqError) as e:
                ai_status, ai_error = e.category, str(e)
                log.warning("%s provider failed; trying next configured provider if available: %s", provider_name, ai_error)
                warnings.append(f"{provider_name.title()} was not used: {ai_error}.")
            except (ValueError, TypeError, KeyError, AttributeError) as e:
                ai_status, ai_error = "invalid_response", f"{provider_name.title()} answered, but the itinerary JSON was missing required parts"
                log.warning("%s response could not be used: %s: %s", provider_name, type(e).__name__, redact_provider_error(provider_name, e))
                warnings.append(f"{provider_name.title()} was not used: {ai_error}.")
            except Exception as e:
                ai_status, ai_error = "request_failed", f"Unexpected error while calling {provider_name.title()}"
                log.exception("Unexpected %s failure (%s)", provider_name, type(e).__name__)
                warnings.append(f"{provider_name.title()} was not used: {ai_error}.")

        if itinerary_source == "fallback":
            warnings.append("All configured AI providers failed. A demo itinerary is shown instead.")

    if itinerary_source in ("gemini", "groq"):
        log.info("ITINERARY_SOURCE=%s model=%s research=%s", itinerary_source, model, research_source)
    else:
        level = logging.INFO if ai_status == "not_configured" else logging.WARNING
        log.log(level, "ITINERARY_SOURCE=fallback (DEMO MODE) reason=%s model=%s research=%s error=%s",
                ai_status, settings.gemini_model(), research_source, ai_error or "-")

    if itinerary_source == "fallback" and sources:
        warnings.append("The research links below are live Google results, but the demo itinerary does not use them.")

    # 3. Budget (always computed by the backend)
    budget = compute_budget(plan["budget_lines"], trip)

    return PlanResponse(
        trip={
            "start_city": trip.start_city,
            "destination": trip.destination,
            "departure_date": trip.departure_date.isoformat(),
            "return_date": trip.return_date.isoformat(),
            "duration_days": trip.duration_days,
            "travelers": trip.travelers,
            "budget": trip.budget,
            "currency": trip.currency,
            "travel_style": trip.travel_style,
            "interests": trip.interests,
        },
        overview=plan["overview"],
        days=plan["days"],
        accommodation=plan["accommodation"],
        budget=budget,
        tips=plan["tips"],
        caveats=plan["caveats"],
        sources=sources,
        meta={
            "itinerary_source": itinerary_source,
            "research_source": research_source,
            "model": model,
            "ai_status": ai_status,
            "ai_error": None if ai_status in ("ok", "not_configured") else ai_error,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "verified": False,
            "warnings": warnings,
            "disclaimer": DISCLAIMER,
        },
    )
