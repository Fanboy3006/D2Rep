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
| **`Q7 全盘复现交互 UI（回放浏览器）`**（q7_replay.py + build_q7_html.py + q7_winprob.py）【现行·两步已交付】 | 单场库 4 表（`combat_log` / `entity_snapshots` / `game_events` / `player_identity`）+ **`dems/db/`（技能/道具 CD）** + `stats.db`(队名/时长对账) | **单场**回放浏览器：左地图(缩放/平移)+10 英雄逐秒位置+轨迹、地图下双方 10 头像(真血条)、双时间轴(全场 + ±60s)、顶部两套经济差+经验差+**状态胜率**+全场火花线、右表 10 英雄 KDA+正反补；**点英雄 → 该英雄 ±10s combat log（4 toggle）+ 技能 CD 三态（BKB/刷新球/TP）**。复核页 `q7_replay_<match>.html`；口径与边界见 `Q7_SUBMISSION.md`；交互回归 `q7_viewer_itest.js`；胜率模型 `q7_winprob.py` |

## Q7 全盘复现交互 UI（回放浏览器）· 两步已交付

- **脚本链**：
  `python analysis/q7_winprob.py`（状态胜率：970 场拟合 + 按 match 留出验证；`--refit-only` 用样本缓存重拟合）
  → `python analysis/q7_replay.py <match_id>`（切片 + 自检）
  → `python analysis/build_q7_html.py <match_id>`（单文件 HTML）
  → `node analysis/q7_viewer_itest.js <match_id>`（交互回归，含内嵌 JS 语法预检）
  → `node analysis/q7_viewer_itest.js <mid> "dump=<idx>@<秒>"`（把右栏面板渲染成文本，**不用浏览器就能核对内容**）
- **共享时基**：`analysis/timebase.py`（Q5B/Q6/Q7 单一真相源）—— 号角 / 比赛结束 / **暂停感知**的
  `t_tick→t_cle` 折算 / `gold.value` 的 int32 下溢还原。自检 `python analysis/timebase.py <match_id>`。
- **时间口径**：显示钟 `disp = t_cle − horn_cle`（0:00 = 号角）；结束 = 远古被摧毁（不用 `MAX(t_cle)`）。
  `entity_snapshots` 与 `dems/db` 的 CD 事件都是**回放钟**，经 `timebase.Clock` 折算。
- **经济两套源并列显示**（净值 `m_iNetWorth` / combat-log 累计金币），**owner 定案主显净值差**；胜率模型也用净值差。
- **技能 CD 不需要常量表**：`dems/db` 的 `ability_cd_start/end` 已带**真实剩余冷却秒**（实体 `m_fCooldown`，
  含等级/天赋/减CD）。`db_full` 里没有（COMBAT_LOG_REWRITE 删了散装 extractor，CD 不是 combat 条目）→ **两库按 match_id join**。
- **状态胜率**：分时间桶逻辑回归 + 桶间系数线性插值，970 场 / 按 match 切分 → **测试 AUC 0.830**（30-40 分钟桶 0.910），
  特征只用 t 时刻可观测的净值差+经验差+时刻，标签用"远古被摧毁"判定，**无泄漏**。
- **眼位 / 烟雾图层**：眼位**直接调用 `q5_ward.parse_match`**（口径单一真相源，自动继承 Q5B §4 五条裁定：
  实体类名判型 / use 为放置时刻 / 到期 `attacker==target` / 销毁一一对应 / 右删失）；
  烟雾来自 `combat_log` 的 `item_smoke_of_deceit`（使用时刻）+ `modifier_smoke_of_deceit`（在烟雾中的区间）。
- **多场切换**：`python analysis/build_q7_index.py` 生成 `q7_index.html`（**970 场**可搜可筛、按列排序、
  勾选后一键生成整批构建命令）；`--batch league:19719|spread:8|hero:axe|ids:...|top:20`（加 `--full` 建完整版）；
  `--publish` 复制到 publish_repo。索引的胜负/时长由 combat_log 推出：**415 场对账 OpenDota 时长中位差 0.0s、胜负 0 冲突**。
- **lite 版**（`build_q7_html.py --lite --step 4`）：**0.4~0.6MB/场**（地图 448px、位置 4s / 经济 16s 抽稀、不含明细与技能图标；
  地图/双时间轴/经济·经验差/胜率/KDA/眼位/烟雾/技能CD 全保留）→ 可批量铺开。
  测试：`Q7_LITE=1 node analysis/q7_viewer_itest.js <mid>`。
- **UI 迭代（owner 逐项提的）**：① 地图宽 = 内容宽 **50%** 且强制 1:1；
  ② **时间轴移出左栏 → 地图上方整宽面板**，重大事件带 mm:ss，**上下位置 = 对谁有利**（击杀看凶手方、
  建筑看被毁方的对面、肉山看击杀方）；③ 事件改成**图标**：阵亡英雄头像 / 塔·兵营·基地·肉山·侦查守卫剪影，
  **环色 = 它属于哪一方**（绿天辉 / 红夜魇 / 灰无主·肉山），mm:ss 变成可关的开关（默认关）；
  ④ **combat log 占右半屏 + 整页锁一屏**（`body` 100vh flex、右栏自己滚、地图 `fitCanvas()` 取
  `min(可用宽,可用高)` 作边长、`CSX` 随画布走所以字不会缩水、时间轴层数按视口高自适应 2/3/4）；
  ⑤ 10 个头像**竖排在地图右侧**（天辉一列 ｜ 夜魇一列，各有队名与分隔线）—— 头像不再吃地图高度，地图从 ≈458 → **≈516px**；
  ⑥ **combat log 窗口 ±10s → ±45s**，列序改成 **时刻 ｜ 本英雄 ｜ 技能/事件 ｜ 数值 ｜ 对象**，
  每行带**技能图标 + 对方英雄头像**（`analysis/detail_icons.py`：英雄头像 / 官方技能·道具图 /
  塔·兵营·肉山字形 / 自绘"普通攻击"字形；**解析不到就不画，文字名一律保留**），
  ±45s 团战期最多 **1785 行** → **虚拟滚动**（DOM 只画视口附近 ~160 行）+ 播放时 130ms 节流。
  逐条记录、右半屏 DOM bug（`.left` 没闭合）、**四类归属对账**（英雄↔英雄事件两侧都记；`--attr-only` 单场秒级、`--attr-scan` 全 corpus 批检）与取舍见 `Q7_SUBMISSION.md` §8。
- **改动量实测（别夸大）**：`python analysis/q7_clock_check.py --scan 45` → 窗口内旧/新 |Δ显示秒| 中位 0.03~0.07s、
  最大 ≤9.6s、>30s 的 0 场；`python analysis/q5_clock_check.py --sample 40` → Q5B 逐支眼改动 **0/4591**；
  换时基后全量重跑 Q5B 并与发布版比对 → **SHA256 相同**。
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
