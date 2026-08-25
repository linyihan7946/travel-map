"""Pins style renderer - clean modern map with location markers."""

import base64
import html
import os
import socket

import folium
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from PIL import Image
import io
import numpy as np

_CJK_FONT_SET = False
_DEFAULT_STATIC_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Street_Map/MapServer/tile/{z}/{y}/{x}"
)


def _ensure_cjk_font():
    """Register a CJK-capable font so Chinese labels render (not boxes)."""
    global _CJK_FONT_SET
    if _CJK_FONT_SET:
        return
    try:
        from matplotlib import font_manager

        candidates = [
            "Microsoft YaHei",
            "SimHei",
            "SimSun",
            "DengXian",
            "PingFang SC",
            "Noto Sans CJK SC",
            "WenQuanYi Micro Hei",
            "Malgun Gothic",
        ]
        available = {f.name for f in font_manager.fontManager.ttflist}
        for name in candidates:
            if name in available:
                plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
                plt.rcParams["axes.unicode_minus"] = False
                break
    except Exception:
        pass
    _CJK_FONT_SET = True

from .base import BaseRenderer
from ..config import TravelConfig


class PinsRenderer(BaseRenderer):
    """Render maps with simple pin markers at each location."""

    def __init__(self, config: TravelConfig):
        super().__init__(config)
        self.marker_color = "#e74c3c"  # Red markers

    def render_interactive(self) -> str:
        """Render an interactive Folium map with markers."""
        center = self._get_center()
        m = folium.Map(
            location=center,
            zoom_start=4,
            tiles="OpenStreetMap",
        )

        # Region maps should stay focused on the highlighted administrative
        # area.  The generic bounds add a fixed two-degree margin, which is
        # useful for unbounded trip maps but makes a city fill only a small
        # part of the viewport.
        bounds = self._get_export_bounds()
        m.fit_bounds([[bounds[0], bounds[2]], [bounds[1], bounds[3]]])

        # Add markers for each location
        for loc in self.config.locations:
            # Build popup content
            popup_content = f"<b>{html.escape(loc.name)}</b>"
            if loc.label:
                popup_content += f"<br>{html.escape(loc.label)}"
            date_str = self._format_date(loc)
            if date_str:
                popup_content += f"<br><i>{date_str}</i>"

            # Build tooltip (shown on hover)
            tooltip = loc.name
            if date_str:
                tooltip += f" ({date_str})"

            if self.config.show_labels:
                # Permanent label shown next to the marker
                icon = folium.DivIcon(
                    html=f'''<div style="display:inline-flex; align-items:center; white-space:nowrap; font-family:Arial, sans-serif;">
                        <span style="display:inline-block; width:12px; height:12px; background:#e74c3c; border:2px solid #fff; border-radius:50%; box-shadow:0 0 4px rgba(0,0,0,0.45);"></span>
                        <span style="margin-left:5px; font-size:13px; font-weight:600; color:#222; text-shadow:0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff;">{html.escape(loc.name)}</span>
                    </div>''',
                    icon_anchor=(8, 8),
                )
            else:
                icon = folium.Icon(color="red", icon="info-sign")

            folium.Marker(
                location=[loc.lat, loc.lon],
                popup=folium.Popup(popup_content, max_width=200),
                tooltip=tooltip,
                icon=icon,
            ).add_to(m)

        # Highlight whole regions (e.g. a province) under the markers
        self._add_highlight_regions(m)

        # Add home marker if routes_from_home is enabled
        if self.config.routes_from_home:
            home = self.config.get_home()
            folium.Marker(
                location=[home.lat, home.lon],
                popup=folium.Popup(f"<b>{home.name}</b><br>Home", max_width=200),
                tooltip=f"{home.name} (Home)",
                icon=folium.Icon(color="green", icon="home", prefix="fa"),
            ).add_to(m)

        # Add title if specified
        if self.config.title:
            title_html = f'''
            <div style="position: fixed;
                        top: 10px; left: 50px;
                        z-index: 1000;
                        background-color: white;
                        padding: 10px;
                        border-radius: 5px;
                        box-shadow: 0 2px 5px rgba(0,0,0,0.2);
                        font-family: Arial, sans-serif;
                        font-size: 16px;
                        font-weight: bold;">
                {self.config.title}
            </div>
            '''
            m.get_root().html.add_child(folium.Element(title_html))

        # Add export button if requested
        if self.config.export_button:
            self._add_export_button(m)

        # Return a complete HTML document for the web UI's full-screen iframe.
        # Folium's notebook representation wraps the map in a fixed-ratio
        # iframe, which leaves unused space instead of filling the viewport.
        return m.get_root().render()

    def _render_export_png(self, width: int = 1400, height: int = 900) -> bytes:
        """Render the map to PNG bytes for the in-page export button."""
        img = self.render_static(width=width, height=height)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _add_export_button(self, m) -> None:
        """Inject a button that downloads a PNG of the current map."""
        b64 = base64.b64encode(self._render_export_png()).decode("ascii")

        # Derive a filename from the title
        raw = (self.config.title or "travel-map").strip()
        fname = "".join(c if c.isalnum() else "-" for c in raw).strip("-")
        while "--" in fname:
            fname = fname.replace("--", "-")
        fname = (fname or "travel-map") + ".png"

        export_html = f'''<div style="position: fixed; bottom: 20px; right: 20px; z-index: 1000;">
            <button id="tm-export-btn" style="background:#e74c3c; color:#fff; border:none; border-radius:4px; padding:8px 14px; font-size:14px; font-weight:600; cursor:pointer; box-shadow:0 2px 5px rgba(0,0,0,0.3);">&#128229; 导出图片</button>
        </div>
        <script>
        document.getElementById('tm-export-btn').addEventListener('click', function() {{
            var a = document.createElement('a');
            a.download = '{fname}';
            a.href = 'data:image/png;base64,{b64}';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }});
        </script>'''
        m.get_root().html.add_child(folium.Element(export_html))

    @staticmethod
    def _add_static_label(ax, loc, label: str) -> None:
        """Place a label a fixed visual distance above its marker."""
        ax.annotate(
            label,
            xy=(loc.lon, loc.lat),
            xytext=(0, 10),
            textcoords="offset points",
            transform=ccrs.PlateCarree(),
            fontsize=9,
            ha="center",
            va="bottom",
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.7),
            zorder=6,
        )

    def render_static(self, width: int = 1200, height: int = 800) -> Image.Image:
        """Render a static matplotlib/cartopy map with markers."""
        _ensure_cjk_font()

        # Calculate figure size in inches (assuming 100 dpi)
        fig_width = width / 100
        fig_height = height / 100

        # Use tight region bounds for export (focused on the highlighted area),
        # falling back to the wider padded bounds for general trip maps.
        bounds = self._get_export_bounds()
        center_lon = (bounds[2] + bounds[3]) / 2

        # Region-focused maps use real OSM tiles as the base texture; general
        # trip maps fall back to Natural Earth features.
        tiler = None
        if self.config.regions:
            import cartopy.io.img_tiles as cimgt

            tile_url = os.environ.get("TRAVEL_MAP_STATIC_TILE_URL", _DEFAULT_STATIC_TILE_URL)
            tiler = cimgt.GoogleTiles(url=tile_url, cache=True)
            projection = tiler.crs
        else:
            projection = ccrs.PlateCarree(central_longitude=center_lon)

        fig = plt.figure(figsize=(fig_width, fig_height))
        ax = fig.add_subplot(1, 1, 1, projection=projection)

        # Set extent
        ax.set_extent([bounds[2], bounds[3], bounds[0], bounds[1]], crs=ccrs.PlateCarree())

        # Add base map
        if tiler is not None:
            import math

            span_lon = bounds[3] - bounds[2]
            # About six tiles across gives enough detail for a city map without
            # downloading dozens of high-zoom tiles during every export.
            zoom = max(2, min(round(math.log2(1440 / span_lon)), 11))
            try:
                ax.add_image(tiler, zoom)
            except Exception:
                # No network / tile failure: fall back to a light graticule
                ax.set_facecolor("#f7f7f7")
                gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="#c8c8c8", linestyle="--")
                gl.top_labels = False
                gl.right_labels = False
        else:
            ax.add_feature(cfeature.LAND, facecolor="#f0f0f0")
            ax.add_feature(cfeature.OCEAN, facecolor="#d4e6f1")
            ax.add_feature(cfeature.COASTLINE, linewidth=0.5)
            ax.add_feature(cfeature.BORDERS, linewidth=0.3, linestyle=":")
            ax.add_feature(cfeature.LAKES, facecolor="#d4e6f1", alpha=0.5)

        # Highlight configured regions
        region_geoms = self._region_geometries()
        if region_geoms:
            ax.add_geometries(
                region_geoms,
                crs=ccrs.PlateCarree(),
                facecolor="#f39c12",
                edgecolor="#e67e22",
                alpha=0.30,
                linewidth=1.2,
                zorder=3,
            )

        # Plot markers
        for loc in self.config.locations:
            ax.plot(
                loc.lon, loc.lat,
                marker="o",
                color=self.marker_color,
                markersize=12,
                markeredgecolor="white",
                markeredgewidth=1.5,
                transform=ccrs.PlateCarree(),
                zorder=5,
            )

            # Add label
            label = loc.name
            date_str = self._format_date(loc)
            if date_str:
                label += f"\n{date_str}"

            self._add_static_label(ax, loc, label)

        # Add title
        ax.set_title(self.config.title, fontsize=16, fontweight="bold", pad=10)

        # Convert to PIL Image
        buf = io.BytesIO()
        # urllib otherwise has no timeout for tile downloads. A single blocked
        # provider must not hold a Gunicorn worker until its request timeout.
        previous_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(float(os.environ.get("TRAVEL_MAP_TILE_TIMEOUT", "8")))
            fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="white")
        finally:
            socket.setdefaulttimeout(previous_timeout)
            plt.close(fig)
        buf.seek(0)

        return Image.open(buf).copy()
