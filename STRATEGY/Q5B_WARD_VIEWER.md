# Q5B · 真眼反眼热力复核页（Ward Viewer）—— 任务文档

> 状态：**已交付并公网可用**（owner 现场核对通过）。本文是 Q5B 的任务级文档：口径、数据链路、指标、页面、构建部署、已知边界、验证与待办。
> 口径细节的权威出处：`STRATEGY/DEM_FORMAT.md` 附录 **§C6**（订正与实证）、**§D5b**（落地接线）；口径决策流水：`analysis/Q5_RULES_CALIBRATION_LOG.md` §16–§17；交互模式通用规范：`STRATEGY/INTERACTIVE_MAP_PATTERN.md`。

---

## 0. 一句话

把 **970 场职业比赛**里每一支**真眼（Sentry）**的插入位置铺到地图上，逐格统计"**在这个位置插真眼，平均能反掉几个敌方假眼**"，做成可缩放、可筛选、**能钻取到逐场逐支眼（match_id + 时刻 + 坐标）回去看录像**的热力复核页。

- 统计单元 = **真眼位置**（不是"真眼这支眼"，是"格"）。
- 核心指标 = **平均反假眼**。
- 交付物 = 单文件 HTML（内嵌地图与图标），GitHub Pages 公网可开。

---

## 1. 任务定义（owner 口径）

| 项 | 定义 |
|---|---|
| 数据范围 | `dems/db_full/`，**970 场**职业比赛 |
| 真眼 | `CDOTA_NPC_Observer_Ward_TrueSight`（真视/哨卫），寿命 **420s（7:00）** |
| 假眼 | `CDOTA_NPC_Observer_Ward`（侦察守卫），寿命 **360s（6:00）** |
| 真眼的意义 | 真视半径内让敌方假眼现形 → 己方把它反掉；**自身不会主动攻击**，只可能自然到期/被反 |
| 要回答的问题 | 哪个位置插真眼"性价比最高"（平均反掉更多敌方假眼）；哪些位置插了也白插 |
| 分辨率 | 主用 **172 单位 → 100×100 网格**（覆盖 `MAP_HALF=8600`）；源数据另出 1 / 4 / 16 三档供灵敏度对照 |

---

## 2. 指标定义（逐格）

| 指标 | 内部名 | 含义 |
|---|---|---|
| **平均反假眼**（默认热力） | `avg_jy` | 该格 · 该时间窗 · 该队伍：**每支真眼平均反掉几个敌方假眼** = 该格真眼累计反掉的假眼数 ÷ 该格真眼数 |
| 平均反真眼 | `avg_zy` | 同上，但数的是**反掉敌方真眼**（非核心口径，作参考） |
| 平均总反眼 | `avg_all` | `avg_jy + avg_zy` |
| 总反假眼数 / 总反真眼数 / 总反眼数 | `jy_total` / `zy_total` / `all_total` | 该格累计值（不看平均） |
| 位置出现次数 | `n_sen` | 该格出现过的真眼支数（可作"样本够不够"的判据） |
| 出场场次 | `ng` | 该格涉及多少场比赛 |

**反眼归因规则**（关键）：一支敌方假眼被反掉时，归属给"**覆盖该地点的、异阵营的、当时存活的、距离最近的、且在 `TRUE_SIGHT_R=1050` 单位内的真眼**"。1050 内没有己方真眼 → **不归属**（可能是宝石/其他人反的）。反真眼同理。

**时间窗**（按**真眼放置时**的游戏时钟分窗；0:00 = 号角）：

| 窗口 | 区间 | 按钮 |
|---|---|---|
| W0 | `-1:30 – 7:00`（号角前也归入此窗） | `0-7分` |
| W1 | `7:00 – 15:00` | `7-15分` |
| W2 | `15:00 –` | `15分后` |
| — | 不筛 | `全部` |

**队伍**：天辉（team=2）/ 夜魇（team=3），按**真眼所属队伍**统计。

---

## 3. 数据链路

```
dems/db_full/<league>/<match_id>.db          ← 底层 dota_parse 产出（解析层，别改）
        │
        │  analysis/q5_ward.py            （读 combat_log + game_events + entity_snapshots）
        ▼
analysis/output_q5/*                          ← 中间产物（逐格 / 逐支眼）
        │
        │  analysis/build_q5_html.py      （只需 172 档 + 队名 + 地图 PNG + 眼图标）
        ▼
analysis/output_review/q5b_ward_viewer.html   ← 单文件 viewer（7.8 MB，内嵌 base64）
        │
        │  copy → publish_repo/ → git push
        ▼
https://bigfatblackwhale.github.io/DSH-Dota2/q5b_ward_viewer.html
```

**用到的表/字段**（`db_full` 里每个 match 一个 sqlite）：

| 用途 | 表 | 关键字段 |
|---|---|---|
| 插眼时刻（谁/何时） | `combat_log` (`type_category='item'`) | `inflictor LIKE 'item_ward%'`、`attacker`、`t_cle`、`t_tick`（**排除 `target` 非空的"给队友"**） |
| 实体位置 / 类型 / 队伍 / 出生 | `game_events` (`event_type='ward_placed'`) | `target_id`（=实体类名）、`x`/`y`、`properties.t_tick` / `.team` / `.entity_index` |
| 实体逐秒位置 + **末现** | `entity_snapshots` (`entity_type='ward'`) | `entity_id='ward:<idx>'`、`game_time_sec`、`x`/`y` |
| 销毁（到期/被反） | `combat_log` (`type_category='death'`) | `target LIKE '%ward%'`、`attacker`、`a_team`/`t_team`、`t_cle` |
| 号角（游戏时钟 0:00） | `combat_log` (`type_category='gamestate'`, `value=5`) | `t_cle` |

---

## 4. 关键口径裁定（数字对不对，全看这 5 条）

> 完整推导与实测见 `DEM_FORMAT.md` §C6 / §D5b。**与旧文档冲突时以本节为准。**

1. **判真/假眼 = 实体类名**。`TrueSight`→真眼、无后缀→假眼。
   ❌ 不可用 combat `inflictor` 判型：`item_ward_dispenser` 真/假都会出（实测 observer 829 / sentry 1313）。
   ✅ 以**游戏寿命机制**互验：200 场里所有到期眼的 `(销毁−放置)` 精确 = 该型寿命（真眼 420 / 假眼 360，100% 零偏差）。
2. **放置时刻 = combat `item` 条目的 `t_cle`**，候选窗口 **`[实体首见 −35s, 首见 +2.5s]`**（守卫首见会因视野 PVS 晚于真实插入，最晚 40s+）；无 use 命中则用实体 `t_tick` **经 `t_tick→t_cle` 映射**还原。
   ❌ **禁用 `ward_placed.properties.t_cle`** —— 该字段恒早 **540.26s**（会造出"存活 500~900s 的超长真眼"）。
   ⚠️ **`item_ward_dispenser` 的 use 要同时进 sentry/observer 两个候选池**（否则用眼药盒插的真眼永远找不到自己的 use）。
3. **到期 ⇔ `attacker == target`**（守卫"被它自己"销毁）。**其余一律算"被反"**：敌方英雄、小兵/塔、中立野怪、**同队英雄自毁**（owner 裁定：自统计上计入被反）。
   ⚠️ 死亡条目只认**权威单位名** `npc_dota_observer_wards` / `npc_dota_sentry_wards`；`target LIKE '%ward%'` 会把瘟疫守卫/巫医死亡守卫/剑圣治疗守卫也当眼（实测占 8.43%）。
4. **寿命是「游戏时钟 cle」常量**（真眼 420 / 假眼 360）→ 到期判定与寿命窗口**必须在 cle 空间**（暂停时 cle 冻结、tick 照走，用 tick 会比出几十秒误差）；到期眼的销毁时刻 = 放置 + 寿命，死亡事件只用于**确认**。
5. **销毁与眼"一一对应"**：一次全局匹配，代价 = `|Death.t_tick − (实体末现 − 9s)|`（物理上"实体消失紧跟死亡"）；**每个 Death 只用一次**。
   ❌ 禁止"寿命窗内最早 death"（相邻同队同型眼互相抢死亡）；❌ 不要给 use 设独占（一次 use 被判走后，本眼所有死亡候选会连锁失效）。

---

## 5. 页面功能与交互

**地图区**
- Canvas 画地图（底图 PNG 内嵌），格子按当前指标着色（值热力 + 上限裁切）。
- **缩放**：滚轮。**平移**：拖拽。
- **点格**：点一个方块 → 自动放大进入该格（只显示该格 + 该格真眼反掉的真/假眼位置标注）；**再点同一格** → 缩放回去。
- **点被反眼图标**：锁定显示该笔事件的 **比赛ID + 事件地点坐标 + 双方队名 + 反眼队伍**，直到再点地图任意处。
- **hover**：高亮当前格 / 标注点，顶部状态行显示队伍·时间窗·指标·上限。

**筛选控件（3 组 + 3 滑块）**

| 控件 | 选项 | 默认 |
|---|---|---|
| 队伍 | ☑天辉 ☑夜魇 | 全选 |
| 时间窗 | 全部 / 0-7分 / 7-15分 / 15分后 | 0-7分 |
| 指标 | 平均反假眼 / 平均反真眼 / 平均总反眼 ｜ 总反假眼数 / 总反真眼数 / 总反眼数 | **平均反假眼** |
| 底图透明度 | 0–100% | 35% |
| 值热力上限 | 0.05–3.0（另有"重置自动"） | 1.0 |
| 位置出现次数下限 | 0–30（滤掉样本太少的格） | 0 |

**右侧钻取表**（点格后出现，**跟随当前时间窗 + 队伍筛选**）
列：`match_id ｜ 队伍 ｜ 放置 ｜ 销毁 ｜ 存活 ｜ 反假眼 ｜ 反假眼时刻 ｜ 反真眼 ｜ 反真眼时刻 ｜ 被反?`
时间均为**游戏时钟 mm:ss**；用于直接去录像核对。

---

## 6. 构建 / 部署

```powershell
# 1) 全量重算（970 场，约 20–25 分钟）
python analysis\q5_ward.py

# 2) 重建单文件 viewer（约 1 分钟）
python analysis\build_q5_html.py

# 3) 发布
Copy-Item analysis\output_review\q5b_ward_viewer.html publish_repo\q5b_ward_viewer.html -Force
git -C publish_repo add -A
git -C publish_repo commit -m "..."
git -C publish_repo push origin main
```

- 公网：`https://bigfatblackwhale.github.io/DSH-Dota2/q5b_ward_viewer.html`
- 仓库：`publish_repo/` → `github.com/BigFatBlackWhale/DSH-Dota2`（push URL 已配 token）。
- ⚠️ push 时打印的 `sh.exe: fatal error - couldn't create signal pipe, Win32 error 5` 是 Windows/git 的**无害告警**；**以最后一行的 `xxx..yyy  main -> main` 为准**。
- 校验落地：`git -C publish_repo ls-remote origin main` 应与 `git -C publish_repo rev-parse main` 相同。
- 本机受限环境（schannel 无凭证）**无法抓取公网页面自检**，需人工刷新确认。

---

## 7. 产物清单

| 文件 | 内容 | 谁用 |
|---|---|---|
| `analysis/output_q5/q5_sen_win_172.json` | 逐格 × 时间窗 × 队伍 聚合（指标本体） | **viewer 主数据** |
| `analysis/output_q5/q5_sentry_detail_172.csv` | **真眼逐支明细**（格 `cell_x/cell_y` + 格中心坐标、match、放置/销毁/存活、反假眼/反真眼数、被反?、被反时刻、反眼点列表） | **viewer 钻取表** |
| `analysis/output_q5/q5_matches.json` | match_id → 双方队名 | viewer 高亮 |
| `analysis/output_q5/q5_cell_172.json` | 逐格绝对 + 相对（3 邻域 100/300/600） | 分析层 |
| `analysis/output_q5/q5_cell_matches_172.csv` | 逐格 × match（稀疏，供复核） | 分析层 |
| `analysis/output_review/q5b_ward_viewer.html` | **最终交付物**（单文件，7.8 MB） | owner / 公网 |
| `publish_repo/q5b_ward_viewer.html` | 同上，发布副本 | GitHub Pages |

规模：**970 场 / 真眼 70,421 支 / 假眼 38,813 支**。

---

## 8. 已知边界与坑（务必继承）

1. **Death 无坐标**：销毁只能靠"实体自身末现 − 9s"定位（尾差实测 5–14s，1Hz 量化）→ 用**一一对应**兜住，别退回"窗口最早"。
2. **`entity_index` 会复用**（同场两支眼共用同一 idx）→ **绝不作主键**；当"末现 − 出生 > 寿命+25s"时判为复用、弃用该末现，退回窗口最早。
3. **`ward_placed` 行可能重复**（PVS 抖动）→ 按实体出生（位置+时刻）复合键识别，不要按行数计数。
4. **dispenser 必须靠实体判型**（见 §4.1）；判型错会让真/假眼整套统计错位。
5. **自毁计入被反** → 若那支被"自毁"的敌方假眼恰好落在某真眼 1050 内，会计入该真眼的"反假眼"成功。**对假眼口径残留 ≤0.66%（10/1523，40 场样本），owner 已知悉并接受**。
6. **中立野怪打死真眼**（眼插在野点，1.3% of 真眼死亡）同样计入被反。
7. **时钟量化已消除**：到期眼 `survival` 精确 = 420.0，全量 `max = 420.0`、超过 420 的 **0 条**（前置修复把寿命判定放到 cle 空间后，上一版残留的 132 条 420.5~422.5s 归零）。
8. **右侧钻取表必须跟随"时间窗 + 队伍"筛选**（首版曾漏，已修）；时间窗曾缺"全部"，已补。
9. **前端坑**（首版白屏死机）：`capValue.toFixed` 对 DOM 字符串字段调用、`onmousemove` 里 `px` 作用域 —— 详见 `INTERACTIVE_MAP_PATTERN.md` §9。

> **2026-09 前置修复新增 4 条坑（必读，详见 `DEM_FORMAT.md §C6.9`）**：
> 10. **死亡池只认权威单位名** —— `target LIKE '%ward%'` 会收进瘟疫守卫/巫医死亡守卫/剑圣治疗守卫（80 场占 8.43%）。
> 11. **寿命是 cle 常量，不是 tick 常量** —— 到期判定/寿命窗口必须在 cle 空间，否则被暂停撑大几十秒。
> 12. **`item_ward_dispenser` 的 use 要进两个型的候选池** —— 否则用眼药盒插的真眼找不到自己的 use。
> 13. **PVS 延迟**：守卫首见晚于真实插入（实测最晚 40s+）→ use 候选窗口 `[首见−35s, 首见+2.5s]`；且**不要给 use 设独占**（会把本眼所有死亡候选连锁作废）。
> 14. **FIFO/LIFO 配对已彻底废弃**：旧脚本 `opendota_analysis/ward_survival_heatmap.py`、`ward_heatmap_explorer.py` 读的 `ward_destroyed` 事件在新库**已不存在**，跑不出结果（仅作历史参考）。
> 15. **右删失/截断（已修，机制级；见 `analysis/Q5_RULES_CALIBRATION_LOG.md §19`）**：比赛结束时仍存活的眼**没有到期死亡事件**。⚠ 旧写法把 `MAX(t_cle) FROM combat_log` 当"比赛结束"是**错的**——战斗日志在**远古（Fort）被摧毁后仍记录 360~925s（中位 398s）的结算残留**（含 354 条守卫死亡 / 30 场）。权威结束 = **远古被摧毁的那一刻**（`q5_ward.game_end_cle()`）。改用权威结束后：未被反且"出生+寿命 > 结束"的眼 → `destroy = 结束`、`survival = 结束−放置`、`censored=True`（约 9~10% 的量级，假眼侧实测 9.2~9.9%）。**这会让本页的"平均存活"在末段下降**（原先被高估），被反率不变。`--no-censor-at-end` 可退回旧口径（存活=寿命，是**上界**）。**注意**："placed 计数 > death 计数"正是右删失造成的，与判型无关（判型已用寿命机制验证）。

---

## 9. 验证记录

| 验证 | 结论 |
|---|---|
| **owner 现场核对** `8830423116` | 左上边缘那支真眼 `eidx=3508` (-6248.5, 7396.8)：**8:52.2 插 → 15:46.67 被反**（存活 414.5s）；中心那支 `eidx=2474` (603.8, −983.6)：15:39.07 插 → 16:28.30 被反（49.2s）。**与游戏内一致** |
| 判型互验 | **寿命机制**（非计数）：200 场到期眼 `(销毁−放置)` 精确 = 420/360，\|偏差\|≤0.01s **100%**（9,593 / 5,151） |
| 判型独立源排查 | `probe_truesight` 直读 dem：combat log **不含**实体类名（`total=0`）→ 判型只能来自实体表 |
| 全量健康度 | 真眼 `survival` max **420.0s**，`>420s` **0 条**；判定构成：被反 22,570(32.0%) / 到期 47,851(68.0%)（前置修复后重建，`b2264c6`） |
| 地图方位 | 用建筑校准：Radiant 塔 x<0,y<0；Dire 塔 x>0,y>0 → 中心 (0,0)，**左上 = x<0 且 y>0** |
| 冒烟 | `q5_ward.parse_match` 单场跑通；`build_q5_html.py` 出 15,577 格 |
| **截断修复前后对照（v5 vs v4，从两个版本的 viewer 内嵌数据逐格对比）** | 真眼支数 **70,421 → 70,421**；反假眼归因 **9,384 → 9,384**；反真眼归因 **16,873 → 16,873**；格数 **15,577 → 15,577**；被反支数 **22,570 → 22,570** —— **全部不变**；只有逐支"存活"列变：平均 **331.8s → 308.3s**（−23.5s），标"截断" **0 → 7,879 支**（11.2%），最大存活仍 **420.0s** |

---

## 10. 待办 / 未决

1. **"真眼成功"是否要剔除自毁/野怪击杀**（现按 owner 裁定计入）—— 只需在归因处加一个"击杀者为敌方阵营"的开关。
2. **dem 级"实体类名 + 位置"探针**：现有 probe 没有位置版（`probe_ward.exe` 未编译，机器无 cargo）。若某支眼的类型在游戏里与 DB 不符，需要它来钉死。
3. **移动端 / 分享版**：Q2 有 `*_lite.html` 先例，7.8 MB 单文件在移动端偏重。
4. **上游改善**：若 bottom 层修掉 `ward_placed.t_cle`（坏字段）或给 Death 补位置，本页可直接受益（当前已在分析层绕过）。

---

## 11. 变更历史

| 版本 | 提交 | 内容 |
|---|---|---|
| **v5** | 本次 | **根因修复：比赛结束 = 远古被摧毁**（见 §8 第 15 条）→ 末段真眼"存活"不再按 420s 高估，明细里标 **截断**；聚合指标（反眼数/被反数/格数）逐格核对**完全不变**。与 Q6 viewer 同批修（共享解析层），owner 裁定"修复一并发布" |
| v3 | `d8ac7db` | 到期口径改 `attacker==target`；自毁计入被反（owner 裁定）；到期取常量寿命 → 超长条归零 |
| v4 | `b2264c6` | **前置修复后重建**：死亡池只认权威单位名（去掉 8.43% 非眼 ward）、寿命判定改 cle 空间、dispenser use 双池、PVS 延迟 use 窗口、销毁全局一一对应 → 存活上限精确 420.0 |
| v2 | `7bb1872` | 放置时刻改用 `t_tick→t_cle`（弃坏字段）；销毁改"一一对应"匹配 |
| v1 | — | 首版 viewer（含"全部"时间窗、钻取跟随筛选） |

---

## 附：相关文档

- `STRATEGY/DEM_FORMAT.md` §C1–C6（守卫事件语义 / 陷阱 / 本轮订正）、§D5–D5b（根本解设计 + 落地接线）
- `STRATEGY/INTERACTIVE_MAP_PATTERN.md`（交互式地图通用交互规范，可复用到别的分析）
- `STRATEGY/DEM_WARD_REWRITE_NOTES.md`（守卫重写决策史）
- `analysis/Q5_RULES_CALIBRATION_LOG.md` §14–§17（新架构 / 部署 / 本轮口径订正与全量实测）
