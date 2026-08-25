"""YAML configuration parsing and validation."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Location:
    """A single location on the map."""

    name: str
    lat: float
    lon: float
    date: Optional[datetime] = None
    label: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Location":
        """Create a Location from a dictionary."""
        date = None
        if "date" in data and data["date"]:
            if isinstance(data["date"], datetime):
                date = data["date"]
            else:
                date = datetime.fromisoformat(str(data["date"]))

        return cls(
            name=data["name"],
            lat=float(data["lat"]),
            lon=float(data["lon"]),
            date=date,
            label=data.get("label"),
        )


@dataclass
class Route:
    """A route between two locations."""

    from_location: str
    to_location: str

    @classmethod
    def from_dict(cls, data: dict) -> "Route":
        """Create a Route from a dictionary."""
        return cls(
            from_location=data["from"],
            to_location=data["to"],
        )


# Default home location (Madison, Wisconsin)
DEFAULT_HOME = Location(
    name="Madison, WI",
    lat=43.0731,
    lon=-89.4012,
)


@dataclass
class TravelConfig:
    """Complete travel map configuration."""

    title: str
    locations: list[Location]
    style: str = "pins"
    output: str = "interactive"
    show_dates: bool = True
    date_format: str = "%b %d"
    show_labels: bool = False
    regions: list[str] = field(default_factory=list)
    export_button: bool = False
    routes: list[Route] = field(default_factory=list)
    home: Optional[Location] = None
    routes_from_home: bool = False
    trip_gap_days: int = 7  # Days apart to consider separate trips
    source_path: Optional[Path] = None  # Path of the config file (for resolving relative assets)

    def __post_init__(self):
        """Validate configuration after initialization."""
        valid_styles = ["pins", "arcs", "indiana_jones", "worldline", "worldline3d"]
        if self.style not in valid_styles:
            raise ValueError(f"Invalid style '{self.style}'. Must be one of: {valid_styles}")

        valid_outputs = ["static", "interactive"]
        if self.output not in valid_outputs:
            raise ValueError(f"Invalid output '{self.output}'. Must be one of: {valid_outputs}")

        if not self.locations:
            raise ValueError("At least one location is required")

    def get_home(self) -> Location:
        """Get the home location, using default if not specified."""
        return self.home if self.home else DEFAULT_HOME

    def _group_into_trips(self, locations: list[Location]) -> list[list[Location]]:
        """Group locations into trips based on date proximity.

        Locations within trip_gap_days of each other are considered the same trip.
        """
        from datetime import timedelta

        if not locations:
            return []

        # Sort by date
        sorted_locs = sorted(locations, key=lambda x: x.date)
        trips = []
        current_trip = [sorted_locs[0]]

        for i in range(1, len(sorted_locs)):
            prev_date = sorted_locs[i - 1].date
            curr_date = sorted_locs[i].date
            gap = (curr_date - prev_date).days

            if gap <= self.trip_gap_days:
                # Same trip - add to current
                current_trip.append(sorted_locs[i])
            else:
                # New trip - save current and start new
                trips.append(current_trip)
                current_trip = [sorted_locs[i]]

        # Don't forget the last trip
        trips.append(current_trip)
        return trips

    def get_routes(self) -> list[tuple[Location, Location]]:
        """Get routes as location pairs. Auto-generates from dates if not specified."""
        location_map = {loc.name: loc for loc in self.locations}

        # If routes_from_home is enabled, generate smart trip-based routes
        if self.routes_from_home:
            home = self.get_home()
            dated_locations = [loc for loc in self.locations if loc.date]

            if not dated_locations:
                # No dates - just draw from home to each location
                return [(home, loc) for loc in self.locations]

            # Group into trips
            trips = self._group_into_trips(dated_locations)
            routes = []

            for trip in trips:
                # Arc from home to first stop of trip
                routes.append((home, trip[0]))
                # Sequential arcs within the trip
                for i in range(len(trip) - 1):
                    routes.append((trip[i], trip[i + 1]))

            return routes

        if self.routes:
            # Use explicit routes
            result = []
            for route in self.routes:
                from_loc = location_map.get(route.from_location)
                to_loc = location_map.get(route.to_location)
                if from_loc and to_loc:
                    result.append((from_loc, to_loc))
            return result

        # Auto-generate routes from chronological order
        dated_locations = [loc for loc in self.locations if loc.date]
        if len(dated_locations) < 2:
            return []

        sorted_locations = sorted(dated_locations, key=lambda x: x.date)
        return [(sorted_locations[i], sorted_locations[i + 1])
                for i in range(len(sorted_locations) - 1)]

    @classmethod
    def from_dict(cls, data: dict) -> "TravelConfig":
        """Create a TravelConfig from a dictionary."""
        locations = [Location.from_dict(loc) for loc in data.get("locations", [])]
        routes = [Route.from_dict(r) for r in data.get("routes", [])]

        # Parse home location if provided
        home = None
        if "home" in data and data["home"]:
            home = Location.from_dict(data["home"])

        return cls(
            title=data.get("title", "My Travel Map"),
            locations=locations,
            style=data.get("style", "pins"),
            output=data.get("output", "interactive"),
            show_dates=data.get("show_dates", True),
            date_format=data.get("date_format", "%b %d"),
            show_labels=data.get("show_labels", False),
            regions=data.get("regions", []),
            export_button=data.get("export_button", False),
            routes=routes,
            home=home,
            routes_from_home=data.get("routes_from_home", False),
            trip_gap_days=data.get("trip_gap_days", 7),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TravelConfig":
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        config = cls.from_dict(data)
        config.source_path = path
        return config
