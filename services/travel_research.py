"""Optional live travel research through SerpApi (Google Search and Google Maps).

Uses only the official SerpApi API. No scraping. At most three requests per plan.
"""
import asyncio
from typing import Optional
from urllib.parse import quote_plus

import httpx

import settings
from models import TripRequest

SERPAPI_URL = "https://serpapi.com/search.json"
TIMEOUT_SECONDS = 15.0
MAX_SOURCES = 10
MAX_CONTEXT_CHARS = 3000


class ResearchError(Exception):
    """Live research failed entirely."""


def _maps_link(title: str, place_id: str, destination: str) -> str:
    if place_id:
        return f"https://www.google.com/maps/place/?q=place_id:{quote_plus(place_id)}"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(title + ' ' + destination)}"


def parse_results(data: dict, engine: str, destination: str) -> list[dict]:
    """Turn a SerpApi response into a flat list of source dicts."""
    out: list[dict] = []
    if engine == "google":
        for r in (data.get("organic_results") or [])[:4]:
            if r.get("title") and r.get("link"):
                out.append(
                    {"title": r["title"], "url": r["link"], "snippet": r.get("snippet", ""), "kind": "search"}
                )
    else:
        for r in (data.get("local_results") or [])[:5]:
            title = r.get("title")
            if not title:
                continue
            bits = [r.get("type", "")]
            if r.get("rating"):
                bits.append(f"rated {r['rating']}" + (f" ({r['reviews']} reviews)" if r.get("reviews") else ""))
            if r.get("address"):
                bits.append(r["address"])
            out.append(
                {
                    "title": title,
                    "url": _maps_link(title, r.get("place_id", ""), destination),
                    "snippet": ", ".join(b for b in bits if b),
                    "kind": "maps",
                }
            )
    return out


async def _search(client: httpx.AsyncClient, params: dict, engine: str, destination: str) -> list[dict]:
    resp = await client.get(SERPAPI_URL, params=params)
    if resp.status_code >= 400:
        raise ResearchError(f"SerpApi returned HTTP {resp.status_code}.")
    data = resp.json()
    if isinstance(data, dict) and data.get("error") and not data.get("organic_results") and not data.get("local_results"):
        raise ResearchError("SerpApi reported an error for this query.")
    return parse_results(data, engine, destination)


async def fetch_research(trip: TripRequest, client: Optional[httpx.AsyncClient] = None) -> dict:
    """Return {"sources": [...], "context": "..."}.

    Raises ResearchError when every query fails. Partial failures are tolerated.
    """
    key = settings.serpapi_key()
    if not key:
        raise ResearchError("SERPAPI_API_KEY is not configured.")

    dest = trip.destination
    # Swap the third query for nightlife when that is the user's focus; otherwise restaurants.
    third = f"best nightlife in {dest}" if "nightlife" in trip.interests and "food" not in trip.interests else f"best local restaurants in {dest}"
    queries = [
        ("google", {"engine": "google", "q": f"things to do in {dest} travel guide", "num": 5}),
        ("google_maps", {"engine": "google_maps", "q": f"top tourist attractions in {dest}", "type": "search"}),
        ("google_maps", {"engine": "google_maps", "q": third, "type": "search"}),
    ]

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        results = await asyncio.gather(
            *[_search(client, {**p, "api_key": key}, engine, dest) for engine, p in queries],
            return_exceptions=True,
        )
    finally:
        if own_client:
            await client.aclose()

    sources: list[dict] = []
    seen: set[str] = set()
    failures = 0
    for r in results:
        if isinstance(r, BaseException):
            failures += 1
            continue
        for s in r:
            if s["url"] not in seen:
                seen.add(s["url"])
                sources.append(s)
    if failures == len(results):
        raise ResearchError("All SerpApi requests failed or timed out.")

    sources = sources[:MAX_SOURCES]
    lines = [f"- {s['title']}: {s['snippet']}".strip() for s in sources]
    return {"sources": sources, "context": "\n".join(lines)[:MAX_CONTEXT_CHARS]}
