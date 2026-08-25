"""Administrative region resolution (China) and geocoding fallback.

Region resolution uses Alibaba DataV's free areas_v3 API with lazy drill-down
(no key required). Attraction geocoding falls back to OSM Nominatim when the
LLM path is unavailable or returns nothing.
"""

import json
import time
import urllib.parse
import urllib.request

DATAV_BASE = "https://geo.datav.aliyun.com/areas_v3/bound"
NOMINATIM_BASE = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "travel-map/0.1.0 (https://github.com/linyihan7946/travel-map)"

# Administrative suffixes, stripped longest-first to normalize region names.
_SUFFIXES = [
    "维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区",
    "自治区", "自治州", "自治县", "自治旗", "民族乡",
    "地区", "街道", "苏木",
    "省", "市", "县", "区", "旗", "乡", "镇", "盟",
]
_SUFFIXES.sort(key=len, reverse=True)

_CACHE = {}  # url -> parsed JSON (session-level, avoids repeated downloads)
_last_nominatim = 0.0  # monotonic time of last Nominatim request


class RegionNotFound(ValueError):
    """Raised when a region name cannot be resolved to an adcode."""


def _fetch(url: str) -> dict:
    """Fetch and cache JSON from a URL."""
    if url in _CACHE:
        return _CACHE[url]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    _CACHE[url] = data
    return data


def _children(adcode: int) -> list:
    """Return the direct children (features) of an adcode."""
    return _fetch(f"{DATAV_BASE}/{adcode}_full.json").get("features", [])


def _boundary(adcode: int) -> dict:
    """Return the GeoJSON FeatureCollection boundary for an adcode."""
    return _fetch(f"{DATAV_BASE}/geojson?code={adcode}")


def _core(name: str) -> str:
    """Normalize a region name by stripping administrative suffixes."""
    s = (name or "").strip()
    if not s:
        return ""
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if s.endswith(suffix) and len(s) > len(suffix):
                s = s[: -len(suffix)]
                changed = True
                break
    return s


def _match(features: list, target: str):
    """Return the feature whose name best matches ``target`` (normalized)."""
    if not target:
        return None
    best = None

    def key(f):
        return len(_core(f["properties"].get("name")))

    for f in features:
        core = _core(f["properties"].get("name"))
        if core == target:
            return f
    # Fall back to prefix/substring matching (shorter core preferred).
    for f in features:
        core = _core(f["properties"].get("name"))
        if not core or len(target) < 2:
            continue
        if core.startswith(target) or target.startswith(core) or target in core or core in target:
            if best is None or key(f) < key(best):
                best = f
    return best


def resolve_region(name: str, ancestors: list[str] | None = None) -> dict:
    """Resolve a region name to ``{adcode, level, name, geojson}``.

    ``ancestors`` is an optional chain (highest-first) of parent regions,
    e.g. ``["云南省"]`` for "昆明市". When provided, resolution descends the
    tree following the chain, which is fast and unambiguous. Without it, the
    function matches the province level and then scans province children
    (cities); deeper levels require a more specific name or ancestor hint.
    """
    target = _core(name)
    if not target:
        raise RegionNotFound(name)

    provinces = _children(100000)

    if ancestors:
        current = provinces
        for anc in ancestors:
            f = _match(current, _core(anc))
            if f is None:
                raise RegionNotFound(f"{anc}（在上级地区中未找到）")
            current = _children(f["properties"]["adcode"])
        matched = _match(current, target)
    else:
        matched = _match(provinces, target)
        if matched is None:
            # Scan every province's cities (cached) for a city-level match.
            for p in provinces:
                m = _match(_children(p["properties"]["adcode"]), target)
                if m is not None:
                    matched = m
                    break

    if matched is None:
        raise RegionNotFound(
            f"未找到地区「{name}」。请使用更完整的名称（如“云南省”“昆明市”），"
            "或开启大模型后输入简称。"
        )

    props = matched["properties"]
    return {
        "adcode": props["adcode"],
        "level": props.get("level"),
        "name": props.get("name"),
        "geojson": _boundary(props["adcode"]),
    }


def nominatim_geocode(name: str, region_name: str | None = None) -> tuple[float, float] | None:
    """Geocode an attraction name via OSM Nominatim (free, no key).

    Enforces Nominatim's ~1 req/s usage policy. Returns ``(lat, lon)`` or None.
    """
    global _last_nominatim
    query = f"{name}, {region_name}" if region_name else name
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
    url = f"{NOMINATIM_BASE}?{params}"

    # Rate limit: leave >=1.1s between requests.
    wait = 1.1 - (time.monotonic() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            results = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    finally:
        _last_nominatim = time.monotonic()

    if results:
        return float(results[0]["lat"]), float(results[0]["lon"])
    return None
