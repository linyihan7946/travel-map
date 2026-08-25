"""Command-line interface for travel-map."""

import webbrowser
from pathlib import Path
import tempfile

import click

from .config import TravelConfig
from .styles import STYLES


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Generate beautiful travel maps from YAML configuration."""
    pass


@cli.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option(
    "-o", "--output",
    type=click.Path(),
    help="Output file path. Extension determines format (.html or .png/.svg).",
)
@click.option(
    "-s", "--style",
    type=click.Choice(["pins", "arcs", "indiana_jones", "worldline", "worldline3d"]),
    help="Override the style from config.",
)
@click.option(
    "-f", "--format",
    "output_format",
    type=click.Choice(["static", "interactive"]),
    help="Override the output format from config.",
)
@click.option(
    "--width",
    type=int,
    default=1200,
    help="Width for static output (default: 1200).",
)
@click.option(
    "--height",
    type=int,
    default=800,
    help="Height for static output (default: 800).",
)
def generate(config_file, output, style, output_format, width, height):
    """Generate a map from a YAML configuration file."""
    # Load config
    config = TravelConfig.from_yaml(config_file)

    # Apply overrides
    if style:
        config.style = style
    if output_format:
        config.output = output_format

    # Determine output path
    if not output:
        config_path = Path(config_file)
        ext = ".html" if config.output == "interactive" else ".png"
        output = config_path.with_suffix(ext)

    # Get renderer
    renderer_class = STYLES.get(config.style)
    if not renderer_class:
        raise click.ClickException(f"Unknown style: {config.style}")

    renderer = renderer_class(config)

    # Generate map
    click.echo(f"Generating {config.style} map ({config.output})...")

    if config.output == "interactive":
        html = renderer.render_interactive()
        output_path = Path(output)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".html")
        output_path.write_text(html, encoding="utf-8")
        click.echo(f"Saved to: {output_path}")
    else:
        img = renderer.render_static(width=width, height=height)
        output_path = Path(output)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".png")
        img.save(output_path)
        click.echo(f"Saved to: {output_path}")


def _generate_embed_html(renderer) -> str:
    """Generate embeddable HTML snippet from a Folium map.

    Returns HTML that can be inserted into an existing page's <body>.
    Includes necessary CSS/JS and the map container.
    """
    import html
    import re

    # Folium's _repr_html_() returns an iframe with srcdoc containing escaped HTML
    full_output = renderer.render_interactive()

    # Extract the srcdoc content from the iframe
    srcdoc_match = re.search(r'srcdoc="([^"]*)"', full_output, re.DOTALL)
    if srcdoc_match:
        # Decode HTML entities to get the actual HTML
        map_html = html.unescape(srcdoc_match.group(1))
    else:
        # Fallback - maybe it's already plain HTML
        map_html = full_output

    # Extract head content (CSS/JS includes)
    head_match = re.search(r'<head>(.*?)</head>', map_html, re.DOTALL)
    head_content = head_match.group(1).strip() if head_match else ""

    # Extract body content (map div)
    body_match = re.search(r'<body>(.*?)</body>', map_html, re.DOTALL)
    body_content = body_match.group(1).strip() if body_match else ""

    # Extract scripts after </body> (map initialization)
    # Folium puts the map setup scripts after the body
    after_body_match = re.search(r'</body>(.*?)</html>', map_html, re.DOTALL)
    scripts_content = after_body_match.group(1).strip() if after_body_match else ""

    # Build embeddable snippet with clear sections
    embed_html = f"""<!-- ========== TRAVEL MAP EMBED - START ========== -->

<!-- Add these to your <head> section: -->
{head_content}

<!-- Add this to your <body> where you want the map: -->
<div class="travel-map-container" style="width: 100%; height: 600px; position: relative;">
{body_content}
</div>

<!-- Map initialization scripts (add at end of <body> or after the map container): -->
{scripts_content}

<!-- ========== TRAVEL MAP EMBED - END ========== -->
"""
    return embed_html


@cli.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option(
    "-s", "--style",
    type=click.Choice(["pins", "arcs", "indiana_jones", "worldline", "worldline3d"]),
    help="Override the style from config.",
)
@click.option(
    "-o", "--output",
    type=click.Path(),
    help="Output HTML file path. If not specified, saves to examples/{style}_example.html",
)
@click.option(
    "--embed",
    is_flag=True,
    help="Generate embeddable HTML snippet (for inserting into another page).",
)
def preview(config_file, style, output, embed):
    """Preview a map in your default browser."""
    config = TravelConfig.from_yaml(config_file)

    if style:
        config.style = style

    # Force interactive for preview
    config.output = "interactive"

    renderer_class = STYLES.get(config.style)
    if not renderer_class:
        raise click.ClickException(f"Unknown style: {config.style}")

    renderer = renderer_class(config)

    click.echo(f"Generating {config.style} {'embed snippet' if embed else 'preview'}...")

    if embed:
        html = _generate_embed_html(renderer)
        suffix = "_embed.html"
    else:
        html = renderer.render_interactive()
        suffix = "_example.html"

    # Determine output path
    if output:
        output_path = Path(output)
    else:
        # Save to examples folder with standard name
        config_path = Path(config_file)
        examples_dir = config_path.parent
        style_name = config.style.replace("_", "-")
        output_path = examples_dir / f"{style_name}{suffix}"

    # Save HTML file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    click.echo(f"Saved to: {output_path}")

    # Also generate iframe wrapper version
    iframe_path = output_path.with_stem(output_path.stem + "_iframe")
    iframe_html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{config.title or "Travel Map"}</title>
    <style>
        body {{ margin: 0; padding: 0; }}
        iframe {{ width: 100%; height: 100vh; border: none; }}
    </style>
</head>
<body>
    <iframe src="{output_path.name}" allowfullscreen></iframe>
</body>
</html>
'''
    with open(iframe_path, "w", encoding="utf-8") as f:
        f.write(iframe_html)
    click.echo(f"Iframe version: {iframe_path}")

    if not embed:
        click.echo(f"Opening preview in browser...")
        webbrowser.open(f"file://{output_path.absolute()}")
    else:
        click.echo("Embed snippet ready - copy the contents into your webpage.")


@cli.command()
@click.argument("photo_dir", type=click.Path(exists=True))
@click.option(
    "-o", "--output",
    type=click.Path(),
    default="trip.yml",
    help="Output YAML file (default: trip.yml).",
)
@click.option(
    "-t", "--title",
    default="My Trip",
    help="Title for the trip (default: 'My Trip').",
)
@click.option(
    "-s", "--style",
    type=click.Choice(["pins", "arcs", "indiana_jones", "worldline", "worldline3d"]),
    default="arcs",
    help="Default style for the config (default: arcs).",
)
def from_photos(photo_dir, output, title, style):
    """Generate a YAML config from geotagged photos."""
    from .photos import extract_locations_from_photos

    click.echo(f"Scanning photos in {photo_dir}...")

    locations = extract_locations_from_photos(photo_dir)

    if not locations:
        raise click.ClickException("No geotagged photos found!")

    click.echo(f"Found {len(locations)} geotagged photos.")

    # Generate YAML
    import yaml

    config_data = {
        "title": title,
        "style": style,
        "output": "interactive",
        "show_dates": True,
        "date_format": "%b %d, %Y",
        "locations": locations,
    }

    output_path = Path(output)
    with open(output_path, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

    click.echo(f"Config saved to: {output_path}")
    click.echo("You can edit this file to customize location names and add labels.")


if __name__ == "__main__":
    cli()
