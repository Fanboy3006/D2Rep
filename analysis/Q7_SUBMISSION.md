# Q7 · 全盘复现交互 UI（回放浏览器）—— 第一步交付回传（MVP 单场跑通）

> 状态：**第一步（MVP）已交付并发布公网**。第二步（±10s combat log 四 toggle / 技能 CD / 胜率）未做。
> 任务书：`STRATEGY/Q7_REPLAY_UI.md` ｜ 口径权威：`STRATEGY/DEM_FORMAT.md`、`STRATEGY/Q5B_WARD_VIEWER.md` §4 ｜ 交互规范：`STRATEGY/INTERACTIVE_MAP_PATTERN.md`
> 本文用 `STRATEGY/05_HANDOFF.md` 模板回传（见 §1），其后是口径说明 / 可复现脚本 / 验证 / 准确率边界 / 待拍板。

## 2026 第二轮（owner 三条指示的落地）

| owner 指示 | 落地 |
|---|---|
| **①「就用净值差」** | 已定为页面主显口径（顶部 `经济差（净值 · m_iNetWorth）★主显` + 页面文案写明"owner 定案"）；combat-log 累加仍并列显示（§2.4） |
| **②「你现在就是数据层」** | 建共享模块 `analysis/timebase.py`（时基 + `gold_i32`，Q5B/Q6/Q7 单一真相源）；把 6 项上游数据事实查清并写进 `analysis/DATA_DICT.md`（两套库差异 / gold int32 下溢 / `gold_reason` 实测表 / `assist_players` / `raw_json` / 暂停分布）；**新发现：技能 CD 数据本来就有，Q7 第二步的常量表阻塞解除**（§2.5） |
| **③「修掉我看看效果」** | `q5_ward.py::cle_for_tt` 已切到 `timebase.Clock`（保留 `Q5_LEGACY_CLOCK=1` 作 A/B 开关）；**量测后诚实订正**：改动对 Q5B 产出 = **0/4591**、窗口内偏差中位 0.03~0.07s（详见 §2.3 —— 我先前把改动量说大了，实测更小） |


---

## 1. 交接单（05_HANDOFF 模板）

```
问题ID：Q7（第一步 = 单场回放 MVP）
请求层：③逻辑 / ④总控
接收层：②数据（Q7 专项会话，独立于 Q5B/Q6）
目标：把一场职业赛做成"全盘复现"的交互 UI（回放浏览器）：左地图 + 双时间轴 + 双方 10 英雄头像；
      顶部经济差/经验差/胜率；右明细表（默认 10 英雄 KDA + 正反补）。
      本步只做：地图（缩放/平移）+ 10 英雄逐秒位置 + 双时间轴 + 顶部经济/经验差 + KDA/正反补表。
输入：dems/db_full/<league>/<match_id>.db 的 4 张表（combat_log / entity_snapshots / game_events / player_identity）
      + stats.db（队名/胜负/时长，用于对账；非必需）
期望输出：单文件 HTML（内嵌 base64，双击即开）+ GitHub Pages 公网地址 + 可溯源到 match_id 的明细
优先级：高（owner 点名"先做一个录像的东西"）
截止：—
已知约束：
  · 单场 combat_log ≈10.5 万条 → 单文件需精简字段（已做：按显示秒聚合 + 稀疏数组）
  · 两条时间轴（t_cle 游戏钟 / t_tick 回放钟）必须显式折算，且**暂停会破坏固定偏移**（见 §2.3）
  · combat_log 的 gold.value 存在 int32 负数按 uint32 落库的缺陷（见 §2.4）
  · 胜率/技能 CD 不在本步范围；经验差库内无独立源可交叉校验
```

**回传**：
- 产出（§3）已落盘 + 已 push 公网；
- 口径说明（§2）；
- 可复现脚本名（§4）；
- 已知约束 / 准确率边界（§5）——**含三个我主动改口径的地方，请 owner 过目**；
- 待 owner 拍板与下一步（§7）。

---

## 2. 口径说明（数字对不对，全看这几条）

### 2.1 显示时钟（唯一时间轴）
- `disp = t_cle − horn_cle`，**0:00 = 号角** = `combat_log` 里 `type_category='gamestate' AND value=5` 那一条的 `t_cle`。
- 实测自洽：初始金 600 事件恰落在 `disp = −89.9`（= 出门 −1:30），英雄首个采样落在 `disp ≈ −89..−90`。
- 时间轴范围 = `[min(−90, 英雄首个采样−1), 古人被摧毁]`；**比赛结束 = 远古（`*_fort`）被摧毁那刻的 `t_cle`**（同 Q5B §8.15），不是 `MAX(t_cle)`（结算残留会多记 360~925s）。

### 2.2 事件类数据：直接用 `t_cle`
KDA / 正反补 / 经济累加 / 经验累加 / 建筑时刻（建筑用 `game_events` 但走 §2.3 折算）——
`combat_log.t_cle` **本身就是游戏钟**，`disp = t_cle − horn_cle`，无需换算。

### 2.3 快照类数据：回放钟→显示钟折算（已下沉为共享模块 `analysis/timebase.py`）
`entity_snapshots.game_time_sec` 是**回放钟**（`tick/30`，与 `combat_log.t_tick` 同轴），暂停时照走；游戏钟则冻结。

- 固定偏移假设**在带暂停的场上会崩**：8830423116 的 `t_cle − t_tick` 在暂停前是 **+540.27**、暂停后是 **+8.72**（差 531.55s）。
  全量实测（`pause_scan.csv`）：**970 场里 624 场（64.3%）有 >1s 的暂停**，中位 29.2s、最大 1321.4s；
  `Δ_file` 本身从 6.6 到 **540.3** 不等 → 不存在可用的常数偏移。
- 实测物理证据：**暂停时场上所有实体逐秒完全静止**（位移恒 0、hp/净值不变）。
- 做法：把 combat 锚点两两配对，`Δcle`（真实游戏时间）按两锚点之间各秒的**活跃度**配额摊分 →
  锚点端点精确、段内按物理证据摊；副产品 `pause_sec_total` / `pause_blocks` 可直接复核
  （8830423116：531.5s → 3 块 347.8 + 23.6 + 159.6 = 531.0s，**闭合 99.9%**）。

**★ 诚实订正（我先前把改动量说大了，实测后修正）**：原 `q5_ward.py` 用的是
`bisect_left(t_tick)`「取 ≥tt 的最小 combat 记录」——它不是常数偏移，所以**并没有整段崩掉**
（暂停期间 combat_log 仍会零星收到条目，如泉水光环 modifier，"取右侧"落到的记录 `cle` 通常离真值很近）。
45 场抽样实测（`python analysis/q7_clock_check.py --scan 45`）：

| 区域 | 旧 vs 新 \|Δ显示秒\| |
|---|---|
| **游戏窗口内**（用户看得见） | 中位 **0.03~0.07s**；最大 **≤ 9.6s**；>2s 的秒数占 **<1%**；**>30s 的 0 场** |
| 窗口外（pre-game / post-game） | 最大 ~1600s（被 viewer 的 `[T0,T1]` 裁掉，无影响） |

对 **Q5B 产出**的改动量（`python analysis/q5_clock_check.py --sample 40`，41 场 / 4591 支眼）：
**逐支眼字段变化 = 0（0.00%）**。原因：`cle_for_tt` 只在 fallback 路径被调用，而实测 **100% 的眼都能匹配到
combat `use` 事件**（`use` 自带 `t_cle`，不需折算）；`destroy` 的匹配在 tick 空间做，暂停在两边同时存在会抵消。

**★ 全量端到端复核（最强证据，已做）**：换时基后**重跑全量 970 场**（`python analysis\q5_ward.py`）
→ `obs 38,813 / sentries 70,421 / win_sen cells 15,577`，与发布版**计数完全相同**；
再 `python analysis\build_q5_html.py` 重建 viewer，与发布的 `q5b_ward_viewer.html` 比对：

- `DAT.cells`（15,577 格）**逐格完全一致**、`SENG`（6,445 格 / 70,421 支眼钻取明细）**完全一致**、
  `MATCHTEAMS`（970 场）一致；
- **两个文件 SHA256 完全相同**：`F19C42F2…D113F347`。

Q6 侧：`python analysis\q6_check.py --sample 120`（该脚本 C 节用 `q5_ward.parse_match` **独立重算**并与产物逐字段比对）
→ **ALL PASS（120 场抽查，boundary-band 0）**（Q6 也 `import q5_ward`，同一代码路径）。

⇒ 这次改动是**潜在缺陷的加固**（不再依赖"暂停期间恰好有没有战斗日志条目"，顺带收掉"战斗日志稀疏段"的 ~10s 级误差），
**不是产出数字的变动**；**Q5B / Q6 的公网页面不需要因此重算/重发**（已用全量重跑 + 字节级比对证明）。
若某场出现"暂停期间完全没有 combat 条目"或"稀疏段很长"，新口径才是决定性的。

### 2.4 经济：两套源都给，**主显净值**（owner 2026 定案）
owner 已拍板：**顶部"经济差"主显 `m_iNetWorth` 净值差**。实测数据支持这个选择：

| match | 末刻 净值差(`m_iNetWorth`) | 末刻 累计金币差(combat-log) | 全场总值比 |
|---|---|---|---|
| 8955197224 | **+11,542**（天辉胜） | **+484** | 0.940 |
| 8830423116 | **+37,240**（天辉胜） | +40,672 | 0.888 |

两者**方向一致、幅度可差到 11k**。根因是语义不同（不是 bug）：
`combat-log 累加` = 累计**获取**金币（已按 `gold_reason=1` 扣死亡损失）；买装备/消耗品**不减** → 它等于"收入差"，
不等于"净值差"（辅助位买眼/粉/雾多，实测单人误差 −11%~+35%）。

页面处理（两者同时可见，无一被藏）：
- 顶部两个并排数字：`经济差（净值 · m_iNetWorth）★主显` 与 `经济差（combat-log 累加）`，各自带口径小字；
- 火花线三档 toggle（净值差 / 累计金币差 / 经验差）只改"哪条驱动曲线"；
- 页面 `口径与已知边界` 折叠块里写明两者末刻差多少（自动注入真实数字）。

### 2.5 另外几处"数据本身有问题、我在分析层绕过"的地方（owner 已授权我按数据层处理）
1. **`combat_log.gold.value` int32 下溢**：`gold_reason=1`（死亡扣钱）的负数被按 uint32 落库。
   40 场抽样 144,018 条 gold 行中 **2153/2153 行命中**（还原后均值 −232，行数 == 该场英雄死亡数）。
   **只有 gold 有这个问题**（`healing`/`xp`/`damage`/`modifier`/`item` 零行溢出）。
   已下沉为 `analysis/timebase.py::gold_i32()`（单一真相源）。**未改解析器**——按项目规矩这里只上报。
   不还原的后果：单人金币合计会被撑到 4.29e9 量级（纯垃圾）。
2. **`assist_players` 含击杀者本人**：值是**头部玩家索引 0..9**（不是 player_slot），且实测包含 killer。
   已剔除 killer 后再计助攻。不剔除会每人助攻凭空 +1（8830423116 的 40 条击杀里几乎全部命中）。
3. （继承 Q5B）`ward_placed.properties.t_cle` 是坏字段（恒早 540.26s）→ 本页**未使用**。
4. **`combat_log.raw_json` 不是"全字段保真"**：实测只有 11 个键
   （`a_team/attacker/damage_source/health/inflictor/location/t_team/target/type/value/value_name`），
   不含 `assist_players/networth/last_hits/obs_wards_placed` 等。与 `DEM_FORMAT.md` §4.3 的表述有出入，
   好在这些字段都已进固定列。**建议订正 `DEM_FORMAT.md` 该段**（我已把实测事实写进 `DATA_DICT.md` §E）。
5. **★ 两套库并存且内容不同（新发现，影响跨任务可比性）**：
   - `dems/db/`（971 场，**Q5 版散装 extractor**）：`game_events` 有 17 种事件类型
     —— `gold`(3.54M) / `ability_cd_start`+`end`(各 3.15M) / `neutral_kill`(1.23M) / `purchase`(569k) /
     `ability_known`+`ability_learn` / `item_cd_start`+`end` / `ward_use` / `smoke_count` / `game_state` …
   - `dems/db_full/`（970 场，**combat_log 重写版**）：叙事全进 `combat_log`，
     `game_events` 只剩 **3 种**（`ward_placed` / `building_spawn` / `building_destroyed`，即"实体空间层"）。
   - **`analysis/q1_value_zones.py` 的 glob 是 `dems/db/*/*.db`（旧库）**，而 Q5B/Q6/Q7 用 `dems/db_full/`。
     → Q1 的"逐格金币/GPM"与 Q5B/Q6/Q7 的叙事数据来自**同一批 .dem 的两次不同解析**，跨任务比较时需注意。
     已写入 `analysis/DATA_DICT.md` §A。
6. **★ 技能 CD 不需要外部常量表（解除 Q7 第二步的最大阻塞）**：
   `dems/db_full` 里确实没有 CD 数据（COMBAT_LOG_REWRITE §5.5 把散装 extractor 删了，而 CD 是**实体派生**、
   不是 combat 条目），**但 `dems/db/` 里本来就有**：
   `ability_cd_start` / `ability_cd_end` / `ability_known` / `ability_learn`
   （`properties.remaining` = **实际剩余冷却秒**，来自实体 `m_fCooldown`，含等级/天赋/减CD），
   道具同理 `item_cd_start` / `item_cd_end` / `item_known`（键形如 `ITEM:Black_King_Bar`，实测一场 37 条 item_cd_start）。
   → 做法：**按 match_id 两个库 join**（或把该 extractor 补回新 parser）。**不需要 owner 提供冷却常量表。**

### 2.6 KDA / 正反补
- 击杀：`type_category='death' AND is_target_hero=1`，killer=`attacker`，助攻= `assist_players` 去掉 killer。
- 正补 = attacker 为英雄、target 为线上兵（`npc_dota_creep_*`）、且 `a_team != t_team`；反补 = `a_team == t_team`。
  两个队伍字段都缺时退化用兵名前缀（`goodguys`=天辉 2 / `badguys`=夜魇 3）判。

### 2.7 位置 / 血量
- 逐秒 1Hz；采样偶有缺秒（8830423116 覆盖 99.08% / 8955197224 98.70%）→ 页面"沿用上一秒"（≤12s），
  抽屉里会标 `⚠位置沿用`。
- 血量上限取自快照 `extra.hp_max`（随等级变）→ 头像上方是真血条，表格里 `HP 当前/上限`（<30% 变黄）。

---

## 3. 产出（已落盘 + 已发布）

| 产物 | 说明 |
|---|---|
| `analysis/timebase.py` | **数据层共享时基**（Q5B/Q6/Q7 单一真相源）：号角/结束、暂停感知 `t_tick→t_cle`、`gold_i32`。自检 `python analysis/timebase.py <mid>` |
| `analysis/q7_replay.py` | 单场数据切片（读 db_full 只读 → 一个 JSON） |
| `analysis/build_q7_html.py` | 单文件 HTML 生成器（复用 Q5B 交互骨架 + 底图） |
| `analysis/q7_viewer_itest.js` | Node 桩交互回归测试（Q6 范式，65 项断言） |
| `analysis/q7_clock_check.py` | 时基 A/B 影响面量测（`--matches` 细看 / `--scan N` 抽样分布） |
| `analysis/q5_clock_check.py` | 时基改动的 **Q5B 逐支眼** A/B（含 `ab_legacy.csv` / `ab_new.csv` 逐支明细） |
| `analysis/output_q5/clock_ab/pause_scan.csv` | 全量 970 场的暂停总量 / `Δ_file`（可复核） |
| `analysis/output_q7/q7_8955197224.json` | 中间产物 1.61 MB |
| `analysis/output_q7/q7_8830423116.json` | 中间产物 1.05 MB |
| `analysis/output_review/q7_replay_8955197224.html` | **交付物** 3.86 MB（Team Spirit vs Iron Wing，62:25） |
| `analysis/output_review/q7_replay_8830423116.html` | **交付物** 3.32 MB（owner 在 Q5B 核对过的场，40:46） |
| `publish_repo/q7_replay_*.html` | 公网发布副本 |
| `analysis/DATA_DICT.md` | 已补：两套库差异 / gold int32 / gold_reason 实测表 / assist_players / raw_json / 时基 / 暂停分布 |

**公网（需人工刷新确认；本机无法抓公网自检）**：
- https://bigfatblackwhale.github.io/DSH-Dota2/q7_replay_8955197224.html
- https://bigfatblackwhale.github.io/DSH-Dota2/q7_replay_8830423116.html

**页面已实现（对照任务书 §2）**：

| 任务书要求 | 实现 |
|---|---|
| 左：地图（沿用原底图）+ Canvas 缩放/平移 | ✅ 复用 `_q5_map_annot.png` + 官方标定常量；滚轮以鼠标为中心缩放、拖拽平移、双击复位、缩到全图自动归位 |
| 左·地图下方：双方 10 英雄头像 | ✅ 天辉/夜魇各 5，带真血条（<30% 变黄、阵亡灰化），点击=选中 |
| 两条滑动块（全场 + ±60s） | ✅ 见 §3.1 的双条语义 |
| 顶部：经济差 / 经验差 /（胜率） | ✅ 经济差两套源并排 + 经验差；胜率明确标 **未做** |
| 右：默认 10 英雄 KDA + 正反补 | ✅ 另加"当前时刻净值/累计金币/经验/HP"四列（随播放实时变） |
| 地图上按时间显示 10 英雄位置 | ✅ 逐秒位置 + 30s 轨迹 + 队色环 + 阵亡灰化 + ±4s 击杀标记 |
| 附加（提高可核对性） | 建筑（塔/兵营，按摧毁时刻打叉）、大时间轴上的击杀/建筑刻度、全场经济差火花线（可点/拖定位） |
| **未做（第二步）** | 点英雄 → ±10s combat log 四 toggle；技能 CD（三态 + BKB/刷新球/TP）；胜率；眼位/烟雾图层 |

### 3.1 双时间轴语义（按 owner 默认①：松手提交 + 瞬时归零）
- **大条** = 全场（`−1:30 → 结束`，含"已提交时刻"）。
- **小条** = ±60s，中点为 0。实际时刻 `tCur = clamp(大条 + 小条, 起, 止)`。
- **拖小条（实时）**：地图/表格按 `tCur` 实时刷新，大条**滑块**同步小幅移动（±60s 在整场条上仅约 1.5% 宽度，视觉即"小幅联动"）。
- **松手（提交）**：大条推进"滑过的量"、小条**瞬时归零**（`tCur` 不回跳）。
- **拖大条 / 点火花线** = 绝对定位，小条立即归零。播放时 `tCur` 自动前进，播到结束自动停。
- 快捷键：空格=播放/暂停，←→=±5s；速度 1×/2×/4×。

---

## 4. 可复现脚本

```powershell
# 1) 单场切片（只读 db_full；自带自检：时钟/暂停/覆盖/经济对账/KDA/时长对账）
python analysis\q7_replay.py 8955197224          # 或 8830423116；--no-write 只跑自检

# 2) 生成单文件 viewer
python analysis\build_q7_html.py 8955197224 8830423116

# 3) 交互回归测试（Node 桩，无需浏览器）
node analysis\q7_viewer_itest.js 8955197224      # 8830423116 同理；退出码 0 = 全通过

# 4) 时基诊断 / A/B 影响面（数据层加固的验证）
python analysis\timebase.py 8955197224 8830423116        # 号角/结束/暂停块/时长对账
python analysis\q7_clock_check.py --scan 45               # 旧 vs 新换算的窗口内偏差分布
python analysis\q5_clock_check.py --sample 40             # 对 Q5B 逐支眼的改动量（0/4591）

# 5) 发布（同 Q5B 链路）
Copy-Item analysis\output_review\q7_replay_8955197224.html publish_repo\ -Force
git -C publish_repo add -A; git -C publish_repo commit -m "..."; git -C publish_repo push origin main
# push 时打印的 sh.exe ... Win32 error 5 是无害告警，以末行 xxx..yyy main -> main 为准
```

辅助（不进交付链路）：`.tmp/q7_preview.py` 用 PIL 复刻 render 的几何，把某场若干时刻的
英雄位置画到底图上导出 PNG 供人眼核对（本机起不了浏览器时的替代肉眼验证手段）。

---

## 5. 验证记录（本步做了什么实证）

| 验证 | 做法 | 结果 |
|---|---|---|
| **时长独立对账** | 本脚本"远古被摧毁"推出的时长 vs `stats.db`（OpenDota）`duration_sec` | 8955197224：**3745 vs 3744 → 差 +1.0s**（一致）；8830423116 未被 stats.db 覆盖 → 无独立源（已如实标注） |
| 显示时钟自洽 | 初始金 600 事件 / 英雄首个采样 的 `disp` | 分别 −89.9s（出门 −1:30）、−89~−90s ✅ |
| 暂停定位 | 8830423116：精确暂停总量 **531.5s**；3 块 347.8 + 23.6 + 159.6 = **531.0s（闭合 99.9%）**，与逐秒位置目视冻结段（318–665 / 683–705 / 711–869）逐段吻合 | ✅ |
| 时基 A/B 影响面 | `q7_clock_check.py --scan 45`（窗口内外分开） | 窗口内旧/新 \|Δ\| 中位 **0.03~0.07s**、最大 **≤9.6s**、>30s **0 场**；窗口外最大 ~1600s（被裁掉） |
| 时基 A/B 对 Q5B | `q5_clock_check.py --sample 40`（41 场 / 4591 支眼） | **逐支眼变化 0（0.00%）** |
| **时基对 Q5B 全量端到端** | 换时基后重跑全量 970 场 + 重建 viewer，与发布版比对 | 计数相同、`DAT.cells`/`SENG`/`MATCHTEAMS` 全一致、**SHA256 相同** |
| 时基对 Q6 | `q6_check.py --sample 120`（C 节独立重算） | **ALL PASS，boundary-band 0** |
| 全量暂停面 | 970 场 `pause_scan.csv` | **624 场（64.3%）有 >1s 暂停**；中位 29.2s、最大 1321.4s；`Δ_file` 6.6~**540.3** |
| 地图标定闭环 | 内嵌页面里标记的像素坐标 vs 官方标定式 `w2p` | 完全相等（itest 断言 <0.01px）；另用 PIL 预览目视：出门期 10 人全在**天辉泉水（左下）**，结束时刻集中于**夜魇基地（右上）**——与本场天辉获胜一致 |
| KDA/CS 自洽 | 表格 K/D 列之和 == 击杀事件条数（带 killer 的条数） | 8955197224：69/69、82/82 ✅ |
| 交互回归 | `q7_viewer_itest.js`（Node 桩，65 项） | **两场全部通过**（双条语义 15 项、缩放平移 9 项、选中/hover 7 项、全时间轴 58 时刻扫描无 NaN、播放 3 项、火花线 4 项、数据自洽 12 项、几何闭环 1 项） |
| 前端坑继承 | 声明的 Q5B §9 三个坑 | `capValue` DOM 字符串陷阱（本页无该控件）、`onmousemove` 的 px 作用域（已按 §9.2 提到 `if(drag)` 之外）、`draw()` 重入锁（已加） |
| 多场健壮性 | 两场（有/无暂停、有/无 stats.db 元数据、不同 Δ_file） | 均跑通；无暂停场（8955197224）测得暂停 0s、`Δ_file=15.03` |

**本步抓出并修掉的自己的 bug**（留档，避免回归）：
1. 播放 `_lastFrame` 用 `0` 当"未初始化"哨兵 → `ts=0` 时永不推进（改为 `-1` 哨兵）。
2. 大条缩到"已经能看全图"时不归位 → 永远回不到全图态（新增 `snapFull()`）。
3. 播放时火花线每帧重画 ~1.5 万个 `lineTo` → 静态部分缓存到离屏画布，每帧只画播放头。

---

## 6. 已知边界 / 准确率边界（对外必须标注）

1. **经济差是两套口径，别混**（§2.4）：净值差 = 标准经济差（非 combat log，项目内对账 OpenDota 0.000%）；
   累计金币差 = 严格 combat log，但只是"收入差"，与净值差随比赛拉大（本场末刻差 11k / 3.4k）。
2. **经验差只有一条源**：库内无经验快照、`playerstats` 也没有可用的 xpm → **无法交叉校验**，仅作参考。
   量级自洽性检查：8955197224 单人末刻 11,348~40,340（对应约 L20~L30），在合理区间。
3. **正反补只数线上兵**：不含中立/召唤物（口径同任务书）；且依赖 combat `Death` 的 `attacker`
   （Dota 战斗日志的"最后一击"归属）——与 OpenDota 的 `last_hits` 未做逐场对账（本机无该字段的库内来源）。
4. **位置是解析层 1Hz 采样**：偶有缺秒（~1.3~1.9%，已沿用上一秒并标注）；`on_tick_start` 有 1 tick 滞后（秒级可忽略）。
5. **不显示一切"看不见的东西"**：本页没有眼位/烟雾/野区/肉山血量图层，也没有中立生物与兵线。
6. **胜率未做**：状态胜率模型需要"经济/经验+时间 → 胜率"的拟合，且必须防泄漏（绝不偷看结果）；
   本页只给出该场的真实胜负（来自 stats.db），**不是**"某一刻的状态胜率"。
7. **暂停检测的残余不确定性**：活跃度是 1Hz 采样下的布尔判据，若某段"未暂停但全体不动"会被误判为暂停。
   实测两场未出现（正常对局几乎每秒都有人移动/掉血/涨钱）；全量 970 场的暂停分布已落盘可查
   （`analysis/output_q5/clock_ab/pause_scan.csv`）。若某场出现误判，段内 `disp` 会被压平（不会乱序），
   且**窗口内**总体误差已被 45 场抽样量化为 ≤9.6s。
8. **`stats.db` 只覆盖联赛 19719**（本机实际拉取过的联赛）→ 8830423116（19101）**没有队名/胜负/时长**，
   页面上显示"（stats.db 无队名）"，这是如实回退，不是漏做。

---

## 7. 待 owner 拍板 / 下一步

**待拍板（都不阻塞本步）**
1. ~~经济差主显~~ → **owner 2026 已定案：主显净值差**（combat-log 累加并列显示）。已落进页面文案。
2. ~~技能 CD 常量表~~ → **已解除**：数据本来就在 `dems/db/`（`ability_cd_start/end` + `remaining`，
   含 BKB 等道具 `item_cd_start/end`），**不需要外部常量表**（见 §2.5.6）。
3. 双时间轴：现按默认①（松手提交 + 瞬时归零）实现；若要"实时提交"或归零做动画，改动很小。
4. ±10s 窗口：现按默认"以当前播放时刻为中心"预留（第二步实现）。
5. 三步之外的取舍：眼位/烟雾图层要不要并入本页（Q5B/Q6 产物可直接复用）？移动端 lite 版要不要做？

**第二步计划（建议顺序）**
1. 技能 CD（**阻塞已解除**，见 §2.5.6）：`dems/db/` 的 `ability_cd_start/end` + `item_cd_start/end`
   已带**真实剩余冷却秒**（`properties.remaining`，来自实体 `m_fCooldown`），
   连同 `ability_known`/`ability_learn`/`item_known` 可**直接**做"三态"（冷却中灰+剩余秒 / 未学未拥有 / 就绪），
   **不需要常量表**。按 match_id 与 `dems/db_full` join；键名形如 `CDOTA_Ability_Tiny_Tree_Grab` / `ITEM:Black_King_Bar`。
   若某场 `dems/db/` 缺失，则退回"combat_log 的 `ability`/`item` cast 事件 + 常量表"（那条路才需要常量）。
2. 点英雄 → 右栏切为该英雄 **±10s combat log**（4 个 toggle）；数据已在 `combat_log` 里
   （modifier 5.2 万 / damage 3.3 万 条/场，已有 `t_cle` 免折算）；只需在切片里建索引或按需查询。
   注意排除伪 cast：`courier_take_stash_and_transfer_items`(1159)、`ability_lamp_use`、
   `item_power_treads`(1905，切假腿)、`item_quelling_blade`。
3. 胜率：状态胜率（经济/经验差 + 时间）拟合 970 场，**状态化、不偷看结果**。
4. 若 owner 需要：眼位/烟雾图层（复用 Q5B/Q6 产物）、多场切换、移动端 lite 版。

**建议下沉到上游（我在分析层绕过，没改解析器）**
- `combat_log.gold.value` 的 int32 下溢（§2.5.1）——**解析层修**最省事（在写 `value` 列时按 type 判符号）；
- `assist_players` 语义（头部索引 0..9、含 killer）——**解析层/文档写清**；
- `raw_json` 只有 11 键，与 `DEM_FORMAT.md` §4.3"全字段保真"不符——**文档订正**（我已写进 `DATA_DICT.md` §E）;
- 暂停感知的 `t_tick→t_cle` 折算——**已下沉**到 `analysis/timebase.py`，`q5_ward.py` 已切换
  （实测对 Q5B 产出 0/4591 变化，见 §2.3）。
- （可选）把 `ability_cd_*` / `item_cd_*` 补回 combat_log 版 parser，省掉 Q7 第二步的双库 join。
