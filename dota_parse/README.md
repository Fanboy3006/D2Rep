# dota_parse — Dota 2 录像解析器（ARCHITECTURE.md §8 第 4 步）

将一份 `.dem` 录像解析为 ARCHITECTURE.md §6.2 定义的**通用三表模型**
（`entity_snapshots` / `game_events` / `player_identity`）并写入 SQLite 数据库，
取代最初探测脚本输出的单个 JSON 文件。Postgres 迁移后续再做，表结构与其保持一致。

## 构建与运行

Rust 环境为离线 vendor 模式（见项目根目录 `.cargo/config.toml`、`rust_toolchain_*` 目录）：

```powershell
# 用 vendored 工具链构建 Release
$env:PATH = "F:\D2Rep_project\dota_replay_analyzer\rust_toolchain_x86_64-pc-windows-gnu\bin;" + $env:PATH
cargo build --release --offline
```

运行（需要官方 `sqlite3.dll`，见下方说明）：

```
dota_parse <replay.dem> [output.db] [sample_interval_sec]
dota_parse --info <replay.dem>    # header-only JSON（调度层 catalog 登记用）
```

- 默认输出数据库为 demo 同目录同名 `.db`；采样间隔默认 1 秒（可配置，见 §6.3）。
- 重复解析同一 match 会先删除该 match 的三表旧行再插入（单事务，幂等）。

## sqlite3.dll 说明

解析器通过运行时 `LoadLibrary` 调用官方预编译 `sqlite3.dll`（sqlite.org，x64），
因此**无需任何 C 编译器 / rusqlite 捆绑编译**，也不引入新 crate 依赖。
DLL 解析顺序：`$env:DOTA_PARSE_SQLITE_DLL` → exe 同目录 → 当前目录 → 系统搜索路径。
开发机本地已放置 `dota_parse/sqlite3.dll`（3.53.4，SHA3 校验通过）；该文件被 `.gitignore`
排除（体积小但不在 git 仓库内），**git clone 到新电脑后不存在**——执行
`python tools/fetch_sqlite_dll.py` 重新获取（联网），或从旧机器拷贝一份即可。

## 模块结构（解析层按 §6.4 extractor 模式组织）

| 文件 | 职责 |
|---|---|
| `src/main.rs` | CLI、64MB 栈解析线程、写库编排、写后自检 |
| `src/schema.rs` | 三张通用表的 SQLite DDL（唯一事实来源，含相对 §6.2 的适配说明） |
| `src/model.rs` | 通用行模型（SnapshotRow/EventRow/PlayerIdentityRow）与命名/队伍约定 |
| `src/parse.rs` | header→player_identity 提取器；位置提取器→entity_snapshots；购买/守卫提取器→game_events |
| `src/sqlite.rs` | 极简 sqlite3.dll FFI + PreparedWriter（事务化幂等写入） |

新增分析维度 = 在 `parse.rs` 新增一个提取器（或新 `event_type` / `entity_type`），
不改 schema、不改其他提取器——即 §6.4「新增提取器」原则。

## 数据模型（同 §6.2，细节与适配见 src/schema.rs 文件头）

- `entity_snapshots`：hero 实体 1 秒重采样位置快照；`entity_id` 用 hero npc 名
  （如 `npc_dota_hero_pudge`），`extra` JSON 携带 `z` / `pid` / `player_slot` / `class`；
  `hp` 尽力而为（属性缺失时为 NULL）。
- `game_events`：`purchase`（购买者记录在 combat log 的 target 字段，
  `properties.item` 为物品 npc 名、`properties.item_index` 为战斗日志内部序号而非金币价格，
  见 §6.6 已知限制；价格映射属后续静态字典工作）；`ward_placed` / `ward_destroyed`
  （守卫视野事件，§8 第6步：placed 带坐标/队伍/类型，destroyed 带排眼者与
  dewarded/expired 标记，详见 §6.6「守卫事件提取」）。`event_seq` 处理同一秒同
  actor 的多发事件。
- `player_identity`：每场每玩家一行；`player_slot` 采用 Dota 惯例（天辉 0-4、夜魇 128-132）；
  `team_id` 为头部队伍代码（2=天辉 3=夜魇）；`hero_id`（数字英雄 ID）需要外部英雄字典，暂为 NULL，
  `hero_name` 存 npc 名作为解析层自给自足的权威标识。

## 写后自检

程序在 COMMIT 后直接从数据库回读并打印各表行数、每 entity 坐标范围
（可用于对照泉水坐标合理性：天辉负象限 / 夜魇正象限）与事件分布。
也可用 python 独立复核：

```python
import sqlite3, json
con = sqlite3.connect(r"F:\D2Rep_project\dota_replay_analyzer\8592126358.db")
cur = con.cursor()
for t in ("entity_snapshots", "game_events", "player_identity"):
    print(t, cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
# extra/properties 必须是合法 JSON：
print(cur.execute("SELECT extra FROM entity_snapshots LIMIT 1").fetchone()[0])
print(cur.execute("SELECT properties FROM game_events LIMIT 1").fetchone()[0])
```
