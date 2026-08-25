import json
from unittest.mock import Mock, patch

import pytest

from travel_map.config import Location, TravelConfig
from travel_map.styles.pins import (
    PinsRenderer,
    _DEFAULT_MAP_TILE_URL,
    _map_tile_url,
    _static_tile_cache_path,
)


@pytest.fixture
def region_file(tmp_path):
    path = tmp_path / "region.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [104.0, 30.0],
                                    [105.0, 30.0],
                                    [105.0, 31.0],
                                    [104.0, 31.0],
                                    [104.0, 30.0],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_region_map_uses_tight_bounds_for_preview_and_export(region_file):
    renderer = PinsRenderer(
        TravelConfig(
            title="Region map",
            locations=[Location(name="Attraction", lat=30.5, lon=104.5)],
            regions=[str(region_file)],
        )
    )

    assert renderer._get_bounds() == pytest.approx((28.0, 33.0, 102.0, 107.0))
    assert renderer._get_export_bounds() == pytest.approx((30.0, 31.0, 104.0, 105.0))

    html = renderer.render_interactive()
    assert "[[30.0, 104.0], [31.0, 105.0]]" in html


def test_map_without_region_keeps_location_padding():
    renderer = PinsRenderer(
        TravelConfig(
            title="Trip map",
            locations=[
                Location(name="A", lat=30.0, lon=104.0),
                Location(name="B", lat=31.0, lon=105.0),
            ],
        )
    )

    assert renderer._get_export_bounds() == pytest.approx((28.0, 33.0, 102.0, 107.0))

    html = renderer.render_interactive()
    assert "[[28.0, 102.0], [33.0, 107.0]]" in html


def test_static_label_uses_visual_offset_instead_of_geographic_offset():
    ax = Mock()
    location = Location(name="Attraction", lat=30.5, lon=104.5)

    PinsRenderer._add_static_label(ax, location, location.name)

    _, kwargs = ax.annotate.call_args
    assert kwargs["xy"] == (104.5, 30.5)
    assert kwargs["xytext"] == (0, 10)
    assert kwargs["textcoords"] == "offset points"


def test_static_region_map_renders_with_offset_label(region_file):
    renderer = PinsRenderer(
        TravelConfig(
            title="Region map",
            locations=[Location(name="Attraction", lat=30.5, lon=104.5)],
            regions=[str(region_file)],
        )
    )

    # Rendering is the compatibility check; skip downloading OSM tiles here.
    with patch("cartopy.mpl.geoaxes.GeoAxes.add_image"):
        image = renderer.render_static(width=400, height=300)

    assert image.width > 0
    assert image.height > 0


def test_preview_and_export_share_the_same_osm_tile_source(region_file, monkeypatch):
    monkeypatch.delenv("TRAVEL_MAP_TILE_URL", raising=False)
    monkeypatch.delenv("TRAVEL_MAP_STATIC_TILE_URL", raising=False)
    renderer = PinsRenderer(
        TravelConfig(
            title="Region map",
            locations=[Location(name="Attraction", lat=30.5, lon=104.5)],
            regions=[str(region_file)],
        )
    )

    html = renderer.render_interactive()

    assert _map_tile_url() == _DEFAULT_MAP_TILE_URL
    assert _DEFAULT_MAP_TILE_URL in html
    assert "openstreetmap.org/copyright" in html


def test_tile_cache_is_isolated_by_provider_url(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAVEL_MAP_TILE_CACHE_DIR", str(tmp_path))

    osm_cache = _static_tile_cache_path("https://tiles-a/{z}/{x}/{y}.png")
    terrain_cache = _static_tile_cache_path("https://tiles-b/{z}/{x}/{y}.png")

    assert osm_cache.parent == tmp_path
    assert terrain_cache.parent == tmp_path
    assert osm_cache != terrain_cache
