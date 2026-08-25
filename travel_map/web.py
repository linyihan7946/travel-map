"""Web UI: input a region + attraction list, get a highlighted map.

The pipeline reuses the existing ``TravelConfig`` + ``PinsRenderer`` (region
highlighting, permanent labels, and the in-page export button already work).
"""

import json
import os
import tempfile

import folium
from flask import Flask, jsonify, render_template, request

from . import geo, llm
from .config import Location, TravelConfig
from .styles import STYLES

app = Flask(__name__)


class DeploymentPrefixMiddleware:
    """Mount the Flask app below the path injected by the deployment gateway.

    ``one-click-deployment`` exposes projects at ``/<project-slug>/`` and
    provides both ``PUBLIC_BASE_PATH`` and ``X-Forwarded-Prefix``. Its gateway
    currently forwards the prefix in ``PATH_INFO``, so Flask needs to strip it
    before routing while retaining ``SCRIPT_NAME`` for URL generation.
    """

    def __init__(self, wrapped_app):
        self.wrapped_app = wrapped_app

    def __call__(self, environ, start_response):
        configured = os.environ.get("PUBLIC_BASE_PATH", "").strip()
        forwarded = environ.get("HTTP_X_FORWARDED_PREFIX", "").strip()
        raw_prefix = configured or forwarded
        prefix = "/" + raw_prefix.strip("/") if raw_prefix.strip("/") else ""

        if prefix:
            path = environ.get("PATH_INFO", "") or "/"
            if path == prefix:
                environ["PATH_INFO"] = "/"
                environ["SCRIPT_NAME"] = prefix
            elif path.startswith(prefix + "/"):
                environ["PATH_INFO"] = path[len(prefix):] or "/"
                environ["SCRIPT_NAME"] = prefix
            elif forwarded:
                # Also support gateways that already stripped the prefix.
                environ["SCRIPT_NAME"] = prefix

        return self.wrapped_app(environ, start_response)


app.wsgi_app = DeploymentPrefixMiddleware(app.wsgi_app)

_SPLITTERS = ("\n", "，", ",", "、", ";", "；")
_REGION_CHAIN_SPLITTERS = (",", "，", "、", "/", " ")


def _split_attractions(text: str) -> list[str]:
    """Split an attraction list on newlines/commas/ideographic commas."""
    parts = [text]
    for sep in _SPLITTERS:
        parts = [seg for p in parts for seg in p.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def _split_region_from_text(text: str) -> str:
    """Best-effort region extraction from a free-text blob (no-LLM fallback)."""
    for sep in ("：", ":"):
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return ""


def _parse_region_chain(text: str) -> tuple[str, list[str], str]:
    """Parse a region input like ``云南,昆明,五华区`` or ``云南 昆明 五华区``.

    Each segment (delimited by comma / slash / space) is treated as one level
    in the administrative hierarchy, from highest to lowest. The last segment
    is the target region; earlier segments become ``ancestors`` for DataV
    drill-down.

    When the input contains a ``：`` / ``:`` separator, everything after it is
    returned as the attraction blob.

    Returns ``(target_region, ancestors, attractions_after_colon)``.
    """
    if not text:
        return "", [], ""
    region_part, attractions_after = text, ""
    for sep in ("：", ":"):
        if sep in text:
            region_part, _, attractions_after = text.partition(sep)
            region_part = region_part.strip()
            attractions_after = attractions_after.strip()
            break
    # Split into hierarchy segments.
    parts = [region_part]
    for sep in _REGION_CHAIN_SPLITTERS:
        parts = [seg for p in parts for seg in p.split(sep)]
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return region_part, [], attractions_after
    target = parts[-1]
    ancestors = parts[:-1]
    return target, ancestors, attractions_after


def _build_map(region_geojson: dict, resolved: dict, attractions: list[dict]):
    """Build and render a pins map, returning (html, title)."""
    locations = [
        Location(
            name=a["name"],
            lat=float(a["lat"]),
            lon=float(a["lon"]),
            label=a.get("label") or None,
        )
        for a in attractions
    ]
    if not locations:
        raise ValueError("没有可用的景点坐标")

    # Write the boundary to a temp file so the existing region code (which
    # loads GeoJSON from disk) works unchanged.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".geojson", delete=False, encoding="utf-8"
    )
    json.dump(region_geojson, tmp, ensure_ascii=False)
    tmp.close()

    title = f"{resolved['name']} · 景点分布图"
    config = TravelConfig(
        title=title,
        locations=locations,
        style="pins",
        output="interactive",
        show_labels=True,
        export_button=True,
        regions=[tmp.name],
        show_dates=False,
    )
    renderer = STYLES["pins"](config)
    try:
        return renderer.render_interactive(), title
    finally:
        # The rendered HTML and embedded PNG no longer need the boundary file.
        # Removing it prevents an ever-growing /tmp directory in the container.
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass


@app.route("/")
def index():
    # Keep a real interactive map as the full-screen surface from first paint,
    # before the user has generated a destination-specific result.
    initial_map = folium.Map(
        location=[35.8, 104.2],
        zoom_start=4,
        tiles="OpenStreetMap",
        control_scale=True,
        zoom_control=True,
    ).get_root().render()
    return render_template("index.html", initial_map=initial_map)


@app.route("/healthz")
def healthz():
    """Lightweight container and reverse-proxy health check."""
    return jsonify({"status": "ok"})


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True) or {}
    region = (data.get("region") or "").strip()
    attractions_text = (data.get("attractions") or "").strip()
    text = (data.get("text") or "").strip()

    if not (region or text) or not (attractions_text or text):
        return jsonify({"error": "请填写地区与景点列表（或用一行「地区：景点」文本）"}), 400

    use_llm = bool(llm.api_key())

    try:
        # --- Resolve region name + ancestors -------------------------------
        region_name, ancestors = region, []
        if use_llm:
            try:
                if text and not region:
                    parsed = llm.parse(text)
                    region_name = parsed["region_name"]
                    ancestors = parsed["ancestors"]
                    if not attractions_text:
                        attractions_text = "、".join(parsed["attractions"])
                else:
                    norm = llm.normalize_region(region)
                    region_name = norm["region_name"]
                    ancestors = norm["ancestors"]
            except Exception:
                # Invalid/missing key or model error: degrade to raw input.
                pass
        elif text and not region:
            region_name, ancestors, after = _parse_region_chain(text)
            if not attractions_text and after:
                attractions_text = after

        if not region_name:
            return jsonify({"error": "无法确定地区，请单独填写地区名"}), 400

        resolved = geo.resolve_region(region_name, ancestors or None)

        # --- Geocode attractions -------------------------------------------
        names = _split_attractions(attractions_text)
        if not names:
            return jsonify({"error": "请填写至少一个景点"}), 400

        attractions = []
        missing = names
        if use_llm:
            try:
                for a in llm.geocode(names, resolved["name"]):
                    if a.get("name") and a.get("lat") is not None and a.get("lon") is not None:
                        attractions.append(a)
                missing = [n for n in names if n not in {a["name"] for a in attractions}]
            except Exception:
                pass  # fall through to Nominatim for all

        for n in missing:
            coord = geo.nominatim_geocode(n, resolved["name"])
            if coord:
                attractions.append({"name": n, "lat": coord[0], "lon": coord[1], "label": ""})

        if not attractions:
            return jsonify({"error": "未能解析任何景点经纬度"}), 422

        html, title = _build_map(resolved["geojson"], resolved, attractions)
        return jsonify({
            "html": html,
            "title": title,
            "region": resolved["name"],
            "attractions": [a["name"] for a in attractions],
        })
    except geo.RegionNotFound as e:
        return jsonify({"error": str(e)}), 422
    except llm.LLMUnavailable as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        app.logger.exception("Map generation failed")
        return jsonify({"error": f"生成失败：{e}"}), 500


def run(host: str = "127.0.0.1", port: int = 8000, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run()
