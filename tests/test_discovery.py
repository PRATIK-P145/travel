import asyncio

import httpx
from fastapi.testclient import TestClient

import app as app_module
import services.discovery as discovery


def test_normalize_result_handles_optional_fields():
    item = {
        "title": "Amber Fort", "type": "Historical place", "rating": 4.7,
        "reviews": 1200, "address": "Devisinghpura, Jaipur", "place_id": "abc123",
        "thumbnail": "https://example.com/amber.jpg", "website": "https://example.com/fort",
    }
    result = discovery.normalize_result(item, "forts", "Jaipur")
    assert result["title"] == "Amber Fort"
    assert result["rating"] == 4.7 and result["reviews"] == 1200
    assert result["maps_url"].endswith("abc123")
    assert result["website"] == "https://example.com/fort"


def test_normalize_result_rejects_unsafe_links():
    result = discovery.normalize_result({"title": "Example", "website": "javascript:alert(1)", "thumbnail": "data:text/html,x"}, "places", "Delhi")
    assert result["website"] == ""
    assert result["thumbnail"] == ""
    assert result["maps_url"].startswith("https://www.google.com/maps/")


def test_search_places_uses_india_google_maps_params(monkeypatch):
    monkeypatch.setattr(discovery.settings, "serpapi_key", lambda: "test-key")
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"local_results": [
            {"title": "Hawa Mahal", "type": "Tourist attraction", "rating": 4.5, "place_id": "xyz"},
            {"type": "Missing title"},
        ]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(discovery.search_places("  Jaipur ", "tourist attractions", client=client))
    assert result["count"] == 1
    assert result["destination"] == "Jaipur"
    assert seen["params"]["engine"] == "google_maps"
    assert seen["params"]["gl"] == "in"
    assert seen["params"]["q"] == "tourist attractions in Jaipur, India"
    asyncio.run(client.aclose())


def test_search_places_requires_serpapi_key(monkeypatch):
    monkeypatch.setattr(discovery.settings, "serpapi_key", lambda: "")
    try:
        asyncio.run(discovery.search_places("Delhi", "hotels"))
    except discovery.DiscoveryError as exc:
        assert "SERPAPI_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected DiscoveryError")


def test_discover_api_returns_results_and_validates_destination(monkeypatch):
    async def fake_search(destination, category):
        return {"destination": destination, "category": category, "query": "places", "count": 1, "results": [{"title": "Hawa Mahal"}]}

    monkeypatch.setattr(app_module, "search_places", fake_search)
    client = TestClient(app_module.app)
    ok = client.get("/api/discover", params={"destination": "Jaipur", "category": "tourist attractions"})
    assert ok.status_code == 200 and ok.json()["results"][0]["title"] == "Hawa Mahal"
    bad = client.get("/api/discover")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_destination"


def test_discover_page_route_serves_html():
    response = TestClient(app_module.app).get("/discover")
    assert response.status_code == 200
    assert "Explore places" in response.text
