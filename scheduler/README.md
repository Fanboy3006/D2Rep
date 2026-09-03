# scheduler/ — 第 1 层：任务调度 / 元数据（matches catalog）

对应 ARCHITECTURE.md §2 第1层与 §8 第7步。这里只做**索引与调度**：一个轻量
`matches` catalog（独立 `matches.db`）记录每场录像的 来源/路径/解析状态；
**重活（三张通用表）留在每场由 `dota_parse` 产出的逐场 .db 里**，catalog 不复制数据。
第4层分析（`analysis/`）通过 catalog 枚举场次 db，不再要求手传 db 列表。

## catalog（scheduler/catalog.py）

```powershell
python scheduler/catalog.py init                 # 建表（首次）
python scheduler/catalog.py list [--source private|public] [--state parsed|pending|failed]
python scheduler/catalog.py dbs  [--source private|public] [--state parsed]   # 喂 analysis
# 所有子命令可用 --catalog <path> 换库；默认根目录 matches.db（gitignore）
```

**match_id 命名（防撞命名空间）**：public=OpenDota 十进制 id；private=录像头自带
match_id（存在且>0），否则 `manual_<sha256[:12]>`（内容哈希）。

**升级预留**：可扩展字段一律进 `metadata_json`（TEXT JSON）——`--note`、
未来公开赛事的 team/series/赛事名等占位都放这里，加字段不需要重建表。

## 非公开录入（scheduler/intake_private.py）

约定：把 .dem 放进 `dems/private/`（本次只扫该目录），运行：

```powershell
python scheduler/intake_private.py               # 登记 + 立即解析（默认）
python scheduler/intake_private.py --no-parse    # 只登记，状态留 pending
python scheduler/intake_private.py --note "备注" --move
```

流程：sha256 → `dota_parse --info` 读头部（match_id/时长/玩家，无需人工输入）→
幂等登记 →（默认）全量解析到 `dems/db/<id>.db` → 状态 parsed/failed；
`--move` 成功后把 .dem 移入 `dems/private/registered/` 并同步 catalog.dem_path。
重复运行同一文件不会重复插入/重复解析。

## 与公开管道（§8 第1/2步）的衔接

catalog 的 `source` 已支持 `'public'`，公开赛事字段占位进 metadata_json；
实际 OpenDota 拉取/批量下载逻辑后续作为 `source='public'` 的生产者接入同一 catalog。
