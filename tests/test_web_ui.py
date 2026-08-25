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
