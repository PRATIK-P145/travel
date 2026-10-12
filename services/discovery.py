"""Search real-world places through SerpApi Google Maps for the discovery page."""
from urllib.parse import quote_plus, urlparse

import httpx

import settings

SERPAPI_URL = "https://serpapi.com/search.json"
TIMEOUT_SECONDS = 15.0
MAX_RESULTS = 18


class DiscoveryError(Exception):
    """SerpApi discovery could not return usable results."""


def _safe_http_url(value: str) -> str:
    """Only expose ordinary HTTP(S) links returned by the provider."""
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value)
    return value if parsed.scheme in ("http", "https") and parsed.netloc else ""


def _maps_url(item: dict, query: str, destination: str) -> str:
    place_id = item.get("place_id")
    if isinstance(place_id, str) and place_id:
        return "https://www.google.com/maps/place/?q=place_id:" + quote_plus(place_id)
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(
        str(item.get("title") or query) + " " + destination + " India"
    )


def normalize_result(item: dict, query: str, destination: str) -> dict | None:
    """Normalize a Google Maps local result without assuming optional fields exist."""
    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        return None
    website = _safe_http_url(item.get("website", ""))
    thumbnail = _safe_http_url(item.get("thumbnail", ""))
    links = item.get("links") if isinstance(item.get("links"), dict) else {}
    provider_link = _safe_http_url(links.get("website", ""))
    return {
        "title": title[:240],
        "category": str(item.get("type") or item.get("category") or "Place")[:100],
        "address": str(item.get("address") or "").strip()[:400],
        "rating": item.get("rating") if isinstance(item.get("rating"), (int, float)) else None,
        "reviews": item.get("reviews") if isinstance(item.get("reviews"), int) else None,
        "price": str(item.get("price") or "").strip()[:80],
        "description": str(item.get("description") or item.get("snippet") or "").strip()[:500],
        "hours": str(item.get("hours") or "").strip()[:160],
        "thumbnail": thumbnail,
        "website": website or provider_link,
        "maps_url": _maps_url(item, query, destination),
    }


async def search_places(destination: str, category: str, client: httpx.AsyncClient | None = None) -> dict:
    """Search Google Maps in any Indian destination and return UI-ready cards."""
    key = settings.serpapi_key()
    if not key:
        raise DiscoveryError("SERPAPI_API_KEY is not configured. Add it to .env and restart the server.")

    destination = " ".join(destination.split())[:100]
    category = " ".join(category.split())[:80]
    if not destination:
        raise DiscoveryError("Enter an Indian city or destination to search.")
    if not category:
        category = "tourist attractions"

    query = f"{category} in {destination}, India"
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        response = await client.get(SERPAPI_URL, params={
            "engine": "google_maps", "q": query, "type": "search", "hl": "en", "gl": "in", "api_key": key,
        })
        if response.status_code >= 400:
            raise DiscoveryError(f"SerpApi returned HTTP {response.status_code}. Check your key, quota, and plan.")
        data = response.json()
        if not isinstance(data, dict):
            raise DiscoveryError("SerpApi returned an unexpected response format.")
        if data.get("error") and not data.get("local_results") and not data.get("places_results"):
            raise DiscoveryError("SerpApi could not complete this search. Check your API quota and query.")
        raw_results = data.get("local_results") or data.get("places_results") or []
        cards = []
        if isinstance(raw_results, list):
            for item in raw_results:
                if isinstance(item, dict):
                    normalized = normalize_result(item, query, destination)
                    if normalized:
                        cards.append(normalized)
                if len(cards) >= MAX_RESULTS:
                    break
        return {"destination": destination, "category": category, "query": query, "results": cards, "count": len(cards)}
    except DiscoveryError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise DiscoveryError("Could not reach SerpApi or parse its response. Please try again.") from exc
    finally:
        if own_client:
            await client.aclose()
