"""Alibaba Bailian (DashScope) LLM client for natural-language understanding.

Uses DashScope's OpenAI-compatible endpoint. The key is read from the
``DASHSCOPE_API_KEY`` environment variable, optionally loaded from a ``.env``
file at the project root (which is gitignored and never committed).
"""

import json
import os
import re
from pathlib import Path

import requests

BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
DEFAULT_MODEL = "qwen3.7-plus"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_PARSE_SYSTEM = (
    "你是中文旅游地图助手。从用户输入中提取目标地区和景点列表。"
    "只返回 JSON，结构必须为："
    '{"region_name": "目标地区规范全称", "ancestors": ["上级地区名，从高到低，可为空数组"], "attractions": ["景点名1", "景点名2"]}。'
    'region_name 用规范全称（如"云南省""大理白族自治州""昆明市""五华区"）。'
    'ancestors 是从省到县、依次列出该地区的所有上级行政区（不含地区本身），不确定就留空数组。'
    "attractions 把用户输入的景点逐个切分并规范命名。"
)

_GEOCODE_SYSTEM = (
    "你是地理编码助手。为每个景点返回经纬度。只返回 JSON，结构必须为："
    '{"attractions": [{"name": "景点名", "lat": 25.0, "lon": 102.0, "label": "简短说明"}]}。'
    "经纬度用十进制度，lat 在 -90..90、lon 在 -180..180。"
    "无法确定经纬度的景点不要放进结果。label 简短（10 字内）或空字符串。"
)


class LLMUnavailable(RuntimeError):
    """Raised when no DashScope API key is configured."""


def _load_dotenv() -> None:
    """Load ``.env`` from the project root into os.environ (no-op if absent)."""
    env_file = _PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def api_key() -> str | None:
    """Return the DashScope API key, or None if not configured."""
    _load_dotenv()
    return os.environ.get("DASHSCOPE_API_KEY")


def model() -> str:
    """Return the model id to use, honoring ``BAILIAN_MODEL`` if set."""
    _load_dotenv()
    return os.environ.get("BAILIAN_MODEL") or DEFAULT_MODEL


def _extract_json(content: str):
    """Parse JSON from a model reply, tolerating surrounding prose."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Fall back to the first {...} block.
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"模型未返回有效 JSON：{content[:200]}")


def _chat(messages: list[dict], *, json_mode: bool = True, enable_search: bool = False):
    """Send a chat completion request to DashScope and return the reply."""
    key = api_key()
    if not key:
        raise LLMUnavailable(
            "未配置 DASHSCOPE_API_KEY。请在项目根目录创建 .env 并写入 "
            "DASHSCOPE_API_KEY=你的百炼Key（该文件已加入 .gitignore，不会上传）。"
        )

    payload = {"model": model(), "messages": messages, "temperature": 0.2}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if enable_search:
        payload["enable_search"] = True

    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if resp.status_code == 401:
        raise LLMUnavailable(
            "DASHSCOPE_API_KEY 无效或已过期（百炼返回 401）。"
            "请到阿里云百炼控制台确认 Key 是否正确、是否已开启对应模型权限。"
        )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _extract_json(content) if json_mode else content


def parse(text: str) -> dict:
    """Parse a free-text blob into ``{region_name, ancestors, attractions}``."""
    messages = [
        {"role": "system", "content": _PARSE_SYSTEM},
        {"role": "user", "content": text},
    ]
    data = _chat(messages, json_mode=True)
    return {
        "region_name": data.get("region_name", ""),
        "ancestors": data.get("ancestors") or [],
        "attractions": data.get("attractions") or [],
    }


def normalize_region(region: str) -> dict:
    """Normalize a region name to ``{region_name, ancestors}``."""
    messages = [
        {"role": "system", "content": _PARSE_SYSTEM},
        {"role": "user", "content": f"地区：{region}（无景点）"},
    ]
    data = _chat(messages, json_mode=True)
    return {
        "region_name": data.get("region_name") or region,
        "ancestors": data.get("ancestors") or [],
    }


def geocode(names: list[str], region_name: str | None = None) -> list[dict]:
    """Geocode a list of attraction names into ``[{name, lat, lon, label}]``."""
    if not names:
        return []
    context = f"这些景点位于 {region_name}。" if region_name else ""
    messages = [
        {"role": "system", "content": _GEOCODE_SYSTEM},
        {"role": "user", "content": context + "景点列表：" + "、".join(names)},
    ]
    data = _chat(messages, json_mode=True, enable_search=True)
    return data.get("attractions") or []
