"""Worldline style renderer using Three.js - 3D spacetime visualization with image textures."""

import base64
import io
import json
import math
import numpy as np
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from datetime import datetime, timedelta

from .base import BaseRenderer
from ..config import TravelConfig


class WorldlineThreejsRenderer(BaseRenderer):
    """Render maps as 3D worldline visualization using Three.js with real image textures."""

    def __init__(
        self,
        config: TravelConfig,
        base_map_opacity: float = 0.9,
        intermediate_opacity: float = 0.3,
    ):
        super().__init__(config)
        self.path_color = "#e74c3c"  # Red for worldlines
        self.marker_color = "#3498db"  # Blue for location markers
        self.home_color = "#2ecc71"  # Green for home
        self.base_map_opacity = base_map_opacity
        self.intermediate_opacity = intermediate_opacity

    def _render_map_image(
        self,
        lon_range: tuple[float, float],
        lat_range: tuple[float, float],
        resolution: int = 800
    ) -> str:
        """Render a map image using cartopy and return as base64 PNG string."""
        # Calculate the center of our desired view
        central_lon = (lon_range[0] + lon_range[1]) / 2

        # Normalize central_lon to be within -180 to 180 for cartopy
        while central_lon < -180:
            central_lon += 360
        while central_lon > 180:
            central_lon -= 360

        lon_half_span = (lon_range[1] - lon_range[0]) / 2
        lon_span = lon_range[1] - lon_range[0]
        lat_span = lat_range[1] - lat_range[0]

        # Calculate figure dimensions to match geographic extent
        aspect = lon_span / lat_span if lat_span > 0 else 2.0
        dpi = 100
        fig_height = resolution / dpi
        fig_width = fig_height * aspect
        fig = plt.figure(figsize=(fig_width, fig_height), dpi=dpi)

        # Use PlateCarree projection centered on our view
        projection = ccrs.PlateCarree(central_longitude=central_lon)
        ax = fig.add_axes([0, 0, 1, 1], projection=projection)

        # Set extent relative to central longitude
        ax.set_extent([-lon_half_span, lon_half_span, lat_range[0], lat_range[1]], crs=projection)

        # Add Natural Earth features
        ax.add_feature(cfeature.OCEAN, facecolor='#aad3df', zorder=0)
        ax.add_feature(cfeature.LAND, facecolor='#f5f3e5', zorder=1)
        ax.add_feature(cfeature.COASTLINE, linewidth=0.5, edgecolor='#888888', zorder=2)
        ax.add_feature(cfeature.BORDERS, linewidth=0.3, edgecolor='#aaaaaa', linestyle='-', zorder=2)
        ax.add_feature(cfeature.LAKES, facecolor='#aad3df', zorder=1)

        ax.set_frame_on(False)
        ax.axis('off')

        # Render to buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, transparent=False, facecolor='white')
        buf.seek(0)
        plt.close(fig)

        # Load and flip horizontally for Three.js coordinate system
        img = Image.open(buf)
        img = img.convert('RGB')
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

        # Convert to base64
        out_buf = io.BytesIO()
        img.save(out_buf, format='PNG', optimize=True)
        out_buf.seek(0)
        b64 = base64.b64encode(out_buf.read()).decode('utf-8')

        return f"data:image/png;base64,{b64}"

    def _normalize_time(self, locations: list) -> tuple[list[float], datetime, datetime]:
        """Convert dates to normalized time values (0-1 range)."""
        dated_locs = [loc for loc in locations if loc.date]
        if not dated_locs:
            return [0.5] * len(locations), None, None

        dates = [loc.date for loc in dated_locs]
        min_date = min(dates)
        max_date = max(dates)
        date_range = (max_date - min_date).days or 1

        normalized = []
        for loc in locations:
            if loc.date:
                days_from_start = (loc.date - min_date).days
                normalized.append(days_from_start / date_range)
            else:
                normalized.append(0.5)

        return normalized, min_date, max_date

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
        """Determine if arc should go westward (across Pacific).

        Only applies when starting from the genuine western hemisphere
        (not already shifted across dateline) going to eastern Asia.
        """
        nepal_longitude = 85.0
        # Only go westward if starting from genuine western hemisphere
        # (within normal -180 to 180 range, not already shifted)
        if -180 <= from_lon < 0 and to_lon > nepal_longitude:
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

        if force_westward and len(unwrapped) > 1:
            start_lon = unwrapped[0][1]
            end_lon = unwrapped[-1][1]
            if end_lon > start_lon:
                unwrapped = [(lat, lon - 360) for lat, lon in unwrapped]

        return unwrapped

    def render_interactive(self) -> str:
        """Render an interactive 3D worldline visualization using Three.js."""
        locations = self.config.locations
        normalized_times, min_date, max_date = self._normalize_time(locations)
        home = self.config.get_home()

        loc_times = {loc.name: t for loc, t in zip(locations, normalized_times)}

        # First pass: calculate longitude offsets for dateline handling
        # Use ORIGINAL coordinates, then shift the result
        lon_offset_by_name = {}
        routes = self.config.get_routes()
        for from_loc, to_loc in routes:
            from_offset = lon_offset_by_name.get(from_loc.name, 0)
            # Check if this should go westward using the effective starting longitude
            effective_from_lon = from_loc.lon + from_offset
            force_westward = self._should_go_westward(effective_from_lon, to_loc.lon)

            # Calculate arc using ORIGINAL coordinates
            arc_points = self._interpolate_great_circle(
                from_loc.lat, from_loc.lon, to_loc.lat, to_loc.lon
            )
            arc_points = self._unwrap_longitudes(arc_points, force_westward=force_westward)

            # Shift entire arc by from_offset to keep it in the same coordinate space
            if abs(from_offset) > 1:
                arc_points = [(lat, lon + from_offset) for lat, lon in arc_points]

            end_lon = arc_points[-1][1]
            to_offset = end_lon - to_loc.lon
            if abs(to_offset) > 1:
                lon_offset_by_name[to_loc.name] = to_offset

        # Calculate effective longitudes for all locations
        effective_lons = []
        for loc in locations:
            if loc.name in lon_offset_by_name:
                effective_lons.append(loc.lon + lon_offset_by_name[loc.name])
            else:
                effective_lons.append(loc.lon)
        effective_lons.append(home.lon)

        lats = [loc.lat for loc in locations]
        lats.append(home.lat)

        # Calculate extent (not necessarily symmetric around home)
        padding = 10

        # Longitude extent
        min_lon = min(effective_lons) - padding
        max_lon = max(effective_lons) + padding

        # Latitude extent - extend to include South America and Alaska
        min_lat = min(lats) - padding
        max_lat = max(lats) + padding
        min_lat = min(min_lat, -55)  # Always include South America
        max_lat = max(max_lat, 72)   # Always include Alaska

        # Clamp to valid range
        min_lat = max(min_lat, -85)
        max_lat = min(max_lat, 85)

        lon_range = (min_lon, max_lon)
        lat_range = (min_lat, max_lat)

        # Calculate distances from home for coordinate mapping
        max_lon_dist = max(abs(max_lon - home.lon), abs(min_lon - home.lon))
        max_lat_dist = max(abs(max_lat - home.lat), abs(min_lat - home.lat))

        # Render map image
        map_b64 = self._render_map_image(lon_range, lat_range, resolution=1000)

        # Build worldline paths data
        worldlines_data = []

        # Outbound routes
        for from_loc, to_loc in routes:
            from_time = loc_times.get(from_loc.name, 0.5)
            to_time = loc_times.get(to_loc.name, 0.5)

            if from_loc.name == home.name:
                from_time = max(0, to_time - 0.02)

            from_offset = lon_offset_by_name.get(from_loc.name, 0)
            effective_from_lon = from_loc.lon + from_offset
            force_westward = self._should_go_westward(effective_from_lon, to_loc.lon)

            # Calculate arc using ORIGINAL coordinates, then shift
            arc_points = self._interpolate_great_circle(
                from_loc.lat, from_loc.lon, to_loc.lat, to_loc.lon
            )
            arc_points = self._unwrap_longitudes(arc_points, force_westward=force_westward)

            # Shift entire arc by from_offset
            if abs(from_offset) > 1:
                arc_points = [(lat, lon + from_offset) for lat, lon in arc_points]

            times = np.linspace(from_time, to_time, len(arc_points)).tolist()
            worldlines_data.append({
                'points': [{'lat': float(p[0]), 'lon': float(p[1]), 't': float(t)}
                          for p, t in zip(arc_points, times)],
                'name': f"{from_loc.name} → {to_loc.name}",
                'dashed': False
            })

        # Return arcs from each location back to home
        if self.config.routes_from_home:
            for loc, loc_time in zip(locations, normalized_times):
                loc_offset = lon_offset_by_name.get(loc.name, 0)

                return_time = loc_time + 0.01
                # Calculate arc using ORIGINAL coordinates, then shift
                arc_points = self._interpolate_great_circle(
                    loc.lat, loc.lon, home.lat, home.lon
                )
                arc_points = self._unwrap_longitudes(arc_points)

                # Shift arc by loc_offset to match the location's effective position
                if abs(loc_offset) > 1:
                    arc_points = [(lat, lon + loc_offset) for lat, lon in arc_points]

                times = np.linspace(loc_time, return_time, len(arc_points)).tolist()
                worldlines_data.append({
                    'points': [{'lat': float(p[0]), 'lon': float(p[1]), 't': float(t)}
                              for p, t in zip(arc_points, times)],
                    'name': f"{loc.name} → Home",
                    'dashed': True
                })

        # Build markers data at effective positions
        markers_data = []
        for loc in locations:
            offset = lon_offset_by_name.get(loc.name, 0)
            markers_data.append({
                'name': loc.name,
                'lat': float(loc.lat),
                'lon': float(loc.lon + offset),
                't': float(loc_times.get(loc.name, 0.5)),
                'date': self._format_date(loc) if loc.date else None
            })

        # Calculate trip layer times (one layer per trip, at the first location's time)
        trip_layer_times = []
        if self.config.routes_from_home:
            # Group locations into trips based on date proximity
            dated_locations = [(loc, loc_times.get(loc.name, 0.5))
                               for loc in locations if loc.date]
            if dated_locations:
                sorted_locs = sorted(dated_locations, key=lambda x: x[0].date)
                current_trip_time = sorted_locs[0][1]  # First location's normalized time
                trip_layer_times.append(float(current_trip_time))

                for i in range(1, len(sorted_locs)):
                    prev_date = sorted_locs[i - 1][0].date
                    curr_date = sorted_locs[i][0].date
                    gap = (curr_date - prev_date).days

                    if gap > self.config.trip_gap_days:
                        # New trip - add a layer at this location's time
                        trip_layer_times.append(float(sorted_locs[i][1]))

        # Scene dimensions - use extent center for map alignment
        lon_center = (lon_range[0] + lon_range[1]) / 2
        lat_center = (lat_range[0] + lat_range[1]) / 2
        lon_span = lon_range[1] - lon_range[0]
        lat_span = lat_range[1] - lat_range[0]

        # Scale factor to make the scene reasonably sized
        scale = 0.1

        title = self.config.title if self.config.title else "Travel Worldline"

        # Generate HTML with Three.js
        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{ margin: 0; overflow: hidden; font-family: Arial, sans-serif; }}
        canvas {{ display: block; }}
        #info {{
            position: absolute;
            top: 10px;
            left: 10px;
            color: #333;
            background: rgba(255,255,255,0.8);
            padding: 10px;
            border-radius: 5px;
            font-size: 14px;
        }}
        #title {{
            position: absolute;
            top: 10px;
            width: 100%;
            text-align: center;
            color: #333;
            font-size: 24px;
            pointer-events: none;
        }}
    </style>
</head>
<body>
    <div id="title">{title}</div>
    <div id="info">Drag to rotate, scroll to zoom, double-click to select</div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
    <script>
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0xffffff);

        const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);

        const renderer = new THREE.WebGLRenderer({{ antialias: true }});
        renderer.setSize(window.innerWidth, window.innerHeight);
        document.body.appendChild(renderer.domElement);

        const controls = new THREE.OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;

        // Scene parameters
        const lonCenter = {lon_center};
        const latCenter = {lat_center};
        const lonSpan = {lon_span};
        const latSpan = {lat_span};
        const scale = {scale};
        const timeScale = lonSpan * scale * 0.3;

        // Convert geographic coordinates to scene coordinates
        // X is flipped to match the horizontally-flipped map image
        function toScene(lon, lat, t) {{
            return new THREE.Vector3(
                -(lon - lonCenter) * scale,
                t * timeScale,
                (lat - latCenter) * scale
            );
        }}

        // Create text sprite with transparent background
        function createLabel(text) {{
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            canvas.width = 512;
            canvas.height = 128;
            // Transparent background (no fillRect)
            ctx.font = 'bold 48px Arial';
            ctx.fillStyle = '#222';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            // Add subtle text shadow for readability
            ctx.shadowColor = 'rgba(255, 255, 255, 0.9)';
            ctx.shadowBlur = 6;
            ctx.shadowOffsetX = 0;
            ctx.shadowOffsetY = 0;
            ctx.fillText(text, 256, 64);
            const texture = new THREE.CanvasTexture(canvas);
            texture.minFilter = THREE.LinearFilter;
            const material = new THREE.SpriteMaterial({{ map: texture, transparent: true }});
            const sprite = new THREE.Sprite(material);
            sprite.scale.set(lonSpan * scale * 0.18, lonSpan * scale * 0.036, 1);
            return sprite;
        }}

        // Load map texture (disable auto-flip to match cartopy's coordinate system)
        const textureLoader = new THREE.TextureLoader();
        const mapTexture = textureLoader.load("{map_b64}", function(texture) {{
            texture.flipY = false;  // Cartopy: top=north, we want that preserved
            texture.needsUpdate = true;
        }});
        mapTexture.flipY = false;

        // Create map planes with correct dimensions
        const planeWidth = lonSpan * scale;
        const planeHeight = latSpan * scale;

        // Base map (bottom)
        const baseMat = new THREE.MeshBasicMaterial({{
            map: mapTexture, transparent: true, opacity: 1.0, side: THREE.DoubleSide
        }});
        const baseGeo = new THREE.PlaneGeometry(planeWidth, planeHeight);
        const basePlane = new THREE.Mesh(baseGeo, baseMat);
        basePlane.rotation.x = -Math.PI / 2;
        basePlane.position.y = -0.02 * timeScale;
        scene.add(basePlane);

        // Top map (transparent)
        const topMat = new THREE.MeshBasicMaterial({{
            map: mapTexture, transparent: true, opacity: 0.2, side: THREE.DoubleSide
        }});
        const topGeo = new THREE.PlaneGeometry(planeWidth, planeHeight);
        const topPlane = new THREE.Mesh(topGeo, topMat);
        topPlane.rotation.x = -Math.PI / 2;
        topPlane.position.y = 1.02 * timeScale;
        scene.add(topPlane);

        // Intermediate trip layers (semi-transparent, with hover interaction)
        const tripTimes = {json.dumps(trip_layer_times)};
        const tripPlanes = [];
        const defaultOpacity = 0.05;
        const hoverOpacity = 0.8;

        tripTimes.forEach(t => {{
            const tripMat = new THREE.MeshBasicMaterial({{
                map: mapTexture, transparent: true, opacity: defaultOpacity, side: THREE.DoubleSide
            }});
            const tripGeo = new THREE.PlaneGeometry(planeWidth, planeHeight);
            const tripPlane = new THREE.Mesh(tripGeo, tripMat);
            tripPlane.rotation.x = -Math.PI / 2;
            tripPlane.position.y = t * timeScale;
            tripPlane.userData.normalizedTime = t;
            scene.add(tripPlane);
            tripPlanes.push(tripPlane);
        }});

        // Corner vertical lines for the map cube
        const cornerLineMat = new THREE.LineBasicMaterial({{ color: 0xcccccc, transparent: true, opacity: 0.5 }});
        const corners = [
            [-planeWidth/2, -planeHeight/2],
            [-planeWidth/2, planeHeight/2],
            [planeWidth/2, -planeHeight/2],
            [planeWidth/2, planeHeight/2]
        ];
        corners.forEach(([x, z]) => {{
            const cornerLineGeo = new THREE.BufferGeometry().setFromPoints([
                new THREE.Vector3(x, -0.02 * timeScale, z),
                new THREE.Vector3(x, 1.02 * timeScale, z)
            ]);
            scene.add(new THREE.Line(cornerLineGeo, cornerLineMat));
        }});

        // Home vertical line (use effective home position accounting for any offset)
        const homeLon = {float(home.lon)};
        const homeLat = {float(home.lat)};
        const homePos = toScene(homeLon, homeLat, 0);
        const homeLineGeo = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(homePos.x, -0.02 * timeScale, homePos.z),
            new THREE.Vector3(homePos.x, 1.02 * timeScale, homePos.z)
        ]);
        const homeLineMat = new THREE.LineBasicMaterial({{ color: 0xe74c3c, linewidth: 3 }});
        scene.add(new THREE.Line(homeLineGeo, homeLineMat));

        // Home marker and label
        const homeSphereGeo = new THREE.SphereGeometry(lonSpan * scale * 0.006, 16, 16);
        const homeSphereMat = new THREE.MeshBasicMaterial({{ color: 0x2ecc71 }});
        const homeSphere = new THREE.Mesh(homeSphereGeo, homeSphereMat);
        homeSphere.position.set(homePos.x, 0, homePos.z);
        scene.add(homeSphere);

        const homeLabel = createLabel("{home.name}");
        homeLabel.position.set(homePos.x, 0.08 * timeScale, homePos.z);
        scene.add(homeLabel);

        // Draw worldlines
        const worldlines = {json.dumps(worldlines_data)};
        worldlines.forEach(wl => {{
            const points = wl.points.map(p => toScene(p.lon, p.lat, p.t));
            const geo = new THREE.BufferGeometry().setFromPoints(points);
            const mat = new THREE.LineBasicMaterial({{ color: 0xe74c3c }});
            scene.add(new THREE.Line(geo, mat));
        }});

        // Draw markers, labels, and drop lines
        const markers = {json.dumps(markers_data)};
        const markerGeo = new THREE.SphereGeometry(lonSpan * scale * 0.005, 16, 16);
        const markerMat = new THREE.MeshBasicMaterial({{ color: 0x3498db }});
        const hitGeo = new THREE.SphereGeometry(lonSpan * scale * 0.025, 16, 16);  // 5x larger for hit detection
        const hitMat = new THREE.MeshBasicMaterial({{ visible: false }});
        const dropLineMat = new THREE.LineBasicMaterial({{ color: 0xcccccc, transparent: true, opacity: 0.6 }});
        const markerSpheres = [];  // Used for raycasting (invisible hit spheres)

        markers.forEach(m => {{
            const pos = toScene(m.lon, m.lat, m.t);

            // Visible marker
            const sphere = new THREE.Mesh(markerGeo, markerMat);
            sphere.position.copy(pos);
            scene.add(sphere);

            // Larger invisible hit detection sphere
            const hitSphere = new THREE.Mesh(hitGeo, hitMat);
            hitSphere.position.copy(pos);
            hitSphere.userData.normalizedTime = m.t;
            hitSphere.userData.name = m.name;
            scene.add(hitSphere);
            markerSpheres.push(hitSphere);

            // Vertical drop line from marker to base map
            const dropLineGeo = new THREE.BufferGeometry().setFromPoints([
                new THREE.Vector3(pos.x, pos.y, pos.z),
                new THREE.Vector3(pos.x, -0.02 * timeScale, pos.z)
            ]);
            scene.add(new THREE.Line(dropLineGeo, dropLineMat));

            const label = createLabel(m.name);
            label.position.set(pos.x, pos.y + 0.05 * timeScale, pos.z);
            scene.add(label);
        }});

        // Raycaster for hover and click detection
        const raycaster = new THREE.Raycaster();
        const mouse = new THREE.Vector2();
        let hoveredPlane = null;
        let selectedPlane = null;
        const selectedOpacity = 0.8;
        const dimmedOpacity = 0.05;
        const baseDefaultOpacity = 1.0;
        const topDefaultOpacity = 0.2;

        function dimAllLayers() {{
            basePlane.material.opacity = dimmedOpacity;
            topPlane.material.opacity = dimmedOpacity;
            tripPlanes.forEach(plane => {{
                if (plane !== selectedPlane && plane !== hoveredPlane) {{
                    plane.material.opacity = dimmedOpacity;
                }}
            }});
        }}

        function restoreAllLayers() {{
            basePlane.material.opacity = baseDefaultOpacity;
            topPlane.material.opacity = topDefaultOpacity;
            tripPlanes.forEach(plane => {{
                plane.material.opacity = defaultOpacity;
            }});
        }}

        function findTripPlaneForMarker(markerTime) {{
            // Find the trip plane closest to this marker's time (at or before)
            let closestPlane = null;
            let closestDiff = Infinity;
            tripPlanes.forEach(plane => {{
                const planeTime = plane.userData.normalizedTime;
                if (planeTime <= markerTime) {{
                    const diff = markerTime - planeTime;
                    if (diff < closestDiff) {{
                        closestDiff = diff;
                        closestPlane = plane;
                    }}
                }}
            }});
            return closestPlane;
        }}

        function onMouseMove(event) {{
            mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
            mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;

            raycaster.setFromCamera(mouse, camera);
            const intersects = raycaster.intersectObjects(markerSpheres);

            // Reset previously hovered plane (but not if it's selected)
            if (hoveredPlane && hoveredPlane !== selectedPlane) {{
                hoveredPlane.material.opacity = defaultOpacity;
                hoveredPlane = null;
                // Restore layers if nothing selected
                if (!selectedPlane) {{
                    restoreAllLayers();
                }}
            }}

            if (intersects.length > 0) {{
                const markerTime = intersects[0].object.userData.normalizedTime;
                const closestPlane = findTripPlaneForMarker(markerTime);
                if (closestPlane && closestPlane !== selectedPlane) {{
                    // Dim all other layers
                    dimAllLayers();
                    closestPlane.material.opacity = hoverOpacity;
                    hoveredPlane = closestPlane;
                }}
            }}
        }}

        function onDoubleClick(event) {{
            mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
            mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;

            raycaster.setFromCamera(mouse, camera);
            const intersects = raycaster.intersectObjects(markerSpheres);

            if (intersects.length > 0) {{
                // Double-clicked on a marker - select its trip plane
                const markerTime = intersects[0].object.userData.normalizedTime;
                const closestPlane = findTripPlaneForMarker(markerTime);

                if (closestPlane) {{
                    // Deselect previous selection
                    if (selectedPlane && selectedPlane !== closestPlane) {{
                        selectedPlane.material.opacity = defaultOpacity;
                    }}
                    // Select new plane and dim others
                    selectedPlane = closestPlane;
                    dimAllLayers();
                    selectedPlane.material.opacity = selectedOpacity;
                }}
            }} else {{
                // Double-clicked elsewhere - deselect and restore
                selectedPlane = null;
                hoveredPlane = null;
                restoreAllLayers();
            }}
        }}

        window.addEventListener('mousemove', onMouseMove, false);
        window.addEventListener('dblclick', onDoubleClick, false);

        // Position camera looking from directly south, ~10 degrees higher
        const camDist = lonSpan * scale * 0.8;
        camera.position.set(0, camDist * 0.9, -camDist * 1.2);
        controls.target.set(0, timeScale * 0.5, 0);
        controls.update();

        // Add ambient light
        scene.add(new THREE.AmbientLight(0xffffff, 1));

        // Animation loop
        function animate() {{
            requestAnimationFrame(animate);
            controls.update();
            renderer.render(scene, camera);
        }}
        animate();

        // Handle resize
        window.addEventListener('resize', () => {{
            camera.aspect = window.innerWidth / window.innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(window.innerWidth, window.innerHeight);
        }});
    </script>
</body>
</html>'''

        return html

    def render_static(self, width: int = 1200, height: int = 800) -> Image.Image:
        """Render a static image - placeholder for Three.js."""
        img = Image.new('RGB', (width, height), color='#f5f0e6')
        return img
