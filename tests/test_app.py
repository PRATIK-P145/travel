"""Travel-Genie tests. All external services are mocked: no API keys or network needed."""
import asyncio
import json
import logging
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app as app_module
from models import TripRequest
from services import gemini, planner, travel_research
from services.gemini import GeminiError
from services.travel_research import ResearchError


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    """Every test starts in demo mode, regardless of the developer's real .env."""
    for k in ("GEMINI_API_KEY", "SERPAPI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def client():
    return TestClient(app_module.app)


def payload(**overrides):
    data = {
        "start_city": "Pune",
        "destination": "Goa",
        "departure_date": (date.today() + timedelta(days=10)).isoformat(),
        "duration_days": 3,
        "travelers": 2,
        "budget": 25000,
        "currency": "INR",
        "travel_style": "balanced",
        "interests": ["food", "beaches"],
        "accommodation": "",
        "notes": "",
    }
    data.update(overrides)
    return data


def trip(**overrides) -> TripRequest:
    return TripRequest(**payload(**overrides))


def good_ai_plan(days=3):
    slot = lambda t, c: {"title": t, "description": "A suggestion.", "est_cost": c}
    return {
        "overview": "AI overview.",
        "days": [
            {
                "day": i + 1, "title": f"AI day {i + 1}",
                "morning": slot("AI morning", 500), "afternoon": slot("AI afternoon", 700), "evening": slot("AI evening", 300),
                "meals": {"breakfast": "b", "lunch": "l", "dinner": "d"}, "transport": "Taxi",
            }
            for i in range(days)
        ],
        "accommodation": [{"area": "Candolim", "why": "Quiet", "est_per_night": 2500}],
        "budget_estimate": {"transportation": 8000, "accommodation": 5000, "food": 4800, "activities": 3000, "miscellaneous": 1000},
        "tips": ["AI tip"], "caveats": ["AI caveat"],
    }


# ------------------------------------------------------------------ health + frontend
def test_health_without_keys(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["mode"] == "demo"
    assert body["integrations"]["gemini"]["configured"] is False
    assert body["integrations"]["serpapi"]["configured"] is False


def test_health_reports_keys_without_leaking_them(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-gemini")
    monkeypatch.setenv("SERPAPI_API_KEY", "super-secret-serp")
    r = client.get("/api/health")
    assert r.json()["integrations"]["gemini"]["configured"] is True
    assert r.json()["integrations"]["serpapi"]["configured"] is True
    assert "super-secret" not in r.text


def test_frontend_is_served_and_calls_plan_endpoint(client):
    page = client.get("/")
    assert page.status_code == 200 and "Travel-Genie" in page.text
    js = client.get("/static/app.js")
    assert js.status_code == 200 and '"/api/plan"' in js.text and '"/api/health"' in js.text
    assert client.get("/static/styles.css").status_code == 200


# ------------------------------------------------------------------ valid request, demo mode
def test_plan_without_keys_returns_complete_fallback(client):
    r = client.post("/api/plan", json=payload())
    assert r.status_code == 200
    body = r.json()
    for key in ("trip", "overview", "days", "accommodation", "budget", "tips", "caveats", "sources", "meta"):
        assert key in body
    assert len(body["days"]) == 3
    for d in body["days"]:
        for part in ("morning", "afternoon", "evening"):
            assert d[part]["title"]
        assert d["meals"]["lunch"] and d["transport"]
    meta = body["meta"]
    assert meta["itinerary_source"] == "fallback"
    assert meta["research_source"] == "not_configured"
    assert meta["verified"] is False
    assert any("GEMINI_API_KEY" in w for w in meta["warnings"])
    assert body["sources"] == []
    assert body["trip"]["return_date"] == (date.today() + timedelta(days=12)).isoformat()


def test_fallback_is_deterministic():
    a, b = planner.fallback_plan(trip()), planner.fallback_plan(trip())
    assert a == b


def test_fallback_handles_one_day_and_generic_destination():
    plan = planner.fallback_plan(trip(duration_days=1, destination="Lisbon", interests=[]))
    assert len(plan["days"]) == 1 and plan["days"][0]["morning"]["title"]
    assert "Lisbon" in json.dumps(plan)


def test_return_date_derives_duration(client):
    dep = date.today() + timedelta(days=20)
    r = client.post("/api/plan", json=payload(departure_date=dep.isoformat(), duration_days=None, return_date=(dep + timedelta(days=4)).isoformat()))
    assert r.status_code == 200 and r.json()["trip"]["duration_days"] == 5 and len(r.json()["days"]) == 5


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"destination": ""}, "destination"),
        ({"start_city": " "}, "start_city"),
        ({"departure_date": (date.today() - timedelta(days=1)).isoformat()}, "__model__"),
        ({"departure_date": "not-a-date"}, "departure_date"),
        ({"duration_days": 0}, "duration_days"),
        ({"duration_days": 30}, "duration_days"),
        ({"travelers": 0}, "travelers"),
        ({"travelers": "two"}, "travelers"),
        ({"budget": 0}, "budget"),
        ({"budget": -5}, "budget"),
        ({"currency": "XYZ"}, "currency"),
        ({"interests": ["skydiving"]}, "interests"),
        ({"travel_style": "extravagant"}, "travel_style"),
    ],
)
def test_invalid_input_returns_readable_422(client, overrides, field):
    r = client.post("/api/plan", json=payload(**overrides))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["message"] and err["details"]
    assert all(d["message"] for d in err["details"])


def test_missing_required_fields(client):
    r = client.post("/api/plan", json={})
    assert r.status_code == 422
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert {"start_city", "destination", "departure_date", "budget"} <= fields
    assert "required" in r.json()["error"]["message"]


def test_return_before_departure_and_mismatch_rejected(client):
    dep = date.today() + timedelta(days=10)
    r = client.post("/api/plan", json=payload(duration_days=None, return_date=(dep - timedelta(days=1)).isoformat()))
    assert r.status_code == 422 and "Return date" in r.json()["error"]["message"]
    r = client.post("/api/plan", json=payload(duration_days=3, return_date=(dep + timedelta(days=7)).isoformat()))
    assert r.status_code == 422 and "don't match" in r.json()["error"]["message"]


def test_neither_return_date_nor_duration_rejected(client):
    r = client.post("/api/plan", json=payload(duration_days=None))
    assert r.status_code == 422 and "return date or a trip duration" in r.json()["error"]["message"]


def test_malformed_json_body_is_consistent_error(client):
    r = client.post("/api/plan", content="{nope", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_unknown_route_uses_error_shape(client):
    r = client.get("/api/nothing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "http_error"


def test_interests_are_normalised():
    assert trip(interests=["Food", "food", " BEACHES "]).interests == ["food", "beaches"]


# ------------------------------------------------------------------ budget calculations
def test_budget_total_is_sum_of_lines_and_difference_correct():
    t = trip(budget=25000)
    b = planner.compute_budget({"transportation": 9000, "accommodation": 5000, "food": 4800, "activities": 3600, "miscellaneous": 1800}, t)
    assert b["estimated_total"] == 24200 == sum(l["amount"] for l in b["lines"])
    assert b["difference"] == 800 and b["within_budget"] is True
    assert b["per_person"] == 12100
    assert len(b["lines"]) == 5


def test_budget_over_adds_adjustment_suggestions():
    t = trip(budget=10000, travel_style="luxury")
    b = planner.compute_budget(planner.heuristic_lines(t), t)
    assert b["within_budget"] is False and b["difference"] < 0
    text = " ".join(b["suggestions"])
    assert "over your budget" in text and "balanced" in text


def test_budget_clamps_negative_values():
    b = planner.compute_budget({"transportation": -500, "food": 100}, trip())
    assert all(l["amount"] >= 0 for l in b["lines"]) and b["estimated_total"] == 100


def test_heuristic_scales_with_style_and_currency():
    cheap, lux = planner.heuristic_lines(trip(travel_style="budget")), planner.heuristic_lines(trip(travel_style="luxury"))
    assert sum(lux.values()) > sum(cheap.values())
    usd = planner.heuristic_lines(trip(currency="USD", budget=500))
    assert sum(usd.values()) < sum(planner.heuristic_lines(trip()).values())


def test_default_demo_trip_fits_default_budget():
    b = planner.compute_budget(planner.fallback_plan(trip())["budget_lines"], trip())
    assert b["within_budget"] is True


# ------------------------------------------------------------------ Gemini mocked end-to-end
def test_live_ai_path_and_backend_owns_totals(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    raw = good_ai_plan()
    raw["budget_estimate"]["total"] = 1  # a bogus AI "total" must be ignored

    async def fake(trip, ctx=""):
        return raw

    monkeypatch.setattr(gemini, "generate_itinerary", fake)
    r = client.post("/api/plan", json=payload())
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["itinerary_source"] == "gemini" and body["meta"]["model"] == "gemini-2.5-flash"
    assert body["days"][0]["morning"]["title"] == "AI morning"
    assert body["days"][0]["est_day_cost"] == 1500
    b = body["budget"]
    assert b["estimated_total"] == sum(l["amount"] for l in b["lines"]) != 1
    assert b["difference"] == b["user_budget"] - b["estimated_total"]
    assert body["meta"]["verified"] is False


def test_ai_partial_response_is_filled_from_fallback(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    raw = good_ai_plan(days=1)  # only 1 of 3 days
    del raw["budget_estimate"]["food"]
    raw["days"][0]["afternoon"] = {"title": ""}  # unusable slot

    async def fake(trip, ctx=""):
        return raw

    monkeypatch.setattr(gemini, "generate_itinerary", fake)
    body = client.post("/api/plan", json=payload()).json()
    assert len(body["days"]) == 3 and all(d["morning"]["title"] and d["afternoon"]["title"] for d in body["days"])
    assert body["meta"]["itinerary_source"] == "gemini"
    assert any("missing" in w for w in body["meta"]["warnings"])
    assert next(l for l in body["budget"]["lines"] if l["key"] == "food")["amount"] > 0


@pytest.mark.parametrize("bad", [{"days": []}, {"overview": "no days"}, {"days": "nope"}, ["not", "a", "dict"]])
def test_unusable_ai_payload_falls_back(client, monkeypatch, bad):
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    async def fake(trip, ctx=""):
        return bad

    monkeypatch.setattr(gemini, "generate_itinerary", fake)
    r = client.post("/api/plan", json=payload())
    assert r.status_code == 200 and r.json()["meta"]["itinerary_source"] == "fallback"
    assert len(r.json()["days"]) == 3


# ------------------------------------------------------------------ external failures
@pytest.mark.parametrize("kind", ["rate_limit", "timeout", "network", "malformed", "invalid_key"])
def test_gemini_failure_falls_back_with_warning(client, monkeypatch, kind):
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    async def boom(trip, ctx=""):
        raise GeminiError("simulated failure", kind)

    monkeypatch.setattr(gemini, "generate_itinerary", boom)
    r = client.post("/api/plan", json=payload())
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert meta["itinerary_source"] == "fallback" and meta["model"] is None
    assert any("simulated failure" in w for w in meta["warnings"])
    assert len(r.json()["days"]) == 3


def test_unexpected_gemini_exception_still_returns_plan(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    async def boom(trip, ctx=""):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(gemini, "generate_itinerary", boom)
    r = client.post("/api/plan", json=payload())
    assert r.status_code == 200 and r.json()["meta"]["itinerary_source"] == "fallback"


def test_research_failure_does_not_break_plan(client, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "s")

    async def boom(trip, client=None):
        raise ResearchError("timed out")

    monkeypatch.setattr(travel_research, "fetch_research", boom)
    r = client.post("/api/plan", json=payload())
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert meta["research_source"] == "unavailable" and r.json()["sources"] == []
    assert any("unavailable" in w for w in meta["warnings"])


def test_research_success_with_fallback_itinerary_is_labelled_honestly(client, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "s")
    seen = {}

    async def fake(trip, client=None):
        return {"sources": [{"title": "Goa guide", "url": "https://example.com/goa", "snippet": "s", "kind": "search"}], "context": "- Goa guide: s"}

    monkeypatch.setattr(travel_research, "fetch_research", fake)
    body = client.post("/api/plan", json=payload()).json()
    assert body["meta"]["research_source"] == "serpapi" and body["meta"]["itinerary_source"] == "fallback"
    assert body["sources"][0]["url"] == "https://example.com/goa"
    assert any("does not use them" in w for w in body["meta"]["warnings"])


def test_research_context_is_passed_to_gemini(client, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "s")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen = {}

    async def fake_research(trip, client=None):
        return {"sources": [{"title": "T", "url": "https://e.com", "snippet": "", "kind": "maps"}], "context": "- CONTEXT-MARKER"}

    async def fake_ai(trip, ctx=""):
        seen["ctx"] = ctx
        return good_ai_plan()

    monkeypatch.setattr(travel_research, "fetch_research", fake_research)
    monkeypatch.setattr(gemini, "generate_itinerary", fake_ai)
    body = client.post("/api/plan", json=payload()).json()
    assert "CONTEXT-MARKER" in seen["ctx"]
    assert body["meta"]["itinerary_source"] == "gemini" and body["meta"]["research_source"] == "serpapi"


def test_internal_error_returns_json_500(monkeypatch):
    async def boom(trip):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(app_module, "build_plan", boom)
    r = TestClient(app_module.app, raise_server_exceptions=False).post("/api/plan", json=payload())
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error" and "secret internals" not in r.text


# ------------------------------------------------------------------ service-level HTTP behaviour (mock transport)
def _gemini_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run_gemini(handler, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    return asyncio.run(gemini.generate_itinerary(trip(), "", client=_gemini_client(handler)))


def gemini_reply(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_gemini_parses_fenced_json(monkeypatch):
    out = run_gemini(lambda req: gemini_reply('```json\n{"days": [1]}\n```'), monkeypatch)
    assert out == {"days": [1]}


def test_gemini_sends_key_in_header_not_url(monkeypatch):
    seen = {}

    def handler(req):
        seen["url"], seen["key"] = str(req.url), req.headers.get("x-goog-api-key")
        return gemini_reply("{}")

    run_gemini(handler, monkeypatch)
    assert seen["key"] == "k" and "key=" not in seen["url"]


@pytest.mark.parametrize(
    "response, kind",
    [
        (httpx.Response(429, json={}), "rate_limit"),
        (httpx.Response(403, json={}), "permission"),
        (httpx.Response(500, json={}), "server"),
        (gemini_reply("this is not json"), "malformed"),
        (gemini_reply(""), "empty"),
        (httpx.Response(200, json={"candidates": []}), "empty"),
        (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), "blocked"),
        (httpx.Response(200, content=b"<html>"), "malformed"),
    ],
)
def test_gemini_error_kinds(monkeypatch, response, kind):
    with pytest.raises(GeminiError) as exc:
        run_gemini(lambda req: response, monkeypatch)
    assert exc.value.kind == kind


def test_gemini_timeout_and_network_errors(monkeypatch):
    def timeout(req):
        raise httpx.ReadTimeout("slow")

    def netfail(req):
        raise httpx.ConnectError("down")

    with pytest.raises(GeminiError) as e1:
        run_gemini(timeout, monkeypatch)
    with pytest.raises(GeminiError) as e2:
        run_gemini(netfail, monkeypatch)
    assert e1.value.kind == "timeout" and e2.value.kind == "network"


def test_gemini_requires_key():
    with pytest.raises(GeminiError):
        asyncio.run(gemini.generate_itinerary(trip(), ""))


def test_prompt_contains_requirements_and_guardrails():
    p = gemini.build_prompt(trip(notes="vegetarian"), "- some research")
    assert "Pune" in p and "Goa" in p and "vegetarian" in p and "some research" in p
    assert "Never claim live availability" in p


SERP_GOOGLE = {"organic_results": [{"title": "Goa guide", "link": "https://example.com/goa", "snippet": "Beaches"}]}
SERP_MAPS = {"local_results": [{"title": "Fort Aguada", "type": "Fort", "rating": 4.5, "reviews": 100, "address": "Sinquerim", "place_id": "ABC"}]}


def run_research(handler, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "s")
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return asyncio.run(travel_research.fetch_research(trip(), client=c))


def test_research_parses_and_limits_requests(monkeypatch):
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        return httpx.Response(200, json=SERP_GOOGLE if req.url.params["engine"] == "google" else SERP_MAPS)

    out = run_research(handler, monkeypatch)
    assert len(calls) == 3
    kinds = {s["kind"] for s in out["sources"]}
    assert kinds == {"search", "maps"}
    assert any("place_id:ABC" in s["url"] for s in out["sources"])
    assert "Goa guide" in out["context"]


def test_research_tolerates_partial_failure(monkeypatch):
    def handler(req):
        if req.url.params["engine"] == "google":
            return httpx.Response(500)
        return httpx.Response(200, json=SERP_MAPS)

    assert run_research(handler, monkeypatch)["sources"]


def test_research_all_failures_raise(monkeypatch):
    with pytest.raises(ResearchError):
        run_research(lambda req: httpx.Response(500), monkeypatch)

    def timeout(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ResearchError):
        run_research(timeout, monkeypatch)


def test_research_requires_key():
    with pytest.raises(ResearchError):
        asyncio.run(travel_research.fetch_research(trip()))


def test_nightlife_only_scheduled_in_the_evening():
    plan = planner.fallback_plan(trip(duration_days=5, interests=["nightlife", "food"]))
    nightlife_titles = {e[0] for e in planner.GOA_POOL["nightlife"]}
    for d in plan["days"]:
        assert d["morning"]["title"] not in nightlife_titles
        assert d["afternoon"]["title"] not in nightlife_titles


def test_nightlife_only_interest_still_fills_every_slot():
    plan = planner.fallback_plan(trip(duration_days=4, interests=["nightlife"]))
    assert all(d["morning"]["title"] and d["afternoon"]["title"] and d["evening"]["title"] for d in plan["days"])


def test_validation_numbers_are_formatted_cleanly(client):
    msg = client.post("/api/plan", json=payload(budget=0)).json()["error"]["message"]
    assert "greater than 0." in msg and "0.0" not in msg


def test_requested_nightlife_appears_in_evenings():
    plan = planner.fallback_plan(trip(duration_days=5, interests=["food", "nightlife"]))
    titles = {e[0] for e in planner.GOA_POOL["nightlife"]}
    assert any(d["evening"]["title"] in titles for d in plan["days"])


# ====================================================================== Gemini diagnostics
SECRET = "AIzaSyFAKE_secret_key_1234567890abcdef"


def google_error(status, google_status, message):
    return httpx.Response(status, json={"error": {"code": status, "message": message, "status": google_status}})


@pytest.fixture
def secret_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)


def call_gemini(handler):
    return asyncio.run(gemini.generate_itinerary(trip(), "", client=_gemini_client(handler)))


# ---- 1. missing key vs failed request vs invalid response are distinct
def test_missing_key_is_not_configured_and_logged(caplog):
    caplog.set_level(logging.INFO, logger="travel_genie")
    with pytest.raises(GeminiError) as exc:
        asyncio.run(gemini.generate_itinerary(trip(), ""))
    assert exc.value.kind == "not_configured" and exc.value.category == "not_configured"
    assert "GEMINI_API_KEY is not set" in str(exc.value)
    assert "kind=not_configured" in caplog.text


def test_missing_key_plan_reports_not_configured_and_logs_demo_mode(client, caplog):
    caplog.set_level(logging.INFO, logger="travel_genie")
    meta = client.post("/api/plan", json=payload()).json()["meta"]
    assert meta["ai_status"] == "not_configured" and meta["itinerary_source"] == "fallback"
    assert "ITINERARY_SOURCE=fallback (DEMO MODE) reason=not_configured" in caplog.text


# ---- 2. non-200 responses: status + redacted Google message logged, key never leaked
@pytest.mark.parametrize(
    "status, google_status, message, kind",
    [
        (400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.", "invalid_key"),
        (401, "UNAUTHENTICATED", "Request had invalid authentication credentials.", "invalid_key"),
        (403, "PERMISSION_DENIED", "Generative Language API has not been used in project 123 before or it is disabled.", "permission"),
        (404, "NOT_FOUND", "models/gemini-9-ultra is not found for API version v1beta", "model_not_found"),
        (429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.", "rate_limit"),
        (500, "INTERNAL", "An internal error has occurred.", "server"),
        (503, "UNAVAILABLE", "The model is overloaded.", "server"),
        (400, "INVALID_ARGUMENT", "Unsupported generation config.", "bad_request"),
    ],
)
def test_non_200_logs_status_and_google_message(secret_key, caplog, status, google_status, message, kind):
    caplog.set_level(logging.INFO, logger="travel_genie")
    with pytest.raises(GeminiError) as exc:
        call_gemini(lambda req: google_error(status, google_status, message))
    e = exc.value
    assert e.kind == kind and e.category == "request_failed" and e.status_code == status
    assert f"HTTP {status}" in str(e)
    assert f"http_status={status}" in caplog.text and f"kind={kind}" in caplog.text
    assert message[:25] in caplog.text  # Google's message is logged (redacted), not swallowed


def test_unsupported_model_message_names_the_model(secret_key, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-9-ultra")
    with pytest.raises(GeminiError) as exc:
        call_gemini(lambda req: google_error(404, "NOT_FOUND", "models/gemini-9-ultra is not found"))
    assert "gemini-9-ultra" in str(exc.value) and "GEMINI_MODEL" in str(exc.value)


def test_secret_never_appears_in_logs_errors_or_responses(secret_key, caplog, client, monkeypatch):
    caplog.set_level(logging.DEBUG, logger="travel_genie")
    leaky = f"API key not valid: {SECRET}. See https://x.test/?key={SECRET}&a=1"
    with pytest.raises(GeminiError) as exc:
        call_gemini(lambda req: google_error(400, "INVALID_ARGUMENT", leaky))
    assert SECRET not in str(exc.value) and SECRET not in exc.value.detail and SECRET not in caplog.text
    assert "[REDACTED]" in caplog.text

    # same leaky failure end to end: nothing in the API response or logs
    async def leaky_call(trip, ctx=""):
        raise gemini._fail("invalid_key", f"bad key {SECRET}", 400, leaky)

    monkeypatch.setattr(gemini, "generate_itinerary", leaky_call)
    r = client.post("/api/plan", json=payload())
    assert SECRET not in r.text and SECRET not in caplog.text


def test_redact_helper(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "customkey-not-aiza-format")
    out = gemini.redact("x customkey-not-aiza-format y AIzaSyABCDEFGHIJKLMNOPQRSTUV z ?key=abc123&q=1 api_key=zzz")
    assert "customkey" not in out and "AIzaSy" not in out and "abc123" not in out and "zzz" not in out
    assert len(gemini.redact("a" * 1000)) <= 300


def test_non_json_error_body_does_not_crash(secret_key):
    with pytest.raises(GeminiError) as exc:
        call_gemini(lambda req: httpx.Response(502, content=b"<html>Bad gateway</html>"))
    assert exc.value.kind == "server" and exc.value.status_code == 502


def test_failed_request_plan_surfaces_explicit_fallback(client, secret_key, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="travel_genie")

    async def real_call(trip, ctx=""):
        return await _orig_generate(trip, ctx, client=_gemini_client(lambda req: google_error(429, "RESOURCE_EXHAUSTED", "quota")))

    monkeypatch.setattr(gemini, "generate_itinerary", real_call)
    body = client.post("/api/plan", json=payload()).json()
    meta = body["meta"]
    assert meta["itinerary_source"] == "fallback" and meta["model"] is None
    assert meta["ai_status"] == "request_failed" and "HTTP 429" in meta["ai_error"]
    assert any("Gemini was not used" in w for w in meta["warnings"])
    assert "ITINERARY_SOURCE=fallback (DEMO MODE) reason=request_failed" in caplog.text
    assert len(body["days"]) == 3


_orig_generate = gemini.generate_itinerary


# ---- 3. timeouts and network errors
def test_timeout_is_logged_and_classified(secret_key, caplog):
    caplog.set_level(logging.INFO, logger="travel_genie")

    def timeout(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(GeminiError) as exc:
        call_gemini(timeout)
    assert exc.value.kind == "timeout" and exc.value.category == "request_failed"
    assert "kind=timeout" in caplog.text and "ReadTimeout" in caplog.text


def test_network_error_is_logged_and_classified(secret_key, caplog):
    caplog.set_level(logging.INFO, logger="travel_genie")

    def down(req):
        raise httpx.ConnectError("name resolution failed")

    with pytest.raises(GeminiError) as exc:
        call_gemini(down)
    assert exc.value.kind == "network" and "kind=network" in caplog.text


# ---- 4. invalid model responses (HTTP 200 but unusable)
def test_truncated_answer_is_reported_as_truncated(secret_key, caplog):
    caplog.set_level(logging.INFO, logger="travel_genie")
    resp = httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"days": [{"day": 1,'}]}}]})
    with pytest.raises(GeminiError) as exc:
        call_gemini(lambda req: resp)
    assert exc.value.kind == "truncated" and exc.value.category == "invalid_response"
    assert "MAX_TOKENS" in caplog.text


def test_malformed_json_is_invalid_response_and_plan_says_so(client, secret_key, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="travel_genie")

    async def real_call(trip, ctx=""):
        return await _orig_generate(trip, ctx, client=_gemini_client(lambda req: gemini_reply("Sure! Here is your trip: lots of prose")))

    monkeypatch.setattr(gemini, "generate_itinerary", real_call)
    meta = client.post("/api/plan", json=payload()).json()["meta"]
    assert meta["ai_status"] == "invalid_response" and meta["itinerary_source"] == "fallback"
    assert "reason=invalid_response" in caplog.text


def test_valid_json_with_wrong_shape_is_invalid_response(client, secret_key, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="travel_genie")

    async def fake(trip, ctx=""):
        return {"days": "not a list"}

    monkeypatch.setattr(gemini, "generate_itinerary", fake)
    meta = client.post("/api/plan", json=payload()).json()["meta"]
    assert meta["ai_status"] == "invalid_response" and meta["ai_error"]
    assert "Gemini response could not be used" in caplog.text


# ---- 5. success path
def test_successful_generation_is_logged_and_marked_ok(client, secret_key, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="travel_genie")

    async def real_call(trip, ctx=""):
        return await _orig_generate(trip, ctx, client=_gemini_client(lambda req: gemini_reply(json.dumps(good_ai_plan()))))

    monkeypatch.setattr(gemini, "generate_itinerary", real_call)
    body = client.post("/api/plan", json=payload()).json()
    meta = body["meta"]
    assert meta["itinerary_source"] == "gemini" and meta["ai_status"] == "ok" and meta["ai_error"] is None
    assert body["days"][0]["morning"]["title"] == "AI morning"
    assert "Gemini request succeeded" in caplog.text and "http_status=200" in caplog.text
    assert "ITINERARY_SOURCE=gemini" in caplog.text and "DEMO MODE" not in caplog.text
    assert SECRET not in caplog.text and SECRET not in json.dumps(body)


def test_request_uses_env_model_and_disables_thinking_only_for_25_flash(secret_key, monkeypatch):
    seen = []

    def handler(req):
        seen.append((str(req.url), json.loads(req.content)["generationConfig"]))
        return gemini_reply("{}")

    call_gemini(handler)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")
    call_gemini(handler)
    assert "gemini-2.5-flash:generateContent" in seen[0][0] and seen[0][1]["thinkingConfig"] == {"thinkingBudget": 0}
    assert "gemini-2.0-flash:generateContent" in seen[1][0] and "thinkingConfig" not in seen[1][1]


# ---- 6. startup diagnostics
def test_startup_logs_config_status_without_secrets(secret_key, caplog):
    caplog.set_level(logging.INFO, logger="travel_genie")
    with TestClient(app_module.app):
        pass
    assert "GEMINI_API_KEY_set=True" in caplog.text and SECRET not in caplog.text


def test_startup_warns_when_key_missing(caplog):
    caplog.set_level(logging.INFO, logger="travel_genie")
    with TestClient(app_module.app):
        pass
    assert "GEMINI_API_KEY_set=False" in caplog.text and "DEMO MODE" in caplog.text
