# ① 地图复现层（MAP_REPLAY）

> **一句话**：让地图底图、坐标标定、图标 与真实 Dota 地图正确对齐。

## 边界（只做这个）
- 底图提取/解码（官方 overview 纹理，`dota_737.vpk` / `pak01_dir.vpk`）
- 世界坐标 → 像素标定（`default_ents.vents` 解析 + 泉水锚定）
- 塔/野点/双生门/遗迹/泉水/眼位 的图标与标注
- 矢量地图图标库（SVG）

**明确不做的**：
- 不分析比赛数据、不算经济/胜率。
- 不回答"这场谁赢了"这类数据问题。
- 不修数据解析器（那是数据层）。

## 已有产物
- `opendota_analysis/map_annotations.py` —— 权威坐标 + 标定常量（`CALIB_K/OFFX/REF_Y`）
- `opendota_analysis/map_icons.py` —— 矢量 SVG 图标库
- `opendota_analysis/map_background.py` —— 底图加载 + `map_to_px`
- `opendota_analysis/assets/dota_map_1024.png` —— 权威官方 7.37 底图
- `MAP_PARSING.md` —— **可重复解析流程**（每次 Dota 大版本更新照着跑）

## 已知待修项（挂起，待授权）
- **BUG：野点绝对位置错误**
  - 现象：若干野点黄圈标注位置与真实中立营地不符（多处红框确认）。
  - 疑似根因：`default_ents.vents` 里 `npc_dota_neutral_spawner` 与命名 trigger `neutralcamp_good/evil_N` 的**配对错误**。当前用"最近距离"配对，但部分 spawner 与 trigger 相距 600-800 单位（不可靠）。
  - 方向：改用更可靠配对（如按 hammeruniqueid / 命名约定），或直接核对每个野点的真实世界坐标。
  - 状态：已记录，未修复。

## 交接协议
- **输出**：`map_annotations.py`（坐标+标定）、`map_icons.py`（图标）、`assets/dota_map_1024.png`、`MAP_PARSING.md`。
- **向数据层提供**：坐标标定常量，供其把地图某点换算成世界坐标做热图/标注。
- **向总控提供**：地图层进度 + 待修项清单。

## 每次 Dota 大版本更新（重做流程）
见 `MAP_PARSING.md` 第 7 节检查清单：重解码底图 → 重解析 `default_ents.vents` → 重标 `CALIB_*` → 验证。
