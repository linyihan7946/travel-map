from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_real_wsgi_entrypoint_and_cjk_font():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "fonts-wqy-microhei" in dockerfile
    assert "mirrors.cloud.tencent.com" in dockerfile
    assert "--only-binary=:all:" in dockerfile
    assert "0.0.0.0:8000" in dockerfile
    assert "travel_map.web:app" in dockerfile
    assert '"--timeout", "180"' in dockerfile


def test_compose_passes_runtime_configuration_without_baking_env_file():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["app"]

    assert service["ports"] == ["8000:8000"]
    assert any(item.startswith("DASHSCOPE_API_KEY=") for item in service["environment"])
    assert "TRAVEL_MAP_TILE_TIMEOUT=8" in service["environment"]
    assert service["healthcheck"]["test"][0:2] == ["CMD", "python"]

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in dockerignore
    assert "*.html" not in dockerignore


def test_production_requirements_cover_eagerly_imported_styles():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "plotly>=" in requirements
    assert "kaleido>=" in requirements
