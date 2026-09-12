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
| `Q1 地图经济价值分区`（q1_value_zones.py）【现行】 | `entity_snapshots`(hero 位置 + networth) + `game_events`.gold + `player_identity` | 描述性版：地图 250 网格(80×80)，每格 × (全场/0-10/10-20/20+) 三指标(个人GPM / 整体GPM / 相对GPM)，每指标/窗口独立色阶上限 + 滑块 + 分区标注。复核页 `q1_value_zones_viewer.html`；明细 `q1_zone_detail.db`(逐格×窗口×match)。见 `DATA_DICT.md` |
| `Q2 全队伍经济→胜率`（build_all_teams_q2.py） | `stats.db` + OpenDota + 净值 | 全队伍 10分钟经济→胜率 & 10→20滚雪球(含分类率/±差/来源联赛)。复核页 `q2_all_teams_viewer.html` |
| `Q3 英雄相对贡献`（q3_rel_measure.py）/`Q3a/Q3b`（q3_outcome_economy.py） | `entity_snapshots`.networth + `stats.db` | Q3a-REL 英雄对队伍净值/滚雪球相对贡献(复核页 `q3_rel_viewer.html`)；Q3a 绝对刷钱 + Q3b 英雄×10min状态→胜率(CSV，控版/leave-one-out 未做) |
| **`Q7 全盘复现交互 UI（回放浏览器）`**（q7_replay.py + build_q7_html.py）【现行·第一步已交付】 | 单场库 4 表（`combat_log` / `entity_snapshots` / `game_events` / `player_identity`）+ `stats.db`(队名/时长对账) | **单场**回放浏览器：左地图(缩放/平移)+10 英雄逐秒位置+轨迹、地图下双方 10 头像(真血条)、双时间轴(全场 + ±60s)、顶部两套经济差+经验差+全场火花线、右表 10 英雄 KDA+正反补+当前净值/金币/经验/HP。复核页 `q7_replay_<match>.html`；口径与边界见 `Q7_SUBMISSION.md`；交互回归 `q7_viewer_itest.js` |

## Q7 全盘复现交互 UI（回放浏览器）· 第一步（MVP）

- **脚本**：`python analysis/q7_replay.py <match_id>`（切片 + 自检）→ `python analysis/build_q7_html.py <match_id>`（单文件 HTML）→ `node analysis/q7_viewer_itest.js <match_id>`（交互回归）。
- **共享时基**：`analysis/timebase.py`（Q5B/Q6/Q7 共用的单一真相源）—— 号角 / 比赛结束 / **暂停感知**的
  `t_tick→t_cle` 折算 / `gold.value` 的 int32 下溢还原。自检 `python analysis/timebase.py <match_id>`。
- **时间口径**：显示钟 `disp = t_cle − horn_cle`（0:00 = 号角）；结束 = 远古被摧毁（不用 `MAX(t_cle)`）。
  `entity_snapshots` 是**回放钟**，用"暂停时实体静止"的物理证据把 `Δcle` 按活跃秒摊分折算。
- **经济两套源并列显示**（净值 `m_iNetWorth` / combat-log 累计金币），**owner 定案：主显净值差**；差异与原因见 `Q7_SUBMISSION.md` §2.4。
- **改动量实测（别夸大）**：`python analysis/q7_clock_check.py --scan 45` → 窗口内旧/新 |Δ显示秒| 中位 0.03~0.07s、
  最大 ≤9.6s、>30s 的 0 场；`python analysis/q5_clock_check.py --sample 40` → Q5B 逐支眼改动 **0/4591**。
- **已知上游数据事实（分析层已绕过，未改解析器）**：
  1. `combat_log.gold.value` 的 int32 下溢：`gold_reason=1`（死亡扣钱）的负数被按 uint32 落库。
     40 场抽样 2153/2153 行命中；**只有 gold 有这个问题**（healing/xp/damage/modifier/item 无一行溢出）。
  2. `assist_players` 的值是**头部玩家索引 0..9**（不是 player_slot），且**含击杀者本人**。
  3. `raw_json` 实测只有 11 个键，**不是** `DEM_FORMAT.md` §4.3 说的"全字段保真"。
  4. **两套库并存且内容不同**：`dems/db/`（971 场，Q5 版散装 extractor：`gold` / `ability_cd_*` /
     `item_cd_*` / `ability_known` / `purchase` / `neutral_kill` / `ward_use` / `smoke_count` / `game_state`）
     vs `dems/db_full/`（970 场，combat_log 重写版：叙事全进 `combat_log`，`game_events` 只剩
     building/ward 的空间层）。**Q1 读 `dems/db/`，Q5B/Q6/Q7 读 `dems/db_full/`** —— 跨任务比较时要注意。

## Q1 地图经济价值分区（现行；旧"野区经济 proxy"已归档到 `legacy/`）

Q1 由③逻辑/owner 重定义为"地图经济价值分区"，描述性版已实现并首次发布公网。

- **输入**：`entity_snapshots`(`entity_type='hero'` 逐秒位置 + `networth` 定 game-start) + `game_events.gold` + `player_identity`(hero→team)。
- **口径**：地图 250 网格(80×80)；每格 × (全场/0-10/10-20/20+，game-start 对齐) 三指标：
  ①个人GPM=该格金币/该格英雄秒×60；②整体GPM=某队有人在该格→该队逐秒金币/分钟；③相对GPM=某队有人在该格→(己方−对方)逐秒金币差/分钟。
- **产出(analysis/output_q1)**：`q1_zone_agg.csv`、`q1_zone_detail.db`；复核页(analysis/output_review)`q1_value_zones_viewer.html`。
- **数据源提取器**：`dota_parse` 的 `JungleExtractor`(`neutral_kill`/`gold`)与 `NetWorthExtractor`，见 `DATA_DICT.md`。
- **旧 proxy**(`jungle_economy_proxy.py` + `build_q1_html.py` + `q1_jungle_viewer.html`)已归档 `analysis/legacy/`，仅供对照，非现行口径。
- **限制**：每格 n 小→描述性(需 pub 大样本上统计精度)；反候果未控；分区标签近似(阈值在 `q1_value_zones.py::zone_label` 可调)。

脚本：`python analysis/q1_value_zones.py`（计算，逐场增量写明细库）+ `python analysis/build_q1_zones_html.py`（复核页）。

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
