"""Indiana Jones style renderer - vintage maps with red flight paths."""

import folium
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from PIL import Image, ImageFilter, ImageEnhance
import io
import numpy as np

from .base import BaseRenderer
from ..config import TravelConfig


class IndianaJonesRenderer(BaseRenderer):
    """Render maps in the classic Indiana Jones movie style."""

    def __init__(self, config: TravelConfig):
        super().__init__(config)
        self.path_color = "#c0392b"  # Dark red for flight paths
        self.marker_color = "#c0392b"
        # Sepia/vintage color palette
        self.land_color = "#d4c4a8"
        self.ocean_color = "#a8c4c4"
        self.border_color = "#8b7355"

    def _interpolate_great_circle(
        self, lat1: float, lon1: float, lat2: float, lon2: float, num_points: int = 50
    ) -> list[tuple[float, float]]:
        """Interpolate points along a great circle arc."""
        lat1_r, lon1_r = np.radians(lat1), np.radians(lon1)
        lat2_r, lon2_r = np.radians(lat2), np.radians(lon2)

        d = np.arccos(
            np.clip(
                np.sin(lat1_r) * np.sin(lat2_r) +
                np.cos(lat1_r) * np.cos(lat2_r) * np.cos(lon2_r - lon1_r),
                -1, 1
            )
        )

        if d < 1e-10:
            return [(lat1, lon1), (lat2, lon2)]

        points = []
        for i in range(num_points + 1):
            f = i / num_points
            A = np.sin((1 - f) * d) / np.sin(d)
            B = np.sin(f * d) / np.sin(d)

            x = A * np.cos(lat1_r) * np.cos(lon1_r) + B * np.cos(lat2_r) * np.cos(lon2_r)
            y = A * np.cos(lat1_r) * np.sin(lon1_r) + B * np.cos(lat2_r) * np.sin(lon2_r)
            z = A * np.sin(lat1_r) + B * np.sin(lat2_r)

            lat = np.degrees(np.arctan2(z, np.sqrt(x**2 + y**2)))
            lon = np.degrees(np.arctan2(y, x))
            points.append((lat, lon))

        return points

    def _should_go_westward(self, from_lon: float, to_lon: float) -> bool:
        """Determine if arc should go westward (across Pacific) or eastward."""
        nepal_longitude = 85.0
        if from_lon < 0 and to_lon > nepal_longitude:
            return True
        return False

    def _unwrap_longitudes(
        self, points: list[tuple[float, float]], force_westward: bool = False
    ) -> list[tuple[float, float]]:
        """Unwrap longitude values to make them continuous across the dateline."""
        if len(points) < 2:
            return points

        unwrapped = [points[0]]
        offset = 0.0

        for i in range(1, len(points)):
            prev_lon = unwrapped[i - 1][1]
            curr_lat = points[i][0]
            curr_lon = points[i][1] + offset

            delta = curr_lon - prev_lon
            if delta > 180:
                offset -= 360
                curr_lon -= 360
            elif delta < -180:
                offset += 360
                curr_lon += 360

            unwrapped.append((curr_lat, curr_lon))

        # If we should go westward but ended up going east, shift everything
        if force_westward and len(unwrapped) > 1:
            start_lon = unwrapped[0][1]
            end_lon = unwrapped[-1][1]
            if end_lon > start_lon:
                unwrapped = [(lat, lon - 360) for lat, lon in unwrapped]

        return unwrapped

    def _apply_vintage_effect(self, img: Image.Image) -> Image.Image:
        """Apply vintage/sepia effect to an image."""
        # Convert to RGB if necessary
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Get pixel data
        pixels = np.array(img, dtype=np.float32)

        # Sepia transformation matrix
        sepia_matrix = np.array([
            [0.393, 0.769, 0.189],
            [0.349, 0.686, 0.168],
            [0.272, 0.534, 0.131],
        ])

        # Apply sepia
        sepia_pixels = np.dot(pixels[..., :3], sepia_matrix.T)
        sepia_pixels = np.clip(sepia_pixels, 0, 255).astype(np.uint8)

        sepia_img = Image.fromarray(sepia_pixels)

        # Reduce contrast slightly for aged look
        enhancer = ImageEnhance.Contrast(sepia_img)
        sepia_img = enhancer.enhance(0.9)

        # Add slight warmth
        enhancer = ImageEnhance.Color(sepia_img)
        sepia_img = enhancer.enhance(0.85)

        # Add subtle vignette effect
        sepia_img = self._add_vignette(sepia_img)

        # Add paper texture effect (noise)
        sepia_img = self._add_paper_texture(sepia_img)

        return sepia_img

    def _add_vignette(self, img: Image.Image) -> Image.Image:
        """Add a subtle vignette effect."""
        width, height = img.size
        pixels = np.array(img, dtype=np.float32)

        # Create vignette mask
        x = np.linspace(-1, 1, width)
        y = np.linspace(-1, 1, height)
        X, Y = np.meshgrid(x, y)
        radius = np.sqrt(X**2 + Y**2)

        # Smooth vignette falloff
        vignette = 1 - np.clip((radius - 0.7) / 0.8, 0, 1) ** 2
        vignette = vignette[:, :, np.newaxis]

        # Apply vignette
        pixels = pixels * (0.3 + 0.7 * vignette)
        pixels = np.clip(pixels, 0, 255).astype(np.uint8)

        return Image.fromarray(pixels)

    def _add_paper_texture(self, img: Image.Image) -> Image.Image:
        """Add subtle paper texture noise."""
        pixels = np.array(img, dtype=np.float32)

        # Add subtle noise
        noise = np.random.normal(0, 3, pixels.shape)
        pixels = pixels + noise
        pixels = np.clip(pixels, 0, 255).astype(np.uint8)

        return Image.fromarray(pixels)

    def render_interactive(self) -> str:
        """Render an interactive map with vintage styling."""
        # First pass: calculate longitude offsets for all locations
        lon_offset_by_name = {}
        routes = self.config.get_routes()
        for from_loc, to_loc in routes:
            from_offset = lon_offset_by_name.get(from_loc.name, 0)
            effective_from_lon = from_loc.lon + from_offset
            force_westward = self._should_go_westward(effective_from_lon, to_loc.lon)

            arc_points = self._interpolate_great_circle(
                from_loc.lat, from_loc.lon,
                to_loc.lat, to_loc.lon,
            )
            arc_points = self._unwrap_longitudes(arc_points, force_westward=force_westward)

            if abs(from_offset) > 1:
                arc_points = [(lat, lon + from_offset) for lat, lon in arc_points]

            end_lon = arc_points[-1][1]
            to_offset = end_lon - to_loc.lon
            if abs(to_offset) > 1:
                lon_offset_by_name[to_loc.name] = to_offset

        # Calculate bounds using effective (shifted) longitudes
        lats = [loc.lat for loc in self.config.locations]
        effective_lons = []
        for loc in self.config.locations:
            effective_lon = loc.lon
            if loc.name in lon_offset_by_name:
                effective_lon = loc.lon + lon_offset_by_name[loc.name]
            effective_lons.append(effective_lon)

        # Add home location for bounds calculation
        home = self.config.get_home()
        lats.append(home.lat)
        effective_lons.append(home.lon)

        padding = 5
        bounds = [
            min(lats) - padding,
            max(lats) + padding,
            min(effective_lons) - padding,
            max(effective_lons) + padding,
        ]

        # Center on home location (Madison)
        center = [home.lat, home.lon]

        # Use Esri World Topo Map for vintage cartographic look (free, no auth)
        m = folium.Map(
            location=center,
            zoom_start=4,
            tiles=None,
        )

        # Add vintage-style tile layer (Esri World Topo has nice cartographic feel)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
            attr='Tiles &copy; Esri',
            name="Vintage",
        ).add_to(m)

        m.fit_bounds([[bounds[0], bounds[2]], [bounds[1], bounds[3]]])

        # Draw red dotted flight paths
        routes = self.config.get_routes()
        for from_loc, to_loc in routes:
            # Get the offset for from_loc (for multi-leg trips crossing dateline)
            from_offset = lon_offset_by_name.get(from_loc.name, 0)

            # Determine if this arc should go westward (across Pacific)
            effective_from_lon = from_loc.lon + from_offset
            force_westward = self._should_go_westward(effective_from_lon, to_loc.lon)

            arc_points = self._interpolate_great_circle(
                from_loc.lat, from_loc.lon,
                to_loc.lat, to_loc.lon,
            )

            # Unwrap longitudes to make continuous across dateline
            arc_points = self._unwrap_longitudes(arc_points, force_westward=force_westward)

            # If we have a from_offset, apply it to all points
            # (the interpolation normalizes longitudes, so we need to shift them back)
            if abs(from_offset) > 1:
                arc_points = [(lat, lon + from_offset) for lat, lon in arc_points]

            # Calculate the total offset for the destination
            end_lon = arc_points[-1][1]
            to_offset = end_lon - to_loc.lon
            if abs(to_offset) > 1:
                lon_offset_by_name[to_loc.name] = to_offset

            arc_coords = [[p[0], p[1]] for p in arc_points]

            # Red dashed line (classic Indiana Jones style)
            folium.PolyLine(
                locations=arc_coords,
                color=self.path_color,
                weight=4,
                opacity=0.9,
                dash_array="10, 10",
            ).add_to(m)

            # Add plane icon at midpoint
            mid_idx = len(arc_points) // 2
            mid_point = arc_points[mid_idx]

            # Calculate direction for plane rotation
            if mid_idx > 0:
                prev_point = arc_points[mid_idx - 1]
                angle = np.degrees(np.arctan2(
                    mid_point[0] - prev_point[0],
                    mid_point[1] - prev_point[1]
                ))
            else:
                angle = 0

            # Plane icon (Unicode airplane)
            plane_html = f'''
            <div style="
                font-size: 20px;
                color: {self.path_color};
                transform: rotate({90 - angle}deg);
                text-shadow: 1px 1px 2px rgba(0,0,0,0.3);
            ">&#9992;</div>
            '''
            folium.Marker(
                location=[mid_point[0], mid_point[1]],
                icon=folium.DivIcon(
                    html=plane_html,
                    icon_size=(30, 30),
                    icon_anchor=(15, 15),
                ),
            ).add_to(m)

        # Add location markers at their effective positions (red circles with vintage feel)
        for i, loc in enumerate(self.config.locations):
            popup_content = f"<b style='font-family: Georgia, serif;'>{loc.name}</b>"
            if loc.label:
                popup_content += f"<br><i>{loc.label}</i>"
            date_str = self._format_date(loc)
            if date_str:
                popup_content += f"<br><span style='color: #8b4513;'>{date_str}</span>"

            # Use shifted longitude if this location crossed the dateline
            effective_lon = loc.lon
            if loc.name in lon_offset_by_name:
                effective_lon = loc.lon + lon_offset_by_name[loc.name]

            folium.CircleMarker(
                location=[loc.lat, effective_lon],
                radius=8,
                popup=folium.Popup(popup_content, max_width=200),
                tooltip=loc.name,
                color=self.path_color,
                fill=True,
                fill_color=self.path_color,
                fill_opacity=0.9,
                weight=2,
            ).add_to(m)

        # Vintage-style title if specified
        if self.config.title:
            title_html = f'''
            <div style="position: fixed;
                        top: 10px; left: 50px;
                        z-index: 1000;
                        background-color: #f4e4bc;
                        padding: 12px 20px;
                        border-radius: 3px;
                        border: 2px solid #8b7355;
                        box-shadow: 3px 3px 5px rgba(0,0,0,0.3);
                        font-family: 'Georgia', serif;
                        font-size: 18px;
                        font-weight: bold;
                        color: #5d4e37;">
                {self.config.title}
            </div>
            '''
            m.get_root().html.add_child(folium.Element(title_html))

        return m._repr_html_()

    def render_static(self, width: int = 1200, height: int = 800) -> Image.Image:
        """Render a static vintage-style map."""
        fig_width = width / 100
        fig_height = height / 100

        # First pass: calculate longitude offsets for all locations
        lon_offset_by_name = {}
        routes = self.config.get_routes()
        for from_loc, to_loc in routes:
            from_offset = lon_offset_by_name.get(from_loc.name, 0)
            effective_from_lon = from_loc.lon + from_offset
            force_westward = self._should_go_westward(effective_from_lon, to_loc.lon)

            arc_points = self._interpolate_great_circle(
                from_loc.lat, from_loc.lon,
                to_loc.lat, to_loc.lon,
            )
            arc_points = self._unwrap_longitudes(arc_points, force_westward=force_westward)

            if abs(from_offset) > 1:
                arc_points = [(lat, lon + from_offset) for lat, lon in arc_points]

            end_lon = arc_points[-1][1]
            to_offset = end_lon - to_loc.lon
            if abs(to_offset) > 1:
                lon_offset_by_name[to_loc.name] = to_offset

        # Calculate bounds using effective (shifted) longitudes
        lats = [loc.lat for loc in self.config.locations]
        effective_lons = []
        for loc in self.config.locations:
            effective_lon = loc.lon
            if loc.name in lon_offset_by_name:
                effective_lon = loc.lon + lon_offset_by_name[loc.name]
            effective_lons.append(effective_lon)

        # Add home location if routes_from_home is enabled
        if self.config.routes_from_home:
            home = self.config.get_home()
            lats.append(home.lat)
            effective_lons.append(home.lon)

        padding = 5
        bounds = [
            min(lats) - padding,
            max(lats) + padding,
            min(effective_lons) - padding,
            max(effective_lons) + padding,
        ]

        # Center on home location (Madison) for better visual balance
        home = self.config.get_home()
        center_lon = home.lon

        # Calculate extent that matches the figure aspect ratio
        data_lon_range = bounds[3] - bounds[2]
        data_lat_range = bounds[1] - bounds[0]
        fig_aspect = fig_width / fig_height
        data_aspect = data_lon_range / data_lat_range

        # Expand bounds to match figure aspect ratio
        if data_aspect > fig_aspect:
            # Data is wider than figure - expand latitude
            new_lat_range = data_lon_range / fig_aspect
            lat_center = (bounds[0] + bounds[1]) / 2
            extent_bounds = [
                lat_center - new_lat_range / 2,
                lat_center + new_lat_range / 2,
                bounds[2],
                bounds[3],
            ]
        else:
            # Data is taller than figure - expand longitude
            new_lon_range = data_lat_range * fig_aspect
            lon_center = (bounds[2] + bounds[3]) / 2
            extent_bounds = [
                bounds[0],
                bounds[1],
                lon_center - new_lon_range / 2,
                lon_center + new_lon_range / 2,
            ]

        fig = plt.figure(figsize=(fig_width, fig_height))
        ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=center_lon))

        ax.set_extent([extent_bounds[2], extent_bounds[3], extent_bounds[0], extent_bounds[1]], crs=ccrs.PlateCarree())

        # Vintage color scheme for map features
        ax.add_feature(cfeature.LAND, facecolor=self.land_color)
        ax.add_feature(cfeature.OCEAN, facecolor=self.ocean_color)
        ax.add_feature(cfeature.COASTLINE, linewidth=0.8, edgecolor=self.border_color)
        ax.add_feature(cfeature.BORDERS, linewidth=0.5, linestyle="-", edgecolor=self.border_color)
        ax.add_feature(cfeature.LAKES, facecolor=self.ocean_color, alpha=0.7)
        ax.add_feature(cfeature.RIVERS, edgecolor=self.ocean_color, linewidth=0.5)

        # Draw red dotted flight paths
        routes = self.config.get_routes()
        for from_loc, to_loc in routes:
            # Get the offset for from_loc (for multi-leg trips crossing dateline)
            from_offset = lon_offset_by_name.get(from_loc.name, 0)

            # Determine if this arc should go westward (across Pacific)
            effective_from_lon = from_loc.lon + from_offset
            force_westward = self._should_go_westward(effective_from_lon, to_loc.lon)

            arc_points = self._interpolate_great_circle(
                from_loc.lat, from_loc.lon,
                to_loc.lat, to_loc.lon,
            )

            # Unwrap longitudes to make continuous across dateline
            arc_points = self._unwrap_longitudes(arc_points, force_westward=force_westward)

            # If we have a from_offset, apply it to all points
            if abs(from_offset) > 1:
                arc_points = [(lat, lon + from_offset) for lat, lon in arc_points]

            # Calculate the total offset for the destination
            end_lon = arc_points[-1][1]
            to_offset = end_lon - to_loc.lon
            if abs(to_offset) > 1:
                lon_offset_by_name[to_loc.name] = to_offset

            lats = [p[0] for p in arc_points]
            lons = [p[1] for p in arc_points]

            # Red dashed line
            ax.plot(
                lons, lats,
                color=self.path_color,
                linewidth=3,
                linestyle=(0, (5, 5)),  # Dashed
                transform=ccrs.PlateCarree(),
                zorder=4,
            )

            # Add plane icon at midpoint
            mid_idx = len(arc_points) // 2
            mid_lat, mid_lon = arc_points[mid_idx]

            # Calculate angle for plane orientation
            if mid_idx > 0 and mid_idx < len(arc_points) - 1:
                dlat = arc_points[mid_idx + 1][0] - arc_points[mid_idx - 1][0]
                dlon = arc_points[mid_idx + 1][1] - arc_points[mid_idx - 1][1]
                angle = np.degrees(np.arctan2(dlat, dlon))
            else:
                angle = 0

            # Draw plane symbol
            ax.text(
                mid_lon, mid_lat,
                "✈",
                transform=ccrs.PlateCarree(),
                fontsize=16,
                ha="center",
                va="center",
                color=self.path_color,
                rotation=angle - 90,
                zorder=5,
            )

        # Plot location markers at their effective positions
        for loc in self.config.locations:
            # Use shifted longitude if this location crossed the dateline
            effective_lon = loc.lon
            if loc.name in lon_offset_by_name:
                effective_lon = loc.lon + lon_offset_by_name[loc.name]

            # Red circle marker
            ax.plot(
                effective_lon, loc.lat,
                marker="o",
                color=self.path_color,
                markersize=12,
                transform=ccrs.PlateCarree(),
                zorder=6,
                markeredgecolor="#5d4e37",
                markeredgewidth=1.5,
            )

            # Label with vintage typography
            label = loc.name.upper()  # Uppercase for vintage map feel
            date_str = self._format_date(loc)
            if date_str:
                label += f"\n{date_str}"

            ax.text(
                effective_lon, loc.lat + 1.2,
                label,
                transform=ccrs.PlateCarree(),
                fontsize=9,
                ha="center",
                va="bottom",
                fontweight="bold",
                fontfamily="serif",
                color="#5d4e37",
                bbox=dict(
                    boxstyle="round,pad=0.3",
                    facecolor="#f4e4bc",
                    edgecolor="#8b7355",
                    alpha=0.9,
                ),
                zorder=7,
            )

        # Vintage-style title
        ax.set_title(
            self.config.title.upper(),
            fontsize=18,
            fontweight="bold",
            fontfamily="serif",
            color="#5d4e37",
            pad=15,
        )

        # Remove axis frame for cleaner look
        ax.spines["geo"].set_edgecolor(self.border_color)
        ax.spines["geo"].set_linewidth(2)

        # Save to buffer - use tight_layout to fill figure while keeping some padding
        fig.tight_layout(pad=0.5)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor="#f4e4bc")
        plt.close(fig)
        buf.seek(0)

        # Load and apply vintage effects
        img = Image.open(buf)
        img = self._apply_vintage_effect(img.copy())

        return img
