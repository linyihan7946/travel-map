"""Map style renderers."""

from .base import BaseRenderer
from .pins import PinsRenderer
from .arcs import ArcsRenderer
from .indiana import IndianaJonesRenderer
from .worldline import WorldlineRenderer
from .worldline_threejs import WorldlineThreejsRenderer

STYLES = {
    "pins": PinsRenderer,
    "arcs": ArcsRenderer,
    "indiana_jones": IndianaJonesRenderer,
    "worldline": WorldlineRenderer,
    "worldline3d": WorldlineThreejsRenderer,
}

__all__ = ["BaseRenderer", "PinsRenderer", "ArcsRenderer", "IndianaJonesRenderer", "WorldlineRenderer", "WorldlineThreejsRenderer", "STYLES"]
