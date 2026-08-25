"""Base renderer abstract class."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..config import TravelConfig


class BaseRenderer(ABC):
    """Abstract base class for map renderers."""

    def __init__(self, config: TravelConfig):
        """Initialize the renderer with a configuration."""
        self.config = config

    @abstractmethod
    def render_interactive(self) -> str:
        """Render an interactive HTML map.

        Returns:
            HTML string of the interactive map.
        """
        pass

    @abstractmethod
    def render_static(self, width: int = 1200, height: int = 800) -> "Image":
        """Render a static image of the map.

        Args:
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            PIL Image object.
        """
        pass

    def render(self, output_path: Optional[str | Path] = None) -> str | Path:
        """Render the map based on config output type.

        Args:
            output_path: Optional path to save the output.

        Returns:
            HTML string (interactive) or path to saved image (static).
        """
        if self.config.output == "interactive":
            html = self.render_interactive()
            if output_path:
                output_path = Path(output_path)
                if not output_path.suffix:
                    output_path = output_path.with_suffix(".html")
                output_path.write_text(html)
                return output_path
            return html
        else:
            img = self.render_static()
            if output_path:
                output_path = Path(output_path)
                if not output_path.suffix:
                    output_path = output_path.with_suffix(".png")
                img.save(output_path)
                return output_path
            # Return as bytes if no path specified
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

    def _get_all_locations(self) -> list:
        """Get all locations including home if routes_from_home is enabled."""
        locations = list(self.config.locations)
        if self.config.routes_from_home:
            locations.append(self.config.get_home())
        return locations

    def _region_geometries(self) -> list:
        """Load shapely geometries for all configured region GeoJSON files."""
        import json

        from shapely.geometry import shape

        base_dir = self.config.source_path.parent if self.config.source_path else Path(".")
        geoms = []
        for region_path in self.config.regions:
            p = Path(region_path)
            if not p.is_absolute():
                p = base_dir / p
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            for feature in data.get("features", []):
                geoms.append(shape(feature["geometry"]))
        return geoms

    def _region_bounds(self) -> Optional[tuple[float, float, float, float]]:
        """Combined bounds of all configured region geometries.

        Returns:
            Tuple of (min_lat, max_lat, min_lon, max_lon), or None if no regions.
        """
        bounds = None
        for geom in self._region_geometries():
            minx, miny, maxx, maxy = geom.bounds
            if bounds is None:
                bounds = [miny, maxy, minx, maxx]
            else:
                bounds[0] = min(bounds[0], miny)
                bounds[1] = max(bounds[1], maxy)
                bounds[2] = min(bounds[2], minx)
                bounds[3] = max(bounds[3], maxx)
        return tuple(bounds) if bounds else None

    def _get_bounds(self) -> tuple[float, float, float, float]:
        """Get map bounds from locations (and highlighted regions, if any).

        Returns:
            Tuple of (min_lat, max_lat, min_lon, max_lon).
        """
        locations = self._get_all_locations()
        lats = [loc.lat for loc in locations]
        lons = [loc.lon for loc in locations]

        region_bounds = self._region_bounds()
        if region_bounds:
            lats += [region_bounds[0], region_bounds[1]]
            lons += [region_bounds[2], region_bounds[3]]

        padding = 2.0  # degrees of padding
        return (
            min(lats) - padding,
            max(lats) + padding,
            min(lons) - padding,
            max(lons) + padding,
        )

    def _get_center(self) -> tuple[float, float]:
        """Get center point of all locations.

        Returns:
            Tuple of (lat, lon) for the center.
        """
        locations = self._get_all_locations()
        lats = [loc.lat for loc in locations]
        lons = [loc.lon for loc in locations]
        return (sum(lats) / len(lats), sum(lons) / len(lons))

    def _format_date(self, location) -> str:
        """Format a location's date for display."""
        if location.date and self.config.show_dates:
            return location.date.strftime(self.config.date_format)
        return ""

    def _add_highlight_regions(self, m) -> None:
        """Highlight geographic regions (GeoJSON files) on an interactive map.

        GeoJSON paths in ``config.regions`` are resolved relative to the config
        file, falling back to the current directory if the source is unknown.
        """
        import json

        import folium

        base_dir = self.config.source_path.parent if self.config.source_path else Path(".")
        for region_path in self.config.regions:
            p = Path(region_path)
            if not p.is_absolute():
                p = base_dir / p
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            folium.GeoJson(
                data,
                name="highlight",
                style_function=lambda feature: {
                    "fillColor": "#f39c12",
                    "color": "#e67e22",
                    "weight": 2,
                    "fillOpacity": 0.25,
                },
            ).add_to(m)
