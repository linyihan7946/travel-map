from travel_map import web
from travel_map.web import app


def test_home_page_uses_full_screen_map_with_floating_controls():
    response = app.test_client().get("/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="map-frame"' in page
    assert 'id="search-panel"' in page
    assert 'id="action-dock"' in page
    assert 'id="go"' in page
    assert "frame.srcdoc = initialMap" in page
    assert "leaflet" in page.lower()


def test_app_works_behind_deployment_gateway_prefix(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_PATH", "/travel-map")
    client = app.test_client()
    headers = {"X-Forwarded-Prefix": "/travel-map"}

    response = client.get("/travel-map/", headers=headers)
    health = client.get("/travel-map/healthz", headers=headers)

    assert response.status_code == 200
    assert 'const generateUrl = "/travel-map/api/generate"' in response.get_data(as_text=True)
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}


def test_generated_region_file_is_removed(monkeypatch):
    captured = {}

    class DummyRenderer:
        def __init__(self, config):
            captured["path"] = config.regions[0]

        def render_interactive(self):
            import os

            assert os.path.exists(captured["path"])
            return "<html></html>"

    monkeypatch.setitem(web.STYLES, "pins", DummyRenderer)
    region = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[104, 30], [105, 30], [105, 31], [104, 30]]],
                },
            }
        ],
    }

    html, _ = web._build_map(
        region,
        {"name": "Test Region"},
        [{"name": "Attraction", "lat": 30.5, "lon": 104.5}],
    )

    import os

    assert html == "<html></html>"
    assert not os.path.exists(captured["path"])


def test_generated_map_is_a_complete_full_page_document():
    from travel_map.config import Location, TravelConfig
    from travel_map.styles.pins import PinsRenderer

    renderer = PinsRenderer(
        TravelConfig(
            title="Region map",
            locations=[Location(name="Attraction", lat=30.5, lon=104.5)],
        )
    )

    html = renderer.render_interactive()

    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "height: 100.0%" in html or "height:100.0%" in html
