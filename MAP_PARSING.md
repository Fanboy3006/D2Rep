# 地图解析流程（每次 Dota 大版本更新重新跑一遍）

本文件记录：**如何从游戏安装包里解析出权威的地图底图 + 塔/野点/双生门/遗迹/泉水坐标，并用官方矢量图标把它们画到地图上**。所有坐标来自**游戏真实地图文件**（不是第三方静态数据），因此"塔/野点/眼位"与底图天然对齐。

> 适用：本机装有 Dota 2（Source 2 / Panorama）客户端，能找到 `game/dota` 目录。工具：ValveResourceFormat 的 `Source2Viewer-CLI.exe`（自包含，无需安装）+ Python 3 + PIL。

---

## 0. 前置：找到 Dota 2 安装目录

```
J:\SteamLibrary\steamapps\common\dota 2 beta\game\dota
```
下面用 `$DOTA` 指代该目录。关键文件：
- `maps\dota_737.vpk` —— **当前版本地图包**（数字 `737` 是地图版本，会随大版本更新而变，如 741/745…）
- `pak01_dir.vpk` —— 主资源包（含官方 overview 底图纹理）
- `maps\terrain_previews\dota_default_preview.vpk` —— 地形预览包（3D 网格，本次未用）

> **每次大版本更新要改的第一个地方**：确认 `maps\dota_<NNN>.vpk` 的 `<NNN>`，并同步 `opendota_analysis/map_annotations.py` 里的版本号注释。

---

## 1. 解码官方 overview 底图（→ 权威 1024×1024 底图）

官方 minimap 使用的是 `materials/overviews/dota_<NNN>` 纹理（Source 2 `.vtex_c` 容器）。用 VRF 解成 PNG：

```powershell
$vrf = "F:\D2Rep_project\dota_replay_analyzer\.tmp\vrf\Source2Viewer-CLI.exe"
$pak = "$DOTA\pak01_dir.vpk"
# 先列出 overview 纹理，确认当前版本号对应的 _psd_ 文件名
& $vrf -i $pak --vpk_list -f "materials/overviews/" | Select-String "dota_"
# 用当前版本名（例 dota_737）decode：
& $vrf -i $pak --vpk_filepath "materials/overviews/dota_737_psd_28d44696.vtex_c" -o .tmp\vtx737 --decompile
```

输出：`.tmp\vtx737\materials\overviews\dota_737_psd_28d44696.png`（1024×1024 RGBA）。
再把它转成 RGB 256 存为仓库底图：

```python
from PIL import Image
im = Image.open(".tmp/vtx737/materials/overviews/dota_737_psd_28d44696.png").convert("RGB")
im.save("opendota_analysis/assets/dota_map_1024.png")
```

> 说明：官方 minimap 底图原生分辨率就是 **1024×1024**（`dota_xxx_psd` 与 `dota_minimal_xxx_psd` 都是）。VRF 默认解出 base mip = 1024，这是游戏小地图的真实用途分辨率，无需更高。
> `dota_minimal_*` 是"极简"风格底图；我们用的是标准 `dota_*` 底图。

---

## 2. 解析地图实体坐标（→ 塔/野点/双生门/遗迹/泉水/眼位）

真正的坐标来自地图的**默认实体表** `maps/dota/entities/default_ents.vents_c`（编译的 VEntitySystem keyvalues）。同样在 `maps\dota_737.vpk` 里：

```powershell
& $vrf -i "$DOTA\maps\dota_737.vpk" --vpk_filepath "maps/dota/entities/default_ents.vents_c" -o .tmp\map_ents --decompile
```

输出：`.tmp\map_ents\maps\dota\entities\default_ents.vents`（约 4MB，文本 keyvalues）。

用 Python 按 `====N====` 分块，每块取 `classname` 与 `origin`（"x y z"）：

```python
import re
d = open(".tmp/map_ents/maps/dota/entities/default_ents.vents",
         encoding="utf-8", errors="replace").read()
parts = re.split(r'^====\d+====\s*$', d, flags=re.M)
for b in parts:
    cls = re.search(r'classname\s+"([^"]+)"', b)
    org = re.search(r'origin\s+"([^"]+)"', b)
    if cls and org:
        v = [float(x) for x in org.group(1).split()]
        print(cls.group(1), v[0], v[1], v[2])
```

**关键 `classname` 与用途**：

| classname | 数量 | 用途 |
|---|---|---|
| `npc_dota_neutral_spawner` | 28 | **中立野点**（刷怪点，即野点中心）。与命名 trigger `neutralcamp_good_N`/`neutralcamp_evil_N` **按 `spawner.volumename == trigger.targetname` 配对**（可靠；勿用 hammeruniqueid，其 id 不连续，会错配 23/28，见 `.tmp/diag_camps.py`）。`spawner` 上的 `neutraltype/pulltype/aggrotype` 是权威营地等级字段 |
| `npc_dota_tower` | 22 | **防御塔**（双方各 11 座，含 4 级塔） |
| `npc_dota_unit_twin_gate` | 2 | **双生门**（一正一反） |
| `npc_dota_fort` | 2 | **遗迹/远古**（双方 throne） |
| `ent_dota_fountain` | 2 | **泉水** |
| `npc_dota_roshan_spawner` | 1 | **Roshan 坑**刷怪点 |
| `npc_dota_watch_tower` | 4 | 岗哨塔 |
| `ent_dota_shop` / `trigger_shop` | 4+5 | 商店（基地/秘密商店） |
| `ward_spot_*` | 17 | **眼位**（官方标注的插眼点位） |
| `dota_item_rune_spawner_*` | 6 | 神符 |
| `ent_dota_neutral_item_stash` | 2 | 中立物品储藏处 |
| `npc_dota_mango_tree` | 2 | 芒果树 |

> 参考处理：`.tmp/parse_ents.py`、`.tmp/build_map_data.py`（本项目调试时用，可复用思路）。
> **权威配对脚本**：`.tmp/build_camps_authoritative.py` —— 用 `volumename` 配对、产出带 spawner 原点 / trigger 中心 / neutraltype / pulltype / aggrotype 的 `camps_auth.json` 与可直接粘贴的 `NEUTRAL_CAMPS` 字面量。
> 注意 `neutralcamp_good_6` / `neutralcamp_evil_7` 在此图没有对应 spawner（略去）。
> 野点坐标取 **spawner 原点**（刷怪点 + 野点中心，社区地图工具通用口径），而非 trigger 中心；本图 28/28 与 spawner 原点一致。部分 spawner 与自身 trigger 中心相距 600–800 单位是**地图固有**（该类营地 trigger 体积大 / spawner 放在拉怪点），并不代表配对错误。

---

## 3. 世界→像素映射标定（关键：让塔贴路）

**不要假设世界原点=图中心、也不要假设 ±9472 铺满整图。** 官方底图四周有黑色 out-of-bounds 边，playable 区域只在中央子框里。实测：
- playable 内容区像素约 `x:[46..980] y:[44..956]`，中心 (513,500)；
- **世界 (0,0) 实际映射到像素 (508.30, 504.54)**，不是 (512,512)。

最可靠的做法是用**两座泉水**做标定（泉水在底图上是极清晰的宝石地标，坐标已知）：

| 锚点 | 世界坐标 | 底图像素（肉眼读放大图） |
|---|---|---|
| 天辉泉水 | (−7456, −6938) | (141.5, 843.5) |
| 夜魇泉水 | (7408, 6848) | (872.75, 170.0) |

对这两个锚点做**各向同性最小二乘**（缩放 K 对 X、Y 相同），解出：

```
px = CALIB_OFFX + CALIB_K * x
py = CALIB_REF_Y - CALIB_K * y
```
结果（见 `.tmp/solve_calib.py`，残差 < 1.3px）：
- `CALIB_K ≈ 0.049038 px/world`
- `CALIB_OFFX ≈ 508.3019`
- `CALIB_REF_Y ≈ 504.5433`

这在 `opendota_analysis/map_annotations.py` 里以 `map_to_px()` / `CALIB_*` 常量给出。

> 换算成旧式 `WORLD_SPAN` 口径：`span_full = 1024/K ≈ 20881.8`（整图跨度）；权威 playable 边界 `±9472` 对应像素 43.8..972.8。opendota 的 replay 世界坐标与地图实体坐标共用同一世界空间，可直接用。

---

## 4. 矢量地图图标（官方符号自绘为 SVG）

游戏内的塔/野点/眼位/Roshan 小地图符号**是程序化矢量绘制**的，**没有独立 PNG 文件**。因此本方案自绘一套风格统一、语义清晰、可按队伍/状态着色的 SVG 图标：

模块：`opendota_analysis/map_icons.py`。
- `svg_tower(team, dead, tier)` —— 防御塔（绿/红尖塔+底座；摧毁变灰色圆环）
- `svg_camp(ctype)` —— 野点（0..2 普通怪剪影，3=远古金色皇冠/宝石）
- `svg_ward(sentry)` —— 眼位（蓝圆=插眼、蓝菱形=真眼）
- `svg_roshan()` —— 肉山（红圈+骷髅）
- `svg_ancient(team)` —— 遗迹（队伍色六边形盾）
- `svg_fountain(team)` —— 泉水（青色水滴）
- `svg_gate()` —— 双生门（紫色传送门）

每个返回一个 SVG 字符串；`map_icons.ICONS` 字典提供 `data:image/svg+xml;base64,...` 形式的 data-URI，直接塞进 `<img src=...>`（移动端 webview / iOS 预览也兼容）。

自检：`python opendota_analysis/map_icons.py` 生成 `.tmp/icon_sheet.html`（浏览器打开看全套图标）。

---

## 5. 接入 viewer

- **DOM / lite 版**：`opendota_analysis/export_match_viewer_dom.py`
  - `pct(x,y)` 用 `CALIB_*`（第 3 步），返回板块百分比；
  - 塔/野点/Roshan/眼位用 `map_icons` 的 SVG data-URI 渲染为 `<img>`。
- **Canvas 版**：`opendota_analysis/export_match_viewer.py`
  - JS `w2s()` 同样用 `CALIB_*`；模板里通过 `__CALIB_K/OFFX/REF_Y__` 注入。

生成：
```powershell
python opendota_analysis\export_match_viewer_dom.py 8822238357
python opendota_analysis\export_match_viewer.py    8822238357
```

---

## 6. 验证（每次必做）

1. 生成叠加图：把 28 野点 + 22 塔 + 双生门 + 遗迹 + Roshan + 泉水画到权威底图上，肉眼确认：
   - 塔沿三路走线、野点在丛林空地、Roshan 在下路野区坑、双生门在河边路口、泉水在基地角落。
   - 参考：`.tmp/affine_calib.png`、`.tmp/tower_road_check.png`、`.tmp/preview_vectors.png`。
2. 打开生成的 viewer，确认 SVG 图标渲染正常、位置与底图对齐。
3. 与游戏内 minimap 对照抽查一座塔、一个野点、一个眼位。

---

## 7. 每次大版本更新的检查清单

- [ ] 确认 `maps\dota_<NNN>.vpk` 版本号变化 → 更新底图 decode 命令 + `map_annotations.py` 注释。
- [ ] 重新 decode overview 底图（第 1 步），替换 `opendota_analysis/assets/dota_map_1024.png`。
- [ ] 重新解析 `default_ents.vents`（第 2 步），核对塔/野点数量与坐标是否变化（地图布局若重做，坐标会大改）。
- [ ] 若地图更名/布局变化，**重标 `CALIB_K/OFFX/REF_Y`**（第 3 步，用新泉水/遗迹锚点）。
- [ ] 跑第 6 步验证，确认塔贴合道路。
- [ ] 更新 `ARCHITECTURE.md` 的地图数据链段落 + git 提交。

---

## 已知限制 / 备注
- 底图用官方 1024×1024 overview；官方 minimap 原生分辨率就是 1024，无更高 base。
- "清楚到每一棵树"需从 3D world 网格（`world.vwrld_c`/`dota.vmap_c`）渲染，工作量大、正交投影与 minimap 姿态未必一致，本线未做（`maps/terrain_previews/*.vpk` 备选）。
- 莲花池在当前图的地图实体里没有独立实体；如需可另查脚本资源。
- **"完整全图 3D 不可达"系早期误判，已推翻**：早期只导出 `world.vwrld_c`（轻量入口）得到中心 ±270，误以为外围无网格。真相：真正世界入口是 **`maps/dota.vmap_c`**（Hammer 世界文件，95KB，解出完整文件树：`node000_lr0..lr4` 地形 tile + 全部地形/植被/地貌/建筑模型）。经 SourceIO 导入 **`maps/dota.vpk`**（2026-09-01 最新版，含 `maps/dota.vmap_c` 116KB）已解出 **全图 ±9000 的完整实例表**（见下"当前逆向进度与卡点"），全图真实数据已拿到。
- **Blender + SourceIO 只导出中心 ±270**：SourceIO 世界导入 + `load_placeholder` 后，场景 evaluated bbox 仍为 x/y ±270（外围 ±9472 不进 Blender 场景），故此路不能出全图（已弃；自建管线不依赖 Blender，**Blender 可卸载**）。
- **地图工厂管线（可复用）**：`opendota_analysis/map_factory.py` 从官方 overview 一条命令生成**高清增强标注底图**（超分放大+锐化+对比增强 + 权威塔/野点/远古/Roshan/双生门/遗迹/泉水标注），每次大版本更新/出图复用：
  ```
  python -m opendota_analysis.map_factory --size 4096 --out dist/map_base_4096.png
  ```
  组件 API：`build_base(size)` / `annotate(im, ...)` / `render(size, out)`。增强底图资产（2048）：`opendota_analysis/assets/dota_map_2048_enhanced.png`。

---

## 当前逆向工程进度与卡点（总控关注，最新）

**目标**：从 `maps/dota.vmap_c` 逆向 Dota 全图真实地貌，正交俯视渲染出忠实全图，叠加权威标注，做成可复用管线。

**已确认的数据资产（全图真实数据已齐备，坐标已验证，天辉泉水=(-7512,-6936)）**：
- `dist/props_instances.json` —— SourceIO 从 `dota.vmap` 解出的 **1699 个实体实例**，每个含 `prop_path`(模型路径) + `entity.m_vTransform`(世界变换，Hammer 单位) + `scale`。来源：`tools/dump_props.py`（SourceIO 导入后一键 dump，可复用）。
- `dist/instance_list.json` —— **817 个唯一模型 + 每个的全图实例坐标**（外围覆盖 x[-9021..8992] y[-8808..8640]≈±9000）。来源：`.tmp/build_instance_list.py`。
- 自建正交俯视渲染器：`.tmp/render_patch.py` / `compose_tiles.py` / `compose_center.py`（已能渲单 tile 真实草地+河 → `.tmp/render_patch.png`）。
- VRF（`.tmp/vrf/Source2Viewer-CLI.exe`）可导出每个唯一模型的 `.gltf`（几何+贴图）。

**B 路线（黑背景 + 纯真实几何，不叠官方风格化）已确认，当前状态**：
- 已渲：**坡道/高地真实几何** → `dist/highground_shape2.png`（可靠，黑背景纯几何）。
- 关键数据：**`dist/instance_list_full.json`** —— 从**原始 `node000.vwnod`** 解析的全图 ±9000 实例清单（66 模型：水159 / cliff崖99 / 岩705 / 晶111 / 石77 / 其它；x[-9021..8992] y[-8808..8640]）。来源 `.tmp/parse_vwnod.py`（聚合非单位 3×4 矩阵的 translation）。`full_features_map.png` / `composed_features_map.png` 用它渲（坐标对；后一张官方底图仅示意、非最终）。
- 管线脚本：`tools/dump_props.py`、`tools/gameplay_map.py`、`.tmp/render_geom_map.py`、`.tmp/render_tree_cliff.py`、`.tmp/parse_vwnod.py`、`.tmp/render_full_features.py` 等（逐类导出几何 + 渲真实形状）。

**每一类要素各自的坑（逐类攻坚）**：
1. **坡道/高地** `ramp_*` —— ✅ 已通；模型在 `pak01 models/props_structures/ramp_*.vmdl_c`。
2. **悬崖** `cliff_wall00*`、岩/晶 —— 几何能导出（`dota_737.vpk` `worldnodes\node000_prop*.vmdl_c`），但渲出**微小/近乎黑** → **坐标基准/形状大小需校正**（fragment 矩阵 translation 解析为 x,y,z，渲染 north=ty，但 y/z 轴仍需确认）。
3. **树** `tree_oak_*` —— 几何能导出，但**实例缺失**：`parse_vwnod.py` 没把树对象关联进来（解析器 bug，需修；树的场景对象含大矩阵但关联失败）。
4. **河道/水流** `water_/flow/river` —— 只有坐标点(159)，**河道真实几何 / 加速段**待攻（模型在 vmap 聚合，需定位导出）。

**结论**：每一类 = "模型定位 + 坐标校正 + 形状渲染" 的独立攻坚；坡道已通，其余（崖/岩/晶/树/河）逐类处理。**长周期**。
