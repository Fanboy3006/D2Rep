# Q1–Q4 现状快照（② 数据分析/收集层 → ③ 逻辑层）

> 用于和逻辑层对齐口径、讨论遗留问题。所有数值来源=本地 `.dem`（`dota_parse`），
> 通用三表模型（`entity_snapshots` / `game_events` / `player_identity`），不依赖 OpenDota。
> 时间口径：窗口均按 **game-clock 对齐**（检测第一秒玩家净值>0 作为 game_start，减去 pre-game 偏移）。

---

## 共同依赖：A1 逐玩家逐分钟净值（已定案，精确）

- 读 `.dem` 实体 `CDOTA_DataRadiant` / `CDOTA_DataDire` 每玩家的
  `<idx>.m_iNetWorth`（净值）+ `m_iReliableGold/m_iUnreliableGold`（现金）。
- 写入 `entity_snapshots`（`entity_type='networth'`，`entity_id='nw:<team>:<idx>'`，
  `hp`=净值，`extra{reliable,unreliable}`）。
- **已对账**：与 OpenDota 逐玩家净值的误差 = **0.000%**。
- 播放器映射：天辉 idx 0..4 → slot 0..4；夜魇 idx 0..4 → slot 128..132。
- 旧反推方案（金币流近似）已弃用，不再作为口径。

> Q1 / Q3 / Q4 都建立在 A1 之上。

---

## Q1 打野经济

### 问题（逻辑层定的 scope）
> “刷哪个野点、何时刷，对**本人经济 + 团队经济**的边际影响 / 机会成本。”

按逻辑层结算的 scope，第一版 proxy **不含** `lane_available(t)` 机会成本项（该概念有争议，暂缓）。
交付两块可复现的部分：

- **B1 野点活动热区**：把 `neutral_kill` 事件在**地图网格**上分箱（cell），按窗口
  0-10 / 10-20 / 20+ / 整场 拆分，输出 `(team, cell_x, cell_y, count)` CSV + ASCII 密度图。
  → `analysis/output_q1/q1_neutral_activity_heat_250.csv`
- **B2 团队经济宏观对照**：把 `gold` 事件按 team×分钟求和，得各窗口 team gold_adv（radiant−dire）。
  → `analysis/output_q1/q1_gold_adv_minute.csv`

字段口径（`DATA_DICT.md`）：`neutral_kill` 只算 `CDOTA_BaseNPC_Creep_Neutral`（剔线野/投石车）
与 Roshan；记录世界坐标 `(x,y)`；归属队伍用**象限近似**（`x<0∧y<0`→radiant，`x>0∧y>0`→dire，其余→mid）。

### 关于“是否依赖地图层定义每一组野怪的位置？”
- **当前已完成的部分（B1/B2）不依赖**：B1 直接用击杀的原始世界坐标分箱成网格，B2 只按团队求和金币，
  都不需要一份“每处野点坐标”的地图层。
- **如果要往下做 per-camp 归属（“这个野点被清了值多少” / “某个具体野点活动强度”）才需要**：
  一份**中立野点位置层**（每边标准 N 个野点坑 + Roshan 坐标）。这层**尚未建**。
- 另外“精确归属到是哪个英雄清的”需与战斗日志 Death 配对，属下一步，当前是象限近似。

### 现状 / 缺口
- 仅在 **6 场**小样本上跑通（`output_q1`），产出 pooled CSV；未覆盖全部 970 场。
- 无 HTML 复核页；未做 per-camp 归属；机会成本项暂缓。
- `output_public/` 下有约 480 场**逐场** `*_heatmap_250.csv`（另一次较大批次的野区活动热区），
  但**未与 B1/B2 pooled 口径统一**，需确认是否纳入。

---

## Q3 英雄经济产出 & 经济→胜率（有两条子口径，注意区分）

### Q3a-REL（“相对贡献”版）—— 已发布为 q3_rel_viewer.html
- 来源 `analysis/q3_rel_measure.py`。
- 测度：**带上该英雄，队伍每分钟多赚的净值**（队赚 / 窗口）+ **相比对手，队伍经济优势每分钟变化**
  （优势=Δadv 变好=绿/变差=红）+ 各窗口“转优/flat/转劣”盘数占比。
- 输出：`q3a_rel_detail.csv`、`q3a_rel_agg_hero.csv`；HTML：`q3_rel_viewer.html`（已公网）。

### Q3a 绝对刷钱能力（`q3_outcome_economy.py`）
- 测度：每英雄每窗口**每分净值增量** = `(净值@end − 净值@start) / 窗口分钟`（A1 精确净值）。
- 输出：`analysis/output_q3/q3a_hero_nw_increment.csv`（hero, per_min_0_10/10_20/20_plus, n…）。

### Q3b 经济→胜率转化（**未控版**）—— 计算已有，viewer/稳健性缺口
> **全文：英雄 × 队伍 10分钟经济状态 → 队伍胜率。**
> team 10min 状态用 `stats.db` 的 `gold_adv@10`（team view，±thr=1000 → lead/even/trail）；
> 英雄归属用 `player_identity.hero_name + team_id`；胜率=该状态下队伍胜盘/该状态盘数。

- 输出：`analysis/output_q3/q3b_hero_state_winrate.csv`（hero, state, n, win, winrate）。
- **明确标注未做**：
  1. **控版（固定效应）**：team×tournament 固定效应；
  2. **leave-one-team-out**（留一队）稳健性。

### 现状 / 缺口
- Q3a-REL 已发布。Q3b 只有 CSV，**无 HTML 复核页**（可复用 `build_q3_html.py` 模板）。
- 控版 / leave-one-out 未做。

---

## Q4 窗口因果分解（**未实现，且有两处需要逻辑层先对齐**）

### 问题（按当前理解）
> 把**逐窗口经济**对最终胜负的贡献做“因果”分解——例如 10 分钟领先、10→20 的滚动幅度，
> 各自有多大传导到胜率。

### 现状
- 机器上**无 Q4 脚本**；尚未开始。基础（A1 逐玩家逐分钟净值）已具备，可以支撑「面板/窗口级模型」。

### ⚠️ 讨论点（逻辑层需先定，我再实现）
1. **“因果”到底指什么**：
   - 是**回归/方差分解**（哪个窗口的经济 delta 对胜率贡献最大，做 FE / 控制变量）？还是
   - **反事实/结构分解**（模拟“把领先移除”后胜率变化）？二者模型完全不同。
2. **识别与内生性**：gold_adv 与胜率互相内生（领先→滚雪球→胜，反过来行为也会影响经济）。
   需确定控制组/处理变量、混淆因素（阵容 strength、选手、tournament、ban/pick 等）。
3. **“窗口”切法**：用 0-10/10-20/20+，还是更细？是否对齐 game_start。
4. **输出形态**：是要一个“每个窗口对胜率的边际贡献”表 + 解读，还是要一个可复核 HTML。

> 在逻辑层把上面第 1、2 点讲清楚前，我建议**先不写代码**，避免方向错误返工。

---

## 已上线公网（给逻辑层同步）
- Q2 全队伍（10 分钟经济→胜率 & 10→20 滚雪球，含来源联赛）：
  `https://bigfatblackwhale.github.io/DSH-Dota2/q2_all_teams_viewer.html`
- Q3a-REL（英雄相对贡献，按窗口）：
  `https://bigfatblackwhale.github.io/DSH-Dota2/q3_rel_viewer.html`

## 关键全局口径
- 队伍：XG=8261500（唯一），VG=726228，TS=7119388，TY=9823272，PV={9572001,9824702}。
- 队伍近似合并：共用 ≥3 名 steam_id 的队伍合并（64 队 → 43 组）。
- 窗口：0-10=[0,600)，10-20=[600,1200)，20+=[1200,∞)；阈值 ±1000。
- 官方选手名：`.tmp/pro_players.json`（steam_id→name，找不到 fallback 录像名）。
- 联赛官方名：`.tmp/league_names.json`（league_id→官方名）。
