# analysis/ — 第 4 层：特征提取 / 跨场次聚合层

对应 ARCHITECTURE.md 第 4 层与 §8 第 5/6 步。这里的代码**只做查询与聚合**，
运行在解析层产出的通用三表（`entity_snapshots` / `game_events` /
`player_identity`）之上，**不允许修改解析器或表结构**——新增分析维度=新增查询，
这正是"极大可延展性"要验证的核心假设（第 5 步用位置热区、第 6 步用视野守卫分别验证）。

## 用法

```powershell
# 一台机器上的所有场次库都传进来即可（支持多 db / 多 match）
python analysis/run_analysis.py 8592126358.db 8979891001.db [--cell 250] [--out analysis/output]
python analysis/ward_analysis.py 8592126358.db 8979891001.db [--team 2|3|all]
```

## 现状

| 查询 | 输入表 | 说明 |
|---|---|---|
| `英雄位置热区`（§8 第5步主交付物） | `entity_snapshots` | 按队伍聚合整场位置为密度网格：逐场网格 CSV、ASCII dominance 图（R/D/o），跨场 pooled 网格/图。每场按采样总数归一化后跨场平均，避免时长差异偏置 |
| `购买节奏`（§8 第5步附带） | `game_events` + `player_identity` | 每场按 5 分钟桶统计双方购买次数；只把"0 秒起录"场次纳入跨场 pooled |
| `守卫插眼分布`（§8 第6步验证查询） | `game_events`（ward_placed/ward_destroyed） | 每队整场插眼位置分布：按 (队伍, observer/sentry) 计数、每队 ASCII 放置图（O/S/B）、排眼（deward）归属统计、插眼 CSV。演示"新增视野维度 = 纯第 4 层新查询" |

## 数据口径（与 §4.2 / §6.2 对齐的坑位）

- 只统计**有 player_slot 的英雄**：召唤物（类名以 `CDOTA_Unit_Hero_` 开头但
  `extra.player_slot` 为 NULL）会被 `json_extract(extra,'$.player_slot') IS NOT NULL`
  过滤掉，避免把兽王猪/鹰之类算成"第 11 个玩家"。
- 中途起录的场次（首个英雄采样 >300s，如 8979484553）时间轴带偏移：
  热区不受影响；购买节奏按 demo 内分钟数输出并明确标记，不进 pooled 时间聚合。
- 世界坐标窗口取 ±10000（真实地图约 ±9000），窗口外采样单独计数并告警。
- 守卫事件口径见 `dota_parse/src/parse.rs` 的 WardExtractor 注释与 ARCHITECTURE §6.6：
  `ward_placed` 由守卫单位实体 Created 事件产生（含坐标/队伍），
  `ward_destroyed` 由战斗日志 Death（target=守卫单位）产生（含排眼者/过期标记，无坐标）。

## 输出

写入 `analysis/output/`（该目录已在 .gitignore 中）：
`<db>_<match>_heatmap_<cell>.csv`、`pooled_heatmap_<cell>.csv`、
`pooled_purchases_5min.csv`、`<db>_<match>_ward_placed.csv`，
加上控制台 dominance/放置图与逐场统计。
