# ⑧ 下载 / 分档 Agent（DOWNLOAD_AGENT）

> **一句话**：接管录像下载 + 分档存档 + 报告容量 + 通知数据层。是"录像获取"这条线的唯一负责方，取代原先数据层承担的下载职责。

## 职责（只做这些）
1. **自动下载录像**：按 owner 指定的研究范围，从数据源拉取 .dem。
2. **分档存档**：按统一的"录像属性规范"分类存好，并持续维护一个"要研究什么"的清单表。
3. **报告硬盘剩余容量**：下载/存档后报告 F 盘剩余空间。
4. **通知数据层**：录像有更新时，告知数据层"录像已就绪"（不直接触发数据层脚本，只发通知）。

## 明确不做的
- 不做数据分析 / 解析 / 热图 / viewer（那是数据层）。
- 不修地图、不标定坐标（那是地图层）。
- 不替 owner 决定"研究什么"（owner 在清单击表里定）。

---

## 1. 数据源

### a) 联赛（已有，沿用 OpenDota）
- 沿用现有 `opendota/get_league.py`、`list_matches.py`、`batch_download.py`（OpenDota `replay_url` 下载，无 Steam Key 需求）。
- 管理多个 league_id，逐个更新、增量下载新场次。

### b) 高端路人局（新，D2PT · 待探路）
- **目标**：略低于 8500 分（≈900-1000 名）的 party vs party 高端路人局，欧洲服务器限定。
- **分档依据**：D2PT 有 "7000+ MMR" 分档统计。
- **优先级行动**：
  1. 先**研究 D2PT 是怎么算出这个 MMR 分档的**（数据来源、口径、更新机制）。
  2. 若不清楚，**直接爬 D2PT 的比赛编码（match id）**，限定**欧洲服务器**。
- **诚实边界**：这条线是**可行性探路**——"能否以可控成本获取 + 过滤高端路人 replay"需先验证；解析量 2-3 个数量级的 CPU 成本是真实约束。**在可行性确认前，高端路人局下载是被"预留/探路"的能力，不保证一定规模化。**

### ③ 录像属性文档
- download agent 记录/查询录像属性需要一份**统一的"录像属性规范"**（字段见 §3）。数据层需要时，查这份属性。

---

## 2. 硬盘与存档位置

- **存档**：落在 **F 盘项目下**（回退到 workspace 内，避免 J 盘每次写入需升级 permission）。建议 `dems/` 下新增下载分类目录（如 `dems/download/` + 分档子目录），或按分档规则组织。
- **不再用 J 盘**（J 盘超出 workspace，每次写都要升级权限，成本高）。
- **容量报告**：下载/存档后，报告 **F 盘剩余容量**（用 `Get-PSDrive F` 或磁盘空间 API）。

---

## 3. 录像分档体系（download agent 负责维护）

### 3.1 研究清单表（owner 维护）
- 一张表，owner 在其中指定"要研究什么"（哪些联赛 / 哪些分档 / 是否含高端路人）。
- download agent **读取这张表**决定下什么；owner **维护**这张表。
- 字段建议：`target_id / 类型(league/路人) / 名称 / 启用 / 日期范围`。

### 3.2 录像属性规范（统一字段，可扩展）
每条下载的录像，记录以下属性（作为数据库/清单字段）：

| 字段 | 说明 |
|---|---|
| `match_id` | 比赛 ID |
| `source` | `league` / `public_high`(高端路人) / `private`(训练赛，预留) |
| `league_id` | 若 source=league，所属联赛 |
| `mma` / `分档` | 高端路人局的 MMR 分档（如 7000+ 档） |
| `version` | 游戏版本号 |
| `date` | 比赛日期/下载日期 |
| `server` | 服务器区域（如 EU） |
| `file_path` | 存档路径 |
| `file_size` | 文件大小 |
| `dl_time` | 下载时间 |
| `status` | 已下载 / 待解析 / 有误 |

> **可扩展**：这套属性规范是维度的最小集。以后要加新维度（如赛事类型、patch、阵容），由**新的下载管理层负责维护**（本 agent 负责维护这套规范）。

---

## 4. 通知数据层

- 下载 + 分档 + 属性记录完成后，**通知数据层"录像有更新"**。
- **形态**：不发指令、不直接触发数据层脚去。只让数据层知道"有新录像就绪"，数据层自行读取新录像属性 / 路径。
- 信息载体：更新"录像属性清单"（§3.2）或写一个状态文件，数据层读它。

---

## 5. 状态 / 交接

- **接管**：原来数据层的下载职责（list_matches / batch_download / parse_public 等）移交本 agent。数据层不再负责下载，只做分析。
- **新增能力**：高端路人局获取（D2PT 探路中）。
- **边界**：下载/分档/容量/通知归本 agent；分析/解析/地图/方法论归各自的层。

---

## 6. 待 owner 决定的开放项
- 高端路人局**数据源最终确认**（D2PT 分档口径 vs 直接爬 match id）。
- 是否纳入"private 训练赛"（预留，现未启用）。
- 分档目录的**具体组织**（按 league / 按日期 / 按 source）——由本 agent 负责设计，可反馈 owner。

---

## 附录 A：设计定稿（owner 已确认，2026-XX-XX）

> 本节是对本 agent **如何落地**的定稿设计（模块/数据模型/CLI/分档目录/通知协议）。
> 仅作架构说明，不代表代码已完成——状态：**设计已定稿，代码待实现**。

### A.0 模块组织（新增 `download/` package，与 `opendota/`、`scheduler/` 平级）

```
download/
  __init__.py
  agent.py            # 编排入口 sync/inventory/capacity/d2pt（CLI）
  study_list.py       # 研究清单表读写（owner 维护侧 + agent 读取侧）
  inventory.py        # 录像属性库（§3.2 规范）SQLite
  archive.py          # 分档目录组织 + 落盘/移动/校验
  capacity.py         # F 盘剩余容量报告（[System.IO.DriveInfo]）
  notify.py           # 写"录像已就绪"状态文件
  d2pt/
    README.md         # 可行性探路状态（诚实边界）
    probe.py          # 探路骨架：MMR 分档口径研究 / 爬 match id（未验证）
  README.md
```

`opendota/list_matches.py`、`opendota/batch_download.py` **不改、直接复用**（断点续传/看门狗均成熟）。

### A.1 研究清单表（owner 维护，agent 只读）→ `download/study_list.json`

owner 改清单**无需任何工具**，直接编辑 JSON。

```json
{
  "targets": [
    {"target_id": "league:19719", "type": "league", "name": "TI2026 正赛",
     "league_id": 19719, "enabled": true, "date_from": null, "date_to": null},
    {"target_id": "public_high:7000", "type": "public_high", "name": "高端路人 7000+ EU",
     "mmr_band": "7000+", "server": "EU", "enabled": false, "date_from": null, "date_to": null}
  ]
}
```

### A.2 录像属性库 → `download/inventory.db`

固定核心列 + `metadata_json`（与 `catalog.py`/`stats_db.py` 同一设计原则，加字段不重建表）：

```sql
CREATE TABLE IF NOT EXISTS replays (
  match_id   TEXT PRIMARY KEY,     -- 统一字符串（public 用十进制 match id）
  source     TEXT NOT NULL CHECK (source IN ('league','public_high','private')),
  league_id  INTEGER,              -- source='league' 时用
  mmr_band   TEXT,                 -- source='public_high' 时的分档，如 '7000+'
  version    TEXT,                 -- 游戏版本号（下载时可解析则填，否则 NULL）
  date       TEXT,                 -- 比赛日期 / 下载日期（ISO）
  server     TEXT,                 -- 如 'EU'
  file_path  TEXT,                 -- 分档后存档路径
  file_size  INTEGER,              -- 字节
  dl_time    TEXT,                 -- 下载时间
  status     TEXT NOT NULL DEFAULT 'downloaded'
             CHECK (status IN ('downloaded','pending_parse','error')),
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

> `status` 映射：`downloaded`(已下载) / `pending_parse`(待解析) / `error`(有误)。
> `private` 枚举保留（预留、未启用）；研究清单默认不含 private，须 owner 显式开启。

### A.3 CLI

```
python download/agent.py sync                        # 全量：研究清单 → 下载+分档+记属性+通知
python download/agent.py sync --source league        # 只跑联赛线
python download/agent.py sync --source public_high   # 只跑高端路人线（当前探路骨架）
python download/agent.py sync --adopt-existing       # 回填存量 dems/public（只建索引，不改文件位置）
python download/agent.py inventory list [--source ...] [--status ...]
python download/agent.py inventory stats
python download/agent.py capacity                    # 只报 F 盘剩余
python download/agent.py d2pt probe                  # 探路（未验证）
```

`sync` 幂等流程：读 `study_list.json`（只取 enabled）→ 联赛线调 `list_matches.py` + `batch_download.py` 增量下载 → 分档进 `dems/download/` → 补写 `inventory.db`（match_id 幂等）→ `capacity.py` 报容量 → `notify.py` 写 `.ready`。

### A.4 分档目录（owner 确认：`source/<league>` + 注册表索引）

```
dems/download/
  league/<league_id>/<match_id>.dem
  public_high/<mmr_band>/<match_id>.dem
  private/<...>/<match_id>.dem          # 预留，未启用
  .ready                                # 通知数据层的状态文件
```

- 磁盘按 source/league（或分档）**人可导航**；查询以 `inventory.db` 为**唯一真相源**。
- **存量并存**：`dems/public/<league>/`（970 场）**不改文件位置**，只通过 `sync --adopt-existing` 回填索引进 `inventory.db`。

### A.5 通知数据层（只通知，不触发）

- `dems/download/.ready`：一个 JSON，记录本次新增 `match_id` 列表 + 时间戳 + 属性摘要。
- **只做信号**：数据层探测到 `.ready` 变新后，自行去 `inventory.db` 拉新录像属性/路径。
- **不做**：spawn 数据层脚本/发指令/设解析任务。

### A.6 D2PT 高端路人线（探路，诚实边界）

- `d2pt/probe.py` 只做两件**可验证**的事：(a) 研究 D2PT "7000+ MMR" 分档口径（数据来源/更新机制）；(b) 口径不明则尝试直接爬 match id、限 EU server。
- `d2pt/README.md` 明示：**可行性未验证**，解析量 2–3 个数量级 CPU 成本是真实约束；确认前仅"预留/探路"，不保证规模化。
- `inventory` 的 `source='public_high'` 字段已预留，数据模型不欠账。

### A.7 已确认的决策回执

| # | 讨论点 | 结论 |
|---|---|---|
| 1 | 存量 970 场回填 | **是**——只回填索引、不改文件位置（`sync --adopt-existing`） |
| 2 | D2PT 口径 | **两条都探**，先出结论再定；本轮只做骨架，不真爬取 |
| 3 | `dems/download` 与 `dems/public` 并存 | **确认并存**，不动 `dems/public` |
| 4 | `private` 训练赛 | 预留、不启用；`source` 保留枚举、清单默认不含 |
| 5 | 通知载体 | `.ready` 只作信号，`inventory.db` 作唯一真相源 |

### A.8 当前状态

- 设计：**已定稿**（本节为交付物）。
- 代码：**未实现**（`download/` 目录尚未创建）。下一步由本 agent 按 A.0–A.7 实现。
