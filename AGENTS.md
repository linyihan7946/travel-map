# Travel Map - 项目开发规范

## 项目概述

一个基于 Python 的旅行地图生成器，从 YAML 配置文件生成交互式 HTML 地图和静态 PNG 导出图。支持多种视觉风格、行政区划高亮，以及用于自由文本输入的 Web 界面。

## 快速参考

| 命令 | 用途 |
|---|---|
| `travel-map serve` | 启动 Web 界面（http://127.0.0.1:8000） |
| `travel-map generate trip.yml` | 从 YAML 配置生成地图 |
| `travel-map preview trip.yml` | 在浏览器中预览地图 |
| `travel-map from-photos ./photos` | 从带 GPS 信息的照片自动生成配置 |
| `python -m travel_map.web` | 启动 Web 服务器（开发模式） |

## 项目架构

```
travel_map/
├── cli.py          # Click 命令行：generate、preview、from-photos、serve
├── config.py       # TravelConfig 数据类、Location、Route、YAML 解析
├── geo.py          # 行政区划解析（DataV API）+ Nominatim 地理编码
├── llm.py          # 百炼/DashScope LLM 客户端（OpenAI 兼容模式）
├── photos.py       # 照片 EXIF/GPS 信息提取
├── web.py          # Flask Web 应用：/（表单页）、/api/generate（POST）
├── templates/
│   └── index.html  # Web UI 表单页（响应式，支持移动端）
└── styles/
    ├── __init__.py # STYLES 样式注册表
    ├── base.py     # BaseRenderer 抽象基类、区域几何体加载、边界逻辑
    ├── pins.py     # PinsRenderer（默认样式，支持导出按钮）
    ├── arcs.py     # ArcsRenderer（大圆弧连线）
    ├── indiana.py  # IndianaJonesRenderer（复古/棕褐色调）
    ├── worldline.py        # WorldlineRenderer（Plotly 三维时空可视化）
    └── worldline_threejs.py # WorldlineThreejsRenderer（Three.js）
```

## 核心数据流

### CLI 路径（YAML → 地图）
```
YAML 文件 → TravelConfig.from_yaml() → STYLES[config.style](config) → render_interactive() 或 render_static()
```

### Web UI 路径（自由文本 → 地图）
```
POST /api/generate {region, attractions}
  → llm.parse() 或 llm.normalize_region()  # LLM 文本规范化（可选）
  → geo.resolve_region()                    # DataV 行政区划边界查询
  → llm.geocode() 或 geo.nominatim_geocode()  # 景点经纬度解析
  → TravelConfig(regions=[临时.geojson])    # 用解析结果构建配置
  → PinsRenderer.render_interactive()       # 复用现有渲染器
```

## 外部服务

| 服务 | 用途 | 需要 Key |
|---|---|---|
| 阿里 DataV（`geo.datav.aliyun.com`） | 中国行政区划边界 GeoJSON | 否 |
| 阿里百炼（`coding.dashscope.aliyuncs.com`） | LLM 文本理解 + 景点地理编码 | 是（`DASHSCOPE_API_KEY`） |
| OSM Nominatim | 景点地理编码兜底 | 否 |

## 开发约定

### 1. 修改代码后必须重启服务
**每次修改 Python 源码后，必须重启 `travel-map serve`。** Flask 开发服务器不会对所有变更自动重载（尤其是导入结构、`.env`、依赖变更）。操作步骤：
```bash
# 停止已有服务
for pid in $(lsof -ti:8000 2>/dev/null); do kill -9 $pid 2>/dev/null; done
# 重新启动
python -m travel_map.web &
sleep 2
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/
```

### 2. 安全规范
- **禁止提交 `.env` 或 API Key** — `.env` 已在 `.gitignore` 中
- `DASHSCOPE_API_KEY` 仅从环境变量读取，`.env` 为本地回退
- Bearer Token 禁止写入日志或嵌入 HTML

### 3. 编码规范
- 所有文件 I/O 使用 `encoding="utf-8"`（Windows 默认 GBK 会破坏中文）
- YAML、JSON、HTML、GeoJSON 统一 UTF-8

### 4. 地图边界逻辑
- `_get_bounds()` — 宽边界（2° padding），用于交互式地图视口适配
- `_get_export_bounds()` — 紧边界（仅区域），用于静态 PNG 导出
- 配置了 regions 时，导出使用区域边界；否则回退到 `_get_bounds()`

### 5. 行政区划解析链
```
输入："云南" 或 "云南,昆明,五华区" 或 "云南/昆明/五华区"
  → _parse_region_chain() 按 ,// /空格 拆分
  → ancestors=["云南省", "昆明市"], target="五华区"
  → DataV: 100000_full → 省 adcode → 市 adcode → 区县 adcode
  → geojson?code={adcode} 返回边界 MultiPolygon
```

### 6. LLM 优雅降级
- LLM 调用包裹 `try/except` — 失败时静默回退到原始输入 + Nominatim
- 无 LLM Key → 整条管道仍可用（无 Key 模式）
- Key 无效/过期 → 同样降级，永不返回 500

## 测试模式

### 行政区划解析
```python
from travel_map.geo import resolve_region
r = resolve_region("云南省")
assert r["adcode"] == 530000
assert r["level"] == "province"
```

### Flask 测试客户端
```python
from travel_map.web import app
client = app.test_client()
resp = client.post("/api/generate", json={"region": "云南", "attractions": "昆明\n大理"})
assert resp.status_code == 200
```

### Playwright 端到端
```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto("http://127.0.0.1:8000/")
    page.fill("#region", "云南")
    page.fill("#attractions", "昆明\n大理")
    page.click("#go")
    page.wait_for_selector("#result")
```

## 添加新样式

1. 创建 `travel_map/styles/new_style.py`
2. 继承 `BaseRenderer`，实现 `render_interactive()` 和 `render_static()`
3. 在 `travel_map/styles/__init__.py` 注册：
   ```python
   STYLES["new_style"] = NewStyleRenderer
   ```
4. 如新增配置字段，同步更新 `config.py` 文档字符串

## 依赖清单

| 包 | 用途 |
|---|---|
| folium | 交互式 Leaflet 地图 |
| matplotlib + cartopy | 静态 PNG 地图（OSM 瓦片底图） |
| Pillow | 图像处理、HEIC 支持 |
| PyYAML | YAML 配置解析 |
| click | CLI 命令行框架 |
| flask | Web 服务器 |
| requests | HTTP 客户端（LLM、DataV） |
| plotly | Worldline 三维时空可视化 |
| numpy | 数学运算 |
