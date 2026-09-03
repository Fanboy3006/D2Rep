# opendota/ — 公开赛事标准指标管道（§8 第 1 步）

对应 ARCHITECTURE.md §3.1（OpenDota API 清单）与 §8 第 1 步。职责：把公开赛事的
**标准指标**（比赛列表 / 队名 / 逐分钟经济差）拉入独立的 **`stats.db`**，
与 `scheduler/` 的 matches catalog（调度/索引，只管本地 .dem 解析流程）**分开、不混用**。

## 数据模型（stats.db，根目录，gitignore 覆盖）

| 表 | 内容 | 粒度 |
|---|---|---|
| `leagues` | 联赛字典（id/name） | 联赛 |
| `teams` | 队伍名称字典 | 队伍 |
| `matches` | match_id/联赛/双方 team_id/start_time/duration/radiant_win/series_id/series_type/game_mode/fetched_at/parse_requested_at | **按 match**（不做 series 聚合；series 口径以后用查询层聚合） |
| `gold_adv` | (match_id, minute, value)，value 为 **radiant 视角**原始 `radiant_gold_adv` | 逐分钟 |

- 视角转换（§3.1 `team_adv()`）：dire 视角值 = -value；`stats_db.gold_adv_for(con, match_id, is_radiant)` 已内置符号翻转。
- 扩展字段一律进 `metadata_json`（TEXT JSON），与 catalog.py 同一设计原则——加字段不需要重建表。

## 用法

```powershell
python opendota/stats_db.py init                    # 建表
python opendota/stats_db.py summary                 # 各表行数
python opendota/stats_db.py matches --league 19719 --limit 20
python opendota/stats_db.py teams --limit 20
python opendota/stats_db.py gold 8960991322 --team radiant|dire
python opendota/stats_db.py join-catalog [--catalog matches.db]  # match_id 关联演示
```

拉取（限流默认 1.2s/次，幂等——已有经济差的场次自动跳过，`--refresh` 强制重拉；
某场 `radiant_gold_adv` 为空会自动 `POST /api/request/{match_id}` 触发解析后重试一次，
仍未成功的留待下次运行重试）：

```powershell
python opendota/fetch_league.py --league 19719 [--sleep 1.2] [--refresh]
```

## 与 catalog（scheduler/matches.db）的关联

两库各司其职：catalog 只登记/调度**本地解析**的录像（含将来 `source='public'`
但实际下载了 .dem 的场次），stats.db 存放公开 API 标准指标。关联按 **match_id**
（应用层 join 或 SQLite ATTACH），不做外键强耦合；`join-catalog` 子命令演示该关联。

## 已知边界（见 ARCHITECTURE.md §3.1/§4.2）

- `radiant_gold_adv` 为空需先触发解析（`/request/{id}`），解析耗时可达数分钟，
  本轮只重试一次，未成功的行留 `parse_requested_at` 标记，下次运行自动重试；
- 录像/重放时效性只影响第 2 步的 .dem 下载，不影响本步标准指标；
- 限流约 60 次/分钟：脚本默认 sleep 1.2s 并对 429/5xx 退避重试。
