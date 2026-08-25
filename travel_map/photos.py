"""Extract geolocation and date information from photos."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS, IFD

# Register HEIC support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass  # HEIC support not available


def _get_exif_data(image: Image.Image) -> tuple[dict, dict]:
    """Extract EXIF data from an image.

    Returns:
        Tuple of (exif_data dict, gps_data dict).
    """
    exif_data = {}
    gps_data = {}

    # Try newer getexif() API first (works with HEIC)
    try:
        exif = image.getexif()
        if exif:
            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)
                exif_data[tag] = value

            # Get GPS IFD
            try:
                gps_ifd = exif.get_ifd(IFD.GPSInfo)
                if gps_ifd:
                    for tag_id, value in gps_ifd.items():
                        tag = GPSTAGS.get(tag_id, tag_id)
                        gps_data[tag] = value
            except (KeyError, AttributeError):
                pass

            # Also try EXIF IFD for date info
            try:
                exif_ifd = exif.get_ifd(IFD.Exif)
                if exif_ifd:
                    for tag_id, value in exif_ifd.items():
                        tag = TAGS.get(tag_id, tag_id)
                        if tag not in exif_data:
                            exif_data[tag] = value
            except (KeyError, AttributeError):
                pass

    except (AttributeError, KeyError):
        pass

    # Fallback to older _getexif() API
    if not exif_data:
        try:
            info = image._getexif()
            if info:
                for tag_id, value in info.items():
                    tag = TAGS.get(tag_id, tag_id)
                    exif_data[tag] = value

                # Extract GPS from old-style EXIF
                gps_info = exif_data.get("GPSInfo", info.get(34853))
                if gps_info and isinstance(gps_info, dict):
                    for key, value in gps_info.items():
                        tag = GPSTAGS.get(key, key)
                        gps_data[tag] = value
        except (AttributeError, KeyError):
            pass

    return exif_data, gps_data


def _convert_to_degrees(value) -> float:
    """Convert GPS coordinates to degrees."""
    try:
        d = float(value[0])
        m = float(value[1])
        s = float(value[2])
        return d + (m / 60.0) + (s / 3600.0)
    except (TypeError, IndexError, ZeroDivisionError):
        return 0.0


def _get_coordinates(gps_data: dict) -> Optional[tuple[float, float]]:
    """Extract latitude and longitude from GPS data."""
    if not gps_data:
        return None

    lat = gps_data.get("GPSLatitude")
    lat_ref = gps_data.get("GPSLatitudeRef")
    lon = gps_data.get("GPSLongitude")
    lon_ref = gps_data.get("GPSLongitudeRef")

    if not all([lat, lat_ref, lon, lon_ref]):
        return None

    lat_deg = _convert_to_degrees(lat)
    lon_deg = _convert_to_degrees(lon)

    if lat_ref == "S":
        lat_deg = -lat_deg
    if lon_ref == "W":
        lon_deg = -lon_deg

    return (lat_deg, lon_deg)


def _get_date_taken(exif_data: dict) -> Optional[datetime]:
    """Extract date taken from EXIF data."""
    # Try different date fields
    date_fields = ["DateTimeOriginal", "DateTime", "DateTimeDigitized"]

    for field in date_fields:
        date_str = exif_data.get(field)
        if date_str:
            try:
                # EXIF date format: "YYYY:MM:DD HH:MM:SS"
                return datetime.strptime(str(date_str), "%Y:%m:%d %H:%M:%S")
            except (ValueError, TypeError):
                continue

    return None


def extract_photo_metadata(photo_path: Path) -> Optional[dict]:
    """Extract geolocation and date from a single photo.

    Args:
        photo_path: Path to the photo file.

    Returns:
        Dictionary with lat, lon, date, and filename, or None if no GPS data.
    """
    try:
        with Image.open(photo_path) as img:
            exif_data, gps_data = _get_exif_data(img)
            coords = _get_coordinates(gps_data)

            if not coords:
                return None

            date_taken = _get_date_taken(exif_data)

            return {
                "lat": round(coords[0], 6),
                "lon": round(coords[1], 6),
                "date": date_taken,
                "filename": photo_path.name,
            }
    except Exception:
        return None


def extract_locations_from_photos(
    photo_dir: str | Path,
    extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".tiff", ".heic"),
) -> list[dict]:
    """Extract location data from all photos in a directory.

    Args:
        photo_dir: Directory containing photos.
        extensions: Tuple of file extensions to process.

    Returns:
        List of location dictionaries for YAML config.
    """
    photo_dir = Path(photo_dir)
    locations = []
    seen_coords = set()  # Avoid duplicate locations

    # Find all image files
    photo_files = []
    for ext in extensions:
        photo_files.extend(photo_dir.glob(f"*{ext}"))
        photo_files.extend(photo_dir.glob(f"*{ext.upper()}"))

    for photo_path in sorted(photo_files):
        metadata = extract_photo_metadata(photo_path)
        if metadata:
            # Round coords to avoid near-duplicates
            coord_key = (round(metadata["lat"], 3), round(metadata["lon"], 3))

            if coord_key not in seen_coords:
                seen_coords.add(coord_key)

                location = {
                    "name": photo_path.stem.replace("_", " ").replace("-", " ").title(),
                    "lat": metadata["lat"],
                    "lon": metadata["lon"],
                }

                if metadata["date"]:
                    location["date"] = metadata["date"].strftime("%Y-%m-%d")

                locations.append(location)

    # Sort by date if available
    locations.sort(key=lambda x: x.get("date", "9999-99-99"))

    return locations
