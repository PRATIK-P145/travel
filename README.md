# Travel-Genie

An AI travel planner prototype. Enter your trip details and get a day-by-day itinerary, an estimated budget and research links. It runs with **no API keys** in demo mode, and upgrades to live AI and live research when you add keys.

## Features

- Planning form: route, dates (return date or duration), travelers, budget, currency, travel style, interests, accommodation preference and special requests.
- Day-by-day itinerary with morning, afternoon and evening plans, meal ideas, transport, areas to stay, tips and caveats.
- Budget breakdown (transport, accommodation, food, activities, miscellaneous). The **backend** calculates the total, the difference from your budget, and adjustment suggestions when you're over.
- Optional live research through SerpApi (Google Search and Google Maps), shown as source links.
- Gemini 3.8 Flash is the primary itinerary generator; Groq is the optional backup when Gemini fails or returns unusable output.
- Deterministic fallback planner when no AI provider succeeds, with clear on-screen labels for AI, live research and demo data.
- Nothing is booked, no database, no accounts.

## Architecture

```
Browser (static/index.html, styles.css, app.js)
   |  POST /api/plan, GET /api/health
FastAPI (app.py)  ->  services/planner.py   (workflow, budget math, fallback planner)
                         |-- services/travel_research.py  (SerpApi, optional, max 3 requests)
                         |-- services/gemini.py           (primary Gemini REST API)
                         |-- services/groq.py             (backup Groq REST API)
models.py (request validation and response schema), settings.py (.env)
```

Workflow: validate request, research (if configured), try Gemini, then Groq if needed, then fallback, normalize AI output, compute budget, return result.

Missing or malformed parts of an AI response are filled from the fallback plan, and a warning says so. The AI's own totals are ignored; the budget total is always the sum of the category lines.

## Prerequisites

- Python 3.10 or newer
- Optional: a [Gemini API key](https://aistudio.google.com/apikey) and a [SerpApi key](https://serpapi.com/)

## Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

## Environment configuration

Edit `.env`. Both keys are optional.

| Variable | Purpose | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Enables AI itinerary generation | empty (demo itinerary) |
| `GEMINI_MODEL` | Gemini model name | `gemini-3.8-flash` |
| `GEMINI_TIMEOUT_SECONDS` | Per-request timeout in seconds (10–120; invalid values use the default) | `35` |
| `GEMINI_MAX_OUTPUT_TOKENS` | Maximum generated output tokens (1024–8192; invalid values use the default) | `8192` |
| `GROQ_API_KEY` | Enables Groq backup if Gemini fails | empty (backup disabled) |
| `GROQ_MODEL` | Groq model name | `llama-3.3-70b-versatile` |
| `GROQ_TIMEOUT_SECONDS` | Per-request timeout in seconds (10–120) | `35` |
| `GROQ_MAX_OUTPUT_TOKENS` | Maximum generated output tokens (1024–16384) | `8192` |
| `SERPAPI_API_KEY` | Enables live Google Search and Maps research | empty (no research) |

Keys are never returned by the API. `.env` is git-ignored. Gemini 3.8 Flash remains primary; if it fails, the planner tries Groq when `GROQ_API_KEY` is set. Create a key at [Groq Console](https://console.groq.com/keys) and add it to your local `.env`. HTTP 503 or a timeout is not proof that a key is invalid.

## Run locally

```bash
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>. The header pill shows whether the app is in demo or live mode.

## Run tests

```bash
python -m pytest
```

All external services are mocked, so tests need no keys, network or paid access.

## API examples

Health (reports which integrations are configured, never the keys):

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","mode":"demo","integrations":{"gemini":{"configured":false,"model":"gemini-3.8-flash"},"serpapi":{"configured":false}}}
```

Plan a trip. Provide either `return_date` or `duration_days` (1 to 14 days):

```bash
curl -X POST http://127.0.0.1:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{
    "start_city": "Pune", "destination": "Goa",
    "departure_date": "2026-11-20", "duration_days": 3,
    "travelers": 2, "budget": 25000, "currency": "INR",
    "travel_style": "balanced", "interests": ["food", "beaches"]
  }'
```

The response contains `trip`, `overview`, `days`, `accommodation`, `budget`, `tips`, `caveats`, `sources` and `meta`. `meta.itinerary_source` is `gemini`, `groq` or `fallback`; `meta.research_source` is `serpapi`, `unavailable` or `not_configured`; and `meta.warnings` explains provider fallbacks.

Errors share one shape. Validation failures return HTTP 422:

```json
{"error": {"code": "validation_error", "message": "Please fix the highlighted details: ...", "details": [{"field": "budget", "message": "Total budget must be greater than 0."}]}}
```

Supported currencies: INR, USD, EUR, GBP, AED, SGD, THB.

## Demo-mode behavior

| Situation | Result |
| --- | --- |
| Neither `GEMINI_API_KEY` nor `GROQ_API_KEY` | Template itinerary, labelled "demo template, not AI" |
| Gemini error, rate limit, timeout or bad JSON with Groq key set | Try Groq and use its itinerary if valid |
| All configured AI providers fail | Template itinerary plus a warning explaining why |
| No `SERPAPI_API_KEY` | No research links, labelled "Research: not used" |
| SerpApi failure | Plan continues, research marked unavailable |
| Only SerpApi works | Live links are shown, with a note that the template itinerary does not use them |

## Current limitations

- All prices are estimates. Rule-of-thumb rates are fixed in INR and converted to other currencies with rough static rates, not live exchange rates.
- The demo planner has curated suggestions for Goa only. Other destinations use generic templates.
- Nothing is verified live: opening hours, availability and prices must be checked with providers.
- No flight, hotel or activity search, bookings, payments, accounts or saved trips.
- No distance or travel-time calculation. Transport cost for the main journey is a flat estimate per traveler.
- Trips are limited to 14 days.
- AI providers and SerpApi are called once per request; there is no caching or automatic retry. Groq is a fallback provider, not a replacement for SerpApi research.
