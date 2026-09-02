# analysis/ — 第 4 层：特征提取 / 跨场次聚合层

对应 ARCHITECTURE.md 第 4 层与 §8 第 5 步。这里的代码**只做查询与聚合**，
运行在解析层产出的通用三表（`entity_snapshots` / `game_events` /
`player_identity`）之上，**不允许修改解析器或表结构**——新增分析维度=新增查询，
这正是"极大可延展性"要验证的核心假设。

## 用法

```powershell
# 一台机器上的所有场次库都传进来即可（支持多 db / 多 match）
python analysis/run_analysis.py 8592126358.db 8979891001.db [--cell 250] [--out analysis/output]
```

## 现状（§8 第5步首个实现）

| 查询 | 输入表 | 说明 |
|---|---|---|
| `英雄位置热区`（主交付物） | `entity_snapshots` | 按队伍（radiant/dire）聚合整场位置为密度网格：每场一张网格 CSV、一场一张 ASCII  dominance 图（R/D/o），跨场次再出 pooled 网格 CSV 与 pooled dominance 图。每场按采样总数归一化后再跨场平均，避免时长不同造成的偏置 |
| `购买节奏`（附带最小查询） | `game_events` + `player_identity` | 每场按 5 分钟桶统计双方购买次数；只把"0 秒起录"的场次纳入跨场 pooled（demo 时间轴与比赛时间对齐的场次） |

## 数据口径（与 §4.2 / §6.2 对齐的坑位）

- 只统计**有 player_slot 的英雄**：召唤物（类名以 `CDOTA_Unit_Hero_` 开头但
  `extra.player_slot` 为 NULL）会被 `json_extract(extra,'$.player_slot') IS NOT NULL`
  过滤掉，避免把兽王猪/鹰之类算成"第 11 个玩家"。
- 中途起录的场次（首个英雄采样 >300s，如 8979484553）时间轴带偏移：
  热区不受影响；购买节奏按 demo 内分钟数输出并明确标记，不进 pooled 时间聚合。
- 世界坐标窗口取 ±10000（真实地图约 ±9000），窗口外采样单独计数并告警。

## 输出

写入 `analysis/output/`（该目录已在 .gitignore 中）：
`<db>_<match>_heatmap_<cell>.csv`、`pooled_heatmap_<cell>.csv`、
`pooled_purchases_5min.csv`，加上控制台 dominance 图与逐场统计。
