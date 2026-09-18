# Project Checkpoint — D2Rep Q5 重建(自 bigfatblackwhale.github.io 部署)

> 2026 会话 checkpoint。包 括:项目文件现状、GitHub 上传方式、以前重大事件、新架构状态、待办。
> 目的:上下文丢失后可据此完全恢复。

---

## 1. 项目定位
DOTA 2 职业比赛数据分析(多层协作)。当前主线 = **Q5 真眼(Sentry)研究**,Q5B = 交互 canvas viewer。
- 数据源:970 场官方 `.dem`(`dems/public/<league>/<match>.dem`)。
- 解析器:`dota_parse`(Rust,source2-demo),落 SQLite。
- 数据字典/规则:`analysis/Q5_RULES_CALIBRATION_LOG.md`(权威)。

## 2. 项目根 git 状态
- 仓库:`https://github.com/Fanboy3006/D2Rep.git`,分支 `main`,commit `1eea1b3`。
- 工作区 = `F:\D2Rep_project\dota_replay_analyzer`。

## 3. GitHub Pages 上传方式(✅ 已确认,可直接用)
- **发布仓库**:`publish_repo/`(项目根下,独立 git repo)= `https://github.com/BigFatBlackWhale/DSH-Dota2.git`(push URL 带 token)。
- **发布脚本**:`publish.ps1`(项目根)。逻辑:
  - `$repo=publish_repo`, `$src=analysis/output_review`, `$files=(q2/q3/q1...).html`
  - 把 viewer html复制到 `publish_repo`, `git add -A` + `commit` + `push origin main`。
  - 不需要我登录;token 已配进 remote。
- **公网 URL**:`https://bigfatblackwhale.github.io/DSH-Dota2/<file>.html`
  - Q1: `q1_value_zones_viewer.html`
  - Q2: `q2_all_teams_viewer.html`
  - Q3: `q3_rel_viewer.html`
  - 单场动态: `viewer_8822238357_lite.html` 等。
- **部署方式**:把 `analysis/output_review/q5b_ward_viewer.html` 拷进 `publish_repo`,手动 `git add/commit/push`(或写一个类似 publish.ps1 的 q5 版)。

## 4. 以前的重大事件/教训(记录)
1. **Q1 价值分区** → `q1_value_zones_viewer.html`,已部署(URL 见上)。
2. **Q2 全队比较** → `q2_all_teams_viewer.html`,已部署。
3. **Q3 相对经济** → `q3_rel_viewer.html`,已部署。
4. **Q5 viewer 曾"死机"**:前端 JS bug(非数据问题)。
   - Bug A:`capValue.toFixed` —— clamp 用 DOM range 的 `.max/.min`(字符串)赋给 `capValue`,导致 `capValue.toFixed` 每次 throw。修复:clamp 用数字运算再回写。
   - Bug B:`onmousemove` 里 `const px/py` 声明在 `if(drag){...}` 块内、块外引用 → `px is not defined`。修复:提到块外。
   - 方法:通过 **Chrome DevTools 9222 端口**用 Node 全局 WebSocket 连 CDP,eval + 订阅 `Runtime.enable`,抓到真实报错。
5. **守卫(眼位)时间换算**是核心难点,经历多条弯路:
   - 误以为"插眼无 combat-log 记录" → 实际是 `DotaCombatlogItem` + `inflictor=item_ward_*`(插眼),`DotaCombatlogDamage/Death` + `tgt=npc_*`(反眼)。
   - **"给队友"** 要排除:`uses Sentry Ward on <英雄>` = target 非空 / tgt_self=false。
   - **item_ward_dispenser** 用一次产1只眼(真/假不确定),需实体判型。
   - **entity_index 复用**(eidx=2876 两支眼共用)→ 不能作主键。
   - segmap/顺序配对/贪心全是"精修",被废弃。**根本解 = GamerulesProxy 官方时钟 + combat_log 通用表**。

## 5. 新架构(关键!2026 底层 agent 已落地)
- **新库** `dems/db_full/<league>/<match>.db`(**970 场全量**,独立,不覆盖 Q5 版 `dems/db/`)。
- **`combat_log` 表**(一行=一条 combat 条目,`type_category` 枚举=toggle,全类型全量不聚合)字段: `match_id/event_seq/t_cle/t_tick/type_category/type/attacker/target/damage_source/inflictor/value_name/value/health/location_x/location_y/a_team/t_team/stack_count/modifier_duration/modifier_elapsed/ability_level/assist_players/gold_reason/xp_reason/event_location/is_attacker_hero/is_target_hero/is_target_building/raw_json`。
- `type_category ∈ {damage,healing,ability,item,modifier,death,playerstats,gamestate,...}`。
- **散装 combat extractor 已删**:`game_events` 只剩 `building_spawn/destroyed` + `ward_placed`(实体空间层)。
- **实体空间层**保留:`entity_snapshots`(ward/hero/building/networth)、`game_events.ward_placed`(位置/型/队/t_cle)。
- **`combat_log` 分布**(8946650558):damage 25945/modifier 19703/xp 3617/death 3547/healing 3488/gold 2985/ability 2097/item 1939/playerstats 239/gamestate 9。
- 依据:`STRATEGY/COMBAT_LOG_REWRITE.md`(交接文档)、`STRATEGY/DEM_FORMAT.md` 附录 D(D6)。

## 6. 守卫数据新路径(从 db_full 读)
- **号角基值** = `combat_log WHERE type_category='gamestate' AND value=5` 的 `t_cle`(=8946650558 是 943.5)。
- **插眼**:`combat_log WHERE type_category='item' AND inflictor LIKE 'item_ward%'`;排除 target 非空(=给队友);`inflictor=item_ward_dispenser` 需实体判型。
- **实体位置/型/队**:`game_events.event_type='ward_placed'`(x/y/team/ward_type/t_cle)。
- **销毁**:`combat_log WHERE type_category='death' AND target LIKE '%ward%'`;`a_team!=t_team`=被反,`==`=过期。
- **英雄队**:`player_identity.hero_name→team_id`(combat_log item 无 a_team)。
- **对齐** = (队,型,时间最近邻 + 位置),绝不以 entity_index 为主键。

## 7. 分析层现状
- `analysis/q5_ward.py`:仍读**旧架构**(db 散装 game_events 的 ward_placed/ward_destroyed/ward_use)。**需重写**为从 db_full 读(路径见 §6)。
- `analysis/build_q5_html.py`:读 `q5_ward.py` 产物 `q5_sen_win_172.json` / `q5_sentry_detail_172.csv` 构建 Q5B viewer(`q5b_ward_viewer.html`,7.4MB 已在 output_review)。
- `analysis/output_review/q5b_ward_viewer.html`:已用新数据重建过一次(7.4MB),但底层分析还是旧逻辑重跑的;**待用新架构重跑**。

## 8. 待办(本次任务)
1. **重写 `analysis/q5_ward.py`**:从 `dems/db_full/` 读守卫(combat_log + 实体),按 (队,型,cle+位置) 对齐,输出 `q5_sentry_detail_172.csv` / `q5_sen_win_172.json`。
2. 重跑 Q5(970 场,读 db_full)。
3. 重建 `q5b_ward_viewer.html`(build_q5_html.py)。
4. **推公网**:拷 `q5b_ward_viewer.html` → `publish_repo` → `git add/commit/push origin main` → `https://bigfatblackwhale.github.io/DSH-Dota2/q5b_ward_viewer.html`。

## 9. 关键参考
- `STRATEGY/COMBAT_LOG_REWRITE.md`(新架构交接)。
- `STRATEGY/DEM_FORMAT.md` 附录 D(D6 combat_log 表设计)。
- `analysis/Q5_RULES_CALIBRATION_LOG.md`(规则/定义/校准,§7/§12/§13/§14)。
- `STRATEGY/DEM_WARD_REWRITE_NOTES.md`(守卫根本解)。
