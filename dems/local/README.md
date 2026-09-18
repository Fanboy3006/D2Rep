# dems/local/ — 私人 / 本地录像（Q7B）

这个目录用来放**自己的录像**（练习房、内战、天梯复盘……），和联赛录像（`dems/public/`）完全分开：
联赛目录只读、不动；这里随便加、随便删。

## 目录约定

```
dems/local/<scope>/<match_id>.dem          原始录像（保留；也可直接丢 .bz2 / .zst，工具会自动解压）
dems/local/<scope>/registered/             可选：处理完想归档就挪进来（intake_local.py --move）
dems/db_full/local/<scope>/<match_id>.db    解析产物（Q7 直接吃这个库）
dems/db/local/<scope>/<match_id>.db         预留位置（将来若要"老库"式散装数据）
```

`<scope>` 是你自己定的分组名（一个文件夹 = 一批/一个来源），例如：

- `scrim` —— 练习房/训练赛
- `team_ts` —— 某个队的比赛
- `2026q1` —— 按季度归档
- `steam_76561198…` —— 按上传者分

## 用法（在项目根目录执行）

```powershell
# 1) 把 .dem 拷进 dems/local/<scope>/  （不建 scope 目录就用 inbox）
#    你的录像一般在：<Steam>/steamapps/common/dota 2 beta/game/dota/replays/<match_id>.dem
# 2) 录入 + 解析（自动读录像头拿 match_id/时长/玩家，重复文件不会重复解析）
python scheduler\intake_local.py --scope scrim

# 3) 生成回放页面
python analysis\q7_replay.py 9001661796
python analysis\build_q7_html.py 9001661796 --out analysis\output_review
#    → analysis/output_review/q7_replay_9001661796.html（单文件，双击即开，也可以直接发人）

# 其他常用
python scheduler\intake_local.py --list              # 列出已录入的本地场次
python scheduler\intake_local.py --no-parse          # 只登记不解析
python scheduler\intake_local.py --move              # 处理完把 .dem 挪到 registered/
python scheduler\catalog.py list --source local      # 直接查 catalog
```

## 与联赛场次的关系

- 索引/目录互不干扰：联赛是 `dems/public/<league>/` + `dems/db_full/<league>/`，
  本地是 `dems/local/<scope>/` + `dems/db_full/local/<scope>/`。
- catalog 主键是 **(source, scope, match_id)** —— 同一场比赛若既有联赛录像又有本地录像，两条并存不打架。
- 本地场次没有外部战绩数据（stats.db 只覆盖联赛），页面上的**结果**由录像本身判定
  （哪一方的远古被摧毁），并明确标注"按远古被摧毁判定"。

## 磁盘占用参考（实测）

| 项 | 大小 |
|---|---|
| .dem（原始） | 中位 ~142 MB（练习房可能更大） |
| 解析产物 .db | ≈ 0.38 × .dem（上面这场 86 MB → 76 MB） |
| 解析耗时 | ≈ 0.36 s/MB，单核（86 MB → 61 s） |

所以留一份 dem + 一份 db ≈ `1.4 × dem` 的磁盘；`--move` 只是整理文件位置，不会省磁盘。
