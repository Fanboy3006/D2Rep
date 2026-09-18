# Q7B · 本地/个人录像分析（公网上传解析）—— 可行性评估

> 状态：**评估稿（2026-09）**。owner 问两件事：① 开放公网（谁都能上传录像解析）需要什么条件；
> ② 本地录像文件夹与联赛录像如何做地址/命名空间划分。
> 本文所有数字分两类：**【实测】**= 本机跑出来的；**【估算】**= 由实测线性外推。方案不掺想象。

---

## 0. 一句话结论

**数据链路已经通**：现在的 parser 解析任意一场个人录像，产出的库**与联赛库逐表逐类完全一致**，
直接喂给 `q7_replay.py` 就能跑通全流程（我实测跑过，见 §2）。
所以 Q7B 的"分析能力"不是风险；**风险全在"公网开放"这件事本身**（成本、隔离、滥用、隐私、发布形态），
其中三项必须 owner 先拍板：**是否保留 .dem**、**是否要 Steam 登录**、**玩家名要不要公开**。

**数据缺口已闭合（2026 owner 拍板"CD 面板需要"，已实施并验收）**：技能 CD 三态面板原本依赖
`dems/db/`（Q5 版老库）的 `ability_cd_*` / `item_cd_*`，而当前 parser 已停止产出这些事件
（提取器在源码里但是死代码，见 §2.3）。现已把 `AbilityExtractor` + `JungleExtractor`
**重新接线**，个人录像解析出来的主库里就带 CD/gold/neutral 事件；`q7_replay.py` 在没有老库时
**回退读主库**。两场金标准逐项对比 **13 类事件全部相等**，`load_cd()` 产出（103 键 / 121 技能区间 /
8 道具区间）与老库版完全一致。代价：单场解析 109s → 121~125s。**Q7B 现在没有任何数据缺口。**

---

## 0.5 实施状态（2026-09：owner 定了 L1「只给朋友」，已落地第一部分）

| 项 | 状态 |
|---|---|
| 目录划分 | ✅ `dems/local/<scope>/<mid>.dem`（原始，保留）+ `dems/db_full/local/<scope>/<mid>.db`（Q7 主库）+ 预留 `dems/db/local/<scope>/`；联赛目录不动、只读 |
| catalog | ✅ 主键改成 **(source, scope, match_id)** 复合键（`matches.db`，自动迁移旧表）；`source` 增加 `local` |
| 录入工具 | ✅ `scheduler/intake_local.py`：扫 `dems/local/<scope>/` → sha256 → 读录像头 → 登记 → 全量解析到主库；幂等、可 `--no-parse`、可 `--move`；**支持直接丢 .bz2/.zst 自动解压** |
| Q7 侧 | ✅ `find_db` 递归 glob 天然认 `db_full/local/<scope>/`；`find_old_db` 加了 local 分支；无战绩数据时**用录像本身判定胜负**（哪方远古被摧毁），页面标注"按远古被摧毁判定" |
| 索引页 | ✅ `python analysis/build_q7b_index.py [--rescan]` → `analysis/output_review/q7b_index.html`：只列 `source=local` 的场次，按 scope 分组可筛、可按 match_id/英雄/玩家名/备注搜、可排序、已生成页面的直接给"打开"链接、没生成的给一条命令；**页面上明确写着"只在本地看、不要发布"**（含玩家名）。测试：`node analysis/q7b_index_itest.js`（28 项） |
| 首个录入样本 | ✅ **9001661796**（练习房 lobby_type=1，35:17，夜魇胜）：86 MB dem → 76 MB 库，解析 61s；页面 `analysis/output_review/q7_replay_9001661796.html`（5.3 MB）+ lite（0.55 MB） |

**踩到的坑（留档）**：Valve 的 `replay_url` 现在**实际给的是 zstd**（魔数 `28 b5 2f fd`），
后缀却仍写 `.dem.bz2` —— 按 bz2 解会报 `Invalid data stream`。`intake_local.py` 已按魔数/后缀自动识别
（bz2 / zst / zstd），解压后照例校验头 8 字节 `PBDEMS2\0` 才算拿到可解析的录像。

**还没做（owner 没要，先记着）**：给朋友的传输方式（现在直接把那个 HTML 发过去即可）、
清理/保留策略（磁盘紧张时按 `scope` 整批删）。

---

## 1. 【实测】成本事实（决定公网可行性的全部关键数字）

| 指标 | 实测值 | 说明 |
|---|---|---|
| .dem 体积 | 970 场共 **153 GB**；中位 **142 MB**、平均 157 MB、最大 477 MB | 联赛 7 个文件夹 |
| 表头读取 `dota_parse --info` | **0.16 s**（305MB）/ **0.4 s**（438MB） | 拿到 match_id / 时长 / 10 名玩家（含 steam_id、昵称） |
| 全量解析（1Hz，Q7 口径） | **109 s / 305 MB**、**154 s / 438 MB** → **≈0.36 s/MB** | 单核；中位 142MB ≈ **51 s【估算】** |
| 解析峰值内存 | **1,126 MB**（438MB dem，≈2.6×） | 【实测】每个 worker 必须按 **≥1.5 GB** 预留 |
| 产出 `db_full` | **147 MB / 305 MB dem**、**165 MB / 438 MB dem** ≈ **0.38×dem** | 970 场合计 **103 GB** |
| 现有 `dems/db`（老库） | 971 场 26.7 GB | Q5 版散装 extractor 产物（含 CD/gold/purchase/ward_use/smoke） |
| 本机 | 16 逻辑核 | 现有批量脚本默认 6 worker |
| 单文件 viewer | **8.94 MB** | JSON 载荷 4.06（其中 ±45s 明细 1.96、位置 0.73、技能图标 0.27）＋底图 1.97＋日志图标 CSS 1.42＋骨架 1.49 |

**换算成"每场录像的代价"**：上传 **≈150 MB** ｜ 解析 **≈51 s 单核 / 峰值 ≈1 GB 内存** ｜ 磁盘 **≈150 MB**（保留 dem 则 ≈300 MB）。

---

## 2. 【实测】Q7 管线对"新解析的个人录像"的兼容性

### 2.1 新解析产物 == 历史 `db_full`（逐类核对）

我用当前源码重新解析了 `8955197224.dem`（305MB，109s），产物与历史 `db_full` 对比：

| | 新解析 | 历史 db_full |
|---|---|---|
| combat_log | 188,315 行 / 19 种 (category,type) | **完全相同** |
| entity_snapshots | 134,082 行 | 相同 |
| game_events | 215（ward_placed 157 / building_spawn 36 / building_destroyed 22） | 相同 |
| 文件大小 | 154.0 MB | 154.0 MB |

→ 当前 parser **可复现**联赛库，字节级表规模一致。

### 2.2 直接喂给 Q7（实测）

把新解析的库放到 `.tmp/localprobe/8955197224.db` 跑 `q7_replay.py`：

- 位置覆盖 3,786 秒 × 10 英雄 ✓
- KDA/正反补 与联赛版**逐人一致** ✓
- ±45s 明细 **140,665 条**，与联赛版一致 ✓
- 四类归属对账 **全部一致** ✓
- 眼位 157 支 / 烟雾 20 次 / 时间轴 96 条 ✓
- 时长对账 ✓（stats.db 有本场）
- **技能/道具 CD：源写的是 `dems/db/19719/8955197224.db`** —— 即它按 match_id 找到了**联赛老库**；
  个人录像没有这个库 → CD 面板为空（页面已有"不提供"的如实回退）

### 2.3 缺口根因（已定位到代码）

`dota_parse/src/parse.rs`：

- `AbilityExtractor`（读实体 `m_fCooldown` / `m_iLevel`，产出 `ability_cd_start/end`、`ability_known/learn`、
  `item_cd_*`、`smoke_count`）与 `build_ability_event_rows()` **都还在源码里**；
- 但 `AbilityExtractor::default()` **没有任何调用点**，`build_ability_event_rows()` 也**没有调用点** →
  **死代码**。`assemble()` 里的注释写明：*"Combat narrative now lives in `combat_log` (all types).
  `game_events` keeps only the entity-anchored space/state layer: ward_placed + building + 号角锚点"*。
- 结论：老库里的 `gold`(5669) / `ability_cd_*`(8240) / `neutral_kill`(1932) / `purchase`(864) /
  `ward_use`(66) / `item_cd_*`(92) / `smoke_count`(22) 这些事件，**当前 parser 一条都不写**。

**补法（owner 已选①，✅ 已实现并验收）**：
① **重新接线（已做）**：在 `parse_replay()` 里注册 `AbilityExtractor` 与 `JungleExtractor`，
   把 `build_ability_event_rows()` / `build_jungle_event_rows()` 的产出并进 `game_events`。
   实测与老库**逐事件相等**（见 §2.3 的表），单场耗时 109 → 121~125s。
② ~~不补~~（未采用）。

> Q7B **不受影响**的部分（已验证）：地图/位置/净值/经济经验差/KDA/±45s combat log/眼位/烟雾/时间轴/状态胜率。

---

## 3. 开放公网需要什么条件（按"最少投入 → 真开放"分层）

### L0 · 只在自己机器上（今天就能有，0 额外成本）

现成的东西已经够了：`scheduler/intake_private.py` + `dems/private/`（见 §4）。
把个人录像丢进目录 → 登记（sha256 + `--info`）→ 全量解析 → 按 §4 的地址规则生成 Q7B viewer。

### L1 · 给朋友用（内网/单机 + 一个上传页，1~2 天）

- 最小 HTTP 服务：上传（支持大文件分段/直传）→ 落地到 `dems/local/inbox/` → 入队 → worker 解析 → 生成 viewer → 返回链接
- 必备：**全站并发上限**（= 核数/2，因为每 worker 峰值 ~1GB）、单文件上限（建议 600MB）、
  单 IP 配额（如 5 场/天）、解析超时（**沿用现有 900s**）、失败原因回显、任务状态页（直接读 `matches` catalog）
- 仍然必须：**低权用户 + 独立目录**（不要让上传进程能写 `dems/public` 与 `stats.db`）

### L2 · 真·开放给任何人（下面每一条都是"必须"，不是"最好有"）

| # | 条件 | 为什么 / 具体做法 |
|---|---|---|
| 1 | **把 .dem 当恶意输入做沙箱** | parser 解析的是任意二进制：畸形文件可能崩/死循环/爆内存（峰值实测 2.6× 文件大小）。做法：容器（只读根、无网络、seccomp）+ 每任务 CPU/内存/时间 cgroup 限额 + 独立 uid + 临时目录隔离；worker **不得**能读联赛目录与 `stats.db` |
| 2 | **输入校验与边界** | 校验 `PBDEMS2\0` 魔数、大小上限、拒绝/限制压缩包（bz2 炸弹）、sha256 去重（同一文件不重复解析）、match_id 冲突处理 |
| 3 | **配额与限流** | 匿名开放必然被刷：每 IP/账号 N 场/天、令牌桶、队列公平、封禁名单；最好**要求 Steam 登录**（见 #5） |
| 4 | **存储与保留策略（钱的主要来源）** | 默认**解析后删 .dem**（省 150MB/场）；db 保留 N 天（建议 7）后自动回收；磁盘水位告警。公式：`磁盘 = 日上传数 × 150MB × 保留天数` |
| 5 | **隐私/合规** | dem 头里就有 **10 名玩家的 steam_id + 昵称**（含陌生人）。建议：**Steam OpenID 登录** → 校验"上传的录像里确实有本人"（头里能查到）→ 自然限流 + **玩家名只对本人可见、对公众脱敏**（只显示英雄/阵营）；提供删除请求入口 + 隐私说明；遵守 Valve 条款、明确非商业 |
| 6 | **发布形态要改（否则撑不住）** | 现在每场是 **8.94 MB 单文件 HTML**：1000 场 = 8.7 GB 静态站，而且每打开一次就下 9MB。建议拆成：**页面壳（~1.5MB，含底图/CSS/JS，可被所有场次共用并长期缓存）+ 每场 payload（JSON，gzip 后约 0.8~1.2MB，按需拉取）**；或"按需生成 viewer + 到期回收"；再配对象存储/CDN |
| 7 | **可观测与运维** | 任务状态（排队/解析/失败原因）、成功率、队列长度、磁盘/内存水位、限流日志、**别把内部路径/堆栈回显给用户** |
| 8 | **成本预算** | 每场 ≈ 150MB 入站 + ≈51s 单核 + ≈150MB 磁盘。100 场/天 → 入站 15GB/天、CPU 92 分钟/天、磁盘 15GB/天（7 天保留≈105GB）；1000 场/天 → 入站 150GB/天、需 4~8 核常驻、保留 7 天≈1TB。CPU 便宜、**存储与带宽是大头** |

---

## 4. 本地录像 vs 联赛录像：地址 / 命名空间怎么分（Q2 的答案）

### 4.1 现状（要在此基础上改，不要另起一套）

- 联赛：`dems/public/<league>/<mid>.dem` → `dems/db_full/<league>/<mid>.db` ＋ `dems/db/<league>/<mid>.db`
- catalog 已有：`matches(match_id PK, source ∈ {private,public}, dem_path, db_path, parse_state, dem_sha256, metadata_json)`
- 录入器已有：`scheduler/intake_private.py`（扫 `dems/private/`，sha256 → `--info` 读头 → 幂等登记 → 全量解析）
- Q7 查找：`find_db` 用**递归 glob** `dems/*/**/<mid>.db`（第一位目录名会被当成 league）；
  `find_old_db` 只扫 `dems/db/*/<mid>.db`

### 4.2 建议的划分（最小改动、向后兼容）

**① 物理目录：把"来源"提为第一级，联赛保持只读**

```
dems/public/<league>/<mid>.dem           联赛 dem（现状，不动，只读）
dems/db_full/<league>/<mid>.db           联赛主库（现状，不动，只读）
dems/db/<league>/<mid>.db                联赛老库（现状，不动，只读）

dems/local/<scope>/<mid>.dem             个人/本地 dem
dems/db_full/local/<scope>/<mid>.db      Q7B 主库
dems/db/local/<scope>/<mid>.db          Q7B 老库（若 §2.3 选择补 extractor，则两张都在 local 下）
```

- `<scope>` = **可读的来源标签**，例如 `steam_76561198…`（上传者）、`team_ts`、`scrim_2026q1`、
  `inbox`（未分类）。作用：一个文件夹=一次导入批次/一个来源，便于整批删、整批分享。
- **为什么这样能不痛**：Q7 的 `find_db` 是递归 glob，`dems/db_full/local/<scope>/<mid>.db` 照样能命中，
  只是返回的"league"变成 `local` → Q7B 里把它当 `scope` 用即可；
  **需要同步改的只有 `find_old_db`**（现在只扫一层 `dems/db/*/`，要支持 `dems/db/local/<scope>/`）。

**② 命名空间与主键（防撞，必须改）**

- 同一场比赛**可能既有联赛录像又有本地录像** → 现在的 `match_id` 单列主键**会撞**。
  改成复合主键 **`(source, scope, match_id)`**（`scope` 提列并建索引）。
- 本地场次命名：头部 match_id > 0 → 用它；否则 `local_<sha256[:12]>`（沿用你现有规则，把 `manual_` 前缀改成 `local_` 更直观）。

**③ 地址（URL）与索引页**

- 单场：`local/<scope>/<mid>.html`（或 `q7b_local_<scope>_<mid>.html`）——把 scope 放进 URL，便于按文件夹分享整批。
- 索引：**按 source/scope 分区**，不要和联赛场次混在一张表里：联赛场次有 `stats.db` 的队名/胜负/时长列，
  本地场次没有（列语义不同）；建议 `q7b_index.html?source=local&scope=steam_…` 独立筛选、独立默认值。

**④ 权限与清理**

- 联赛目录（`dems/public`、`dems/db*` 里非 local 部分）设**只读**；清理/重算脚本**只允许作用于 `local/`**。
- 保留策略：`local/<scope>/` 支持"整批过期删除"（catalog 里记 `expires_at`）。

**⑤ catalog 增列**

- `scope`（提列 + 索引）、`visibility`（private/public/link-only）、`owner_steam_id`、`expires_at`、`folder`（原始文件夹名）。
- 其余可扩展信息继续走 `metadata_json`（现有设计已预留）。

---

## 5. 需要 owner 拍板的 6 件事

1. **Q7B 的定位**：只做本地（L0）？还是做到 L1（朋友上传）？还是真开放公网（L2）？——决定后面所有工作量。
2. **技能 CD 面板**要不要（§2.3）：补 Rust extractor（0.5~1 天）还是接受空面板？
3. **.dem 是否保留**：默认解析后即删（省一半磁盘）还是保留（便于重解析/复现）。
4. **是否要求 Steam 登录**：这是最省事也最有效的限流+隐私方案（能校验"录像里有本人"）。
5. **玩家名/steam_id 公开与否**：默认建议脱敏（公众只看英雄，本人可看全）。
6. **发布形态**：是否把 viewer 拆成"页面壳 + 每场 payload"（现在是每场 8.94MB 单文件，公网不划算）。

---

## 附：本次评估用到的实测命令（可复现）

```powershell
# 表头读取（快，用于登记）
dota_parse\target\release\dota_parse.exe --info dems\public\19719\8955197224.dem
# 全量解析（1Hz = Q7 口径）
$env:DOTA_PARSE_SQLITE_DLL="dota_parse\target\release\sqlite3.dll"
Measure-Command { dota_parse\target\release\dota_parse.exe <dem> <out.db> 1 }
# 新解析产物直接喂 Q7（验证兼容性）
python analysis\q7_replay.py <新解析的.db> --no-write
# 现有个人录像录入链路
python scheduler/intake_private.py --dir dems/private --note "scrim" --move
```
