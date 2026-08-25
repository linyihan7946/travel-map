from travel_map import geo


def _feature(name, adcode, level="province"):
    return {
        "type": "Feature",
        "properties": {"name": name, "adcode": adcode, "level": level},
        "geometry": None,
    }


def test_city_fallback_skips_province_with_missing_datav_children(monkeypatch):
    provinces = [
        _feature("Unavailable Province", 100001),
        _feature("四川省", 510000),
    ]
    city = _feature("成都市", 510100, "city")

    def fake_children(adcode):
        if adcode == 100000:
            return provinces
        if adcode == 100001:
            raise OSError("HTTP 404")
        if adcode == 510000:
            return [city]
        raise AssertionError(adcode)

    monkeypatch.setattr(geo, "_children", fake_children)
    monkeypatch.setattr(
        geo,
        "_boundary",
        lambda adcode: {"type": "FeatureCollection", "features": []},
    )

    result = geo.resolve_region("成都市")

    assert result["adcode"] == 510100
    assert result["name"] == "成都市"
