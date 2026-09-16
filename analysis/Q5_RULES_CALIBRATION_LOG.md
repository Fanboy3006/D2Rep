# Q5/Q5B 眼位研究 —— 规则·定义·校准备忘(2026 当日记录)

> 本文件记录当天确认的**规则/定义类**结论与**断电重连恢复方案**。供 ②数据层 与 ③逻辑/总控 对齐。
> 与 `DATA_DICT.md`(口径字典)、`Q5_SYNC_TO_LOGIC_CONTROL.md`(同步报告)配套。**凡出现新的规则/定义/校准结论,先落这里。**

---

## 一、【已定】时间基准与时钟体系（规则）

### 1. 权威时钟 = combat-log 时间
- **`cle.timestamp()`(combat-log 时间)是权威游戏时钟**:精确到小数秒(如日志 `[29:35.266]`),随 pause 冻结,从 0:00 号角起。
- **tick 时钟** `game_time_sec = tick/TICK_RATE` 与 combat-log **同源、差<1s**(实测),但**受 pause 影响**(pause 期间 raw 照走、游戏时钟冻结)。

### 2. 游戏时钟换算
```
游戏时钟 = raw − 0:00raw − (0:00之后、该事件之前的累计 pause)
0:00raw  = min(building_spawn) + 90 + 号角前 pause
```
- `building_spawn`(塔/兵营/瞭望塔)在预赛阶段生成;号角=+90s;若号角前有 pause,0:00raw 再 + 该 pause。
- **pause 边界精度 ≈ 2s**;用固定观测可反推精确 0:00raw:
  - 8817145578 → 1062(用户观测 15:59 放)
  - 8885871759 → 842(用户观测 20:57 放)
  - 8842596730 → 982(用户观测 29:35.266 反)
- **后续方向(已定,待重解析)**:改用 combat-log 时间作为一切时间源,去掉 tick+pause 校正,一劳永逸消除 2s 边界误差。

### 3. pause 判据（关键修正）
- hero 位置**逐秒完全冻结** = 暂停;**合并相隔≤2s 的块**。
- **游戏结束后僵直 ≠ 暂停**:需用**截止点**排除。截止点 = `MAX(building_destroyed, gold)`(铁定 gameplay 结束;不用 ward_destroyed/ward_placed——会被结算污染)。
- 只把"截止点前"的冻结当 pause。
- ⚠️ 曾误检:8817145578 的"923s"、8885871759 的"919s"都是**游戏结束后僵直**(非真暂停)。真实暂停只有开局那几个。

## 二、【已定】销毁 / 归属 / 指标规则

### 4. 销毁判定（混合）
- **自然到期(expired)** = 放置 + 标准寿命(**真眼 420s / 假眼 360s**)。
- **被反(dewarded)** = 该眼提前消失(entity last_t < 放置+寿命)且存在同队同型 combat-log `dewarded`;**被反时刻用 combat-log 死亡 `t_tick`**(权威),不用 entity last_t(后者晚几秒)。

### 5. 反眼归属（按位置,不依赖 actor）
- 归属 = "被反假眼位置在真眼**真视半径(1050)**内 + 存活窗[place,destroy]";**天辉真眼反夜魇假眼(敌方)**。
- `actor_id`(反方英雄)**仅用于展示**,不参与归属判据。实测 actor 可能被解析到非实际反方英雄(如 884 那支假眼日志被 Clockwerk 反,数据 actor 却是 rattletrap)。
- **注意**:归属时按"假眼位置+半径+存活窗",**不要用时间窗匹配去硬配 deward 事件**(曾因此把错误 deward 绑给某真眼)。

### 6. 反眼率(核心指标)
- = 每支真眼**平均反掉的敌方假眼数**(`avg_jy = Σsuccess/n`)。
- **只算反假眼**,不含反真眼、不含真眼被反、不混真假眼(曾误把 `(success+dew_sen)>0` 当反眼率,已改)。
- 其它指标(非核心):平均反真眼(avg_zy)/平均总反眼(avg_all);总反眼数(jy_total/zy_total/all_total)。

## 三、【注意项】双插（不特殊处理,记录）
- combat-log 里真假眼**重叠/一起用**时显示 `uses Observer Ward and Sentry Ward`,具体哪支需判断,两支都要纳入考虑。
- **当前不做特殊逻辑**,出现时按"两支都评估"处理。**不占用本次解析。**

## 【关键·combat-log API 研究(2026)】放哪些字段可读, 供之后分解分类每个 combat log

### CombatLogEntry 的结构(source2-demo `downloads/source2-demo-src/src/event/combat_log.rs`)
- `CombatLogEntry<'a>` 有 `names: &StringTable`(名表)+ `log: CMsgDotaCombatLogEntry`(protobuf)。
- **名称 getter**(`define_name_getter`, 经名表解析成字符串):`target_name` / `target_source_name` / `attacker_name` /
  `damage_source_name` / `inflictor_name` / **`value_name`(→ log.value, item 名)**。
- **标量 getter**(`define_combat_log_getters`, 直接读字段):大量字段, 包括:
  - `value: u32`(数值; 眼=74 sentry / 51 observer / 11 dispenser), `health`, `timestamp: f32`(**游戏时钟**, cle), `stun_duration`,
  - `location_x` / `location_y`(世界坐标), `gold_reason`, `xp_reason`, `last_hits`,
  - `attacker_team` / `target_team`, `assist_players`(辅助玩家), `ability_level`,
  - **`obs_wards_placed: u32`**(放置 Observer Ward/假眼 计数 —— 放置假眼的 combat-log 记录, 带 cle.timestamp!),
  - `modifier_*` 系列(`modifier_duration`, `modifier_elapsed_duration`, `modifier_ability`, `modifier_purge_*`, `modifier_hidden`…),
  - 各种布尔:`is_attacker_hero` / `is_target_hero` / `is_attacker_illusion` / `is_ultimate_ability` / `is_ability_toggle_on/off` /
    `uses_charges` / `will_reincarnate` / `at_night_time` / `is_target_building` / `attacker_has_scepter` /
    `aura_modifier` / `invisibility_modifier` / `spell_evaded` / `silence_modifier` …
  - `event_location`, `building_type`, `damage_type`, `damage_category`, `neutral_camp_type/team`, `rune_type`, `xpm/gpm`.
- **重要**:`obs_wards_placed` 是**放置假眼**的记录(cle.timestamp 有); proto 无 `sen_wards_placed`(真眼放置)字段,
  真眼放置需另行识别(可能 via `value_name=item_ward_sentry` 的 Damage/采购, 或其它)。

### .dem 战斗日志文本 → combat-log 类型 对应(供分解)
- `uses Observer Ward` 放置假眼 → `obs_wards_placed > 0`(att=玩家英雄, 避开 `att=dota_unknown`/召唤物如 nevermore Necromastery)。
- `Observer Ward is killed by X` 反假眼 → `DotaCombatlogDeath` att=英雄 tgt=npc_dota_observer_wards。
- `Sentry Ward is killed by X` 反真眼 → `DotaCombatlogDeath` att=英雄 tgt=npc_dota_sentry_wards。
- `uses Sentry Ward` 放置真眼 → **待定**(proto 无字段, 需用 `value_name=item_ward_sentry` 或其它识别)。
- 眼自到期 → `DotaCombatlogDeath` att=眼单位自己 tgt=自己 val=1。

### 待办(combat-log 分解分类)
- 逐个 combat-log 类型写出含义/字段, 完成"眼相关"事件的 cle 时间映射。

## 【收口·最终结论(2026定稿)】
- **每支假眼(Observer)的存活时间、反眼时间已能用 cle 时钟精确算出**:
  `game clock = cle.timestamp(反眼/到期 Death 事件) − game_start_cl(GAME_IN_PROGRESS的cle)`,
  验证 884: 反假眼 cle 2772.167 − 996.90 = **29:35.27**(与游戏日志 29:35.266 一致)。
- **game_start_cl 已写入 parser**(`game_state` 事件 state=5),Python 层读它当 0:00 锚点。
- **放置/反眼/到期统一用 cle(combat-log)时间的方向已确立**;反眼/到期(wDeCombat Death)已有 cle。
- **`obs_wards_placed`(放置假眼)**:暂未纳入(短期不重要)。**留待以后研究 combat 全部内容时弄明白**[见上文 combat-log API 研究]。
- **每支 OB 的存活时间/反眼时间可用,这是可靠底座**;后续 Q5 时间统计以 cle − game_start_cl 为准。

### parser 改动(cle 时钟版, 2026-06)
- `WardExtractor` 捕获 `game_start_cl`(DotaCombatlogGameState val=5 的 cle.timestamp), 写 `game_state` 事件(state=5)。
- `ward_destroyed` **以 combat-log Death 为权威**: reason=dewarded/expired(from self_expired)、actor、cle.timestamp(t_cle)；
  匹配最邻近的同类型 entity(容差 ≤15s, entity 在 combat death 后残留几秒), 取坐标/index。
- 反眼/到期时间 = `t_cle − game_start_cl`(精确游戏时钟)。
- 验证 884 ward:2117: reason=dewarded, t_cle=2772.167, gameclock=**29:35.10**(report 29:35.266)✓;
  reason 分布 dewarded=44, expired=67 合理。
- `ward_use`(obs_wards_placed)已留字段但条件未命中(暂不纳入, 以后 combat 全量研究再弄)。

### 全量重解析(进行中)
- 用 cle 时钟 parser 全量重解析 970 场(parse_all_force.py, 6 workers, ~2h)。完成后再改 q5_ward.py 用 cle 时间口径, 重跑 Q5 + 重建 viewer。
- 断点续跑: parse_all_force.py 全量重跑(无 skip); 若中断重跑会重新解析全部。

## 【残留阻塞·放置无 cle(影响存活统计)】
- **销毁/到期**时间已用 cle(t_cle)精确; **但放置(ward_placed)仍是 tick 时间(无 cle)**。
- **存活 = 销毁(cle) − 放置(tick) 两套时钟不同, 不能直接算。**
- 要正确算存活/生存, 必须**让放置也有 cle**(攻克"uses ward"/obs_wards_placed 的放置 cle)。
- **因此 q5_ward.py 存活/生存统计暂不改**: 等放置 cle 攻克后再统一 cle 口径。
  ⚠️ 销毁/到期时间单点可用 cle; 但"存活时长/生存统计"需放置 cle 就位后才是对的。

## 【关键·时间统一(cle 时钟)方案 2026】
- 目标: **放置/反眼/到期 全部用 combat-log cle.timestamp**, 统一减 `game_start_cl`(GAME_IN_PROGRESS 的 cle.timestamp)得游戏时钟。
- 已确认: 反眼/到期(combat Death)有 cle; **放置假眼有 `obs_wards_placed` 的 cle**; 放置真眼待补。
- game_start_cl 已在 parser 写入 `game_state` 事件(state=5), Python 层读它当 0:00。
- **`cle.timestamp()`(combat-log 时间)不是游戏时钟**(min=830.83 起,不是 0),不能直接用。
- **但 `DotaCombatlogGameState` 有 GAME_IN_PROGRESS(val=5)**,其 cle.timestamp = **游戏时钟 0:00 的锚点**。
  - 8842596730 实测:`val=5`(GAME_IN_PROGRESS)的 cle.timestamp = **996.90**。
- **正确游戏时钟换算**:
  ```
  游戏时钟 = clt.timestamp − (GAME_IN_PROGRESS 的 clt.timestamp)
  # 即 0:00 = GAME_IN_PROGRESS(号角)的 cle.timestamp
  ```
  用此锚点,即可直接用 combat-log 时间获得游戏时钟(无需 tick+pause 校正)。
- **待验证**:GAME_IN_PROGRESS 的 cle.timestamp 是否跨场/跨 league 稳定作为 0:00;以及 event 的 game_time_sec 改用 cle.timestamp。
- **下一步**:改 parser 记录 GAME_IN_PROGRESS 时的 cle.timestamp 为 `game_start_cl`;所有事件时间用 cle.timestamp − game_start_cl。
  ⚠️ 但 ward**放置**无 combat-log 记录(只有被反/到期 Death),放置时间仍需另来源(entity Created 或 GameState 对齐)。

## 【重要·被反判定 bug 确认(2026,必须重做)】
- **实例 8842596730, ward:2117(假眼, pos -4591,3319)**:游戏日志显示 **29:35.266 被 Clockwerk 反掉(dewarded)**,
  **但我的数据判它 `expired`(自然到期),且无 dewarded 记录,反眼英雄错成 rattletrap(另一事件错配)**。
- **根因**:我的"被反判定"依赖 **entity last_t(比真实偏晚 ~9s)+ 时间窗匹配**,误把该"被反"的眼判成"到期",
  且反眼归属把 deward 事件错配给别的眼/英雄。
- **结论**:**"被反判定 + 反眼归属"逻辑有系统性错误,需从源码重做**,不能靠数据打补丁。
  - 反眼归属应严格按"**被反假眼位置(真实坐标)在真眼真视半径(1050)内 + 存活窗 + 敌方阵营**",不依赖 actor/时间窗匹配;
  - "被反 vs 到期"判定不能用 entity last_t(偏晚),需重新找可靠判据。
- ⚠️ **combat-log 时间与游戏时钟**:之前说"不是游戏时钟"是**误判**——真正问题是归属/判定 bug(把 deward 误判成到期、错配英雄)。
  combat-log 时间(cle.timestamp)本身应该是游戏时钟,但需配合正确的归属/判定逻辑。

## 【重要·探测结论】combat-log 时间不能直接当游戏时钟（2026-06 probe）- **ward 放置(插眼)在 combat-log 里没有对应记录**:combat-log 只有
  - `DotaCombatlogDamage/Death att=英雄 tgt=npc_dota_observer/sentry_wards`(被反/反眼);
  - `DotaCombatlogDeath att=npc_dota_observer/sentry_wards tgt=自己`(自然到期);
  - (`npc_dota_shadow_shaman_ward_1` 等是**英雄技能召唤物**,不是假眼/真眼)。
- **因此 "改用 combat-log 时间作一切时间源" 对放置时间不可行**——放置只能用 entity Created(tick 时钟)。
- **combat-log 时间也不是干净的"游戏时钟"**:`DotaCombatlogGameState`(7条)时间戳从 ~830 起,
  `val=5`(GAME_IN_PROGRESS/号角后)在 **996.90**,而我 pause 校正的 0:00 是 980,差 ~17s。
- **结论**:"切换 combat-log 时间源"并**不能**一劳永逸消除时间基准问题——它只是另一套需要对齐的时钟,
  且放置无 combat-log 记录。时间基准问题需另外的方案(见待办)。
- ⚠️ 之前 user 判断"改用 combat-log 时间(重解析)一劳永逸"——经此探测,该前提**不成立**,方案应调整。
  已记录的"待办2:改用 combat-log 时间重解析"应改为"另寻时间对齐方案"。

## 四、断电重连恢复方案（重要）
- **解析断点续跑**:每场比赛独立写 `dems/db/<league>/<match>.db`;已解析成功(存在且 networth>=10)的可跳过。
- **恢复命令**(在项目根,已设好工具链 PATH + sqlite3.dll):
  ```
  $tc='F:\D2Rep_project\dota_replay_analyzer\rust_toolchain_x86_64-pc-windows-gnu\bin'; $env:PATH="$tc;$env:PATH"
  $env:DOTA_PARSE_SQLITE_DLL='F:\D2Rep_project\dota_replay_analyzer\dota_parse\target\release\sqlite3.dll'
  python parse_all_ward.py 6
  ```
  `parse_all_ward.py`(当前版)每次跑会**重新解析全部**(未做 meta skip);若需断点跳过,可加"已有 ward 实体则 skip"。
- **编译最新 parser**:`cd dota_parse; cargo build --release --bin dota_parse --offline`(需工具链 PATH)。
- **资源**:CPU AMD Ryzen 7 7800X3D(16 逻辑核);磁盘 F:余量 ~177GB。6 workers 全量(970 场,142GB)≈ **2 小时**。
- **耗时参考**:单场 258MB≈45~47s;970 场全量实际跑完 7005s=116.75 分钟。

## 五、待办（下次）
1. **时间基准问题需另谋方案** —— 探测证明 combat-log 时间不能直接当游戏时钟,且 ward 放置无 combat-log 记录。
   方向候选:用 `DotaCombatlogGameState`(游戏状态切换)定位更精确的 0:00/号角;或精修 pause 边界(2s 误差);
   或逐场用固定观测校准(如 8817145578→1062, 8885871759→842, 8842596730→982)。
2. **暂缓"改用 combat-log 时间重解析"** —— 该方案对放置时间不可行,不解决问题。
3. 用观测复核当前 tick+pause 校正:8817145578(15:59放)、8885871759(20:57放)、8842596730(29:35反)。
4. **双插**若成规模再决定是否特殊处理。
5. probe(`probe_wardcl`)已建,下次验证 combat-log 时可复用。

---

## 六、Q5B viewer 死机调试（Chrome DevTools 9222 监视 console）— 已解决

### 前提：9222 端口 = Chrome DevTools 远程调试
- 用户可通过 `--remote-debugging-port=9222`(或已有端口)暴露;暴露后 `http://127.0.0.1:9222/json` 返回所有 page 的 `webSocketDebuggerUrl`。
- ⚠️ **页面实际加载的是 `file:///.../analysis/output_review/q5b_ward_viewer.html`**,即使在 `http://127.0.0.1:8000/` 的有标签。监视 console 必须**连页面的 page-type target**,不是端口 8000(那可能连不上)。
- **方法**:Node ≥22 自带**全局 `WebSocket`**(无需 `ws` 模块)。连到该 page 的 `webSocketDebuggerUrl` 后,CDP 直接**在页面进程内 eval JS** + 订阅:
  - `Runtime.enable` → 收 `Runtime.exceptionThrown` / `Runtime.consoleAPICalled`
  - `Log.enable` → 收 `Log.entryAdded`
  - `Runtime.evaluate`(`returnByValue:true`)探活/注入诊断
- **关键**:"死机"不是 CPU 卡死(进程还响应 CDP),而是**每次红绘都抛异常**——用 CDP 能连上是因为异常在 JS 层、不阻塞浏览器主线程的 CDP 通道。

### 根因（两个真实 bug,源码在 `analysis/build_q5_html.py`）

**Bug A（死机主因）— `capValue.toFixed is not a function`(每次 draw 都抛)**
- `draw()` 里 clamp:`capValue=SL.max` / `capValue=SL.min`。
- `<input type=range>` 的 `.max`/`.min` 属性值是**字符串**(`"0.7"`)。一旦 clamp 触发,`capValue` 变成字符串。
- 字符串**永久卡死**:`capValue==null` 不再成立(下次不会重置),且后续 clamp 又赋回字符串 → `capValue.toFixed(2)` 每次 throw → **canvas 永远停在初画,地图不刷新**。
- 复现链:滑块拖到某值触发 clamp → capValue 变 string → 之后任何重绘都 throw → 视觉"死机"。
- **修复**:clamp 改用**数字**运算再回写:
  ```js
  let cv=+capValue; if(isNaN(cv)||cv<=0) cv=b.cap;
  if(cv>+SL.max) cv=+SL.max; if(cv<+SL.min) cv=+SL.min;
  capValue=cv;
  ```

**Bug B — `px is not defined`(悬停时抛,非滑块)**
- `cv.onmousemove` 里 `const px/py` 声明在 `if(drag){...}` 块**内部**,块外面(悬停高亮)直接用 `px`,`py` → 每次鼠标悬停(未拖拽时)抛 `ReferenceError`。
- **修复**:把 `const rect=cv.getBoundingClientRect(); const px=..., py=...` **提到 `if(drag)` 块之前**,块内只复用。

### 验证（CDP 实测,重新构建后）
- 重建:`python analysis/build_q5_html.py` → 写 `analysis/output_review/q5b_ward_viewer.html`(7.3MB)。
- 通过 CDP reload 该 tab 后复测:
  - 滑块 0→40 全扫:**每点渲染 ≤1.8ms、0 报错、`capValue` 全程为 number**。
  - 滑块→10:`capValue=0.7`(number)、`ms≈4.5`、1311 格显示,0 console 错误。
  - hover:`hoverCaught:null`,不再有 `px is not defined`。
- **待办提醒**:此 bug 曾造成"滑块值异常/卡死"的误判 → 已确认为 viewer 前端 JS 问题,与解析/时间基准无关。

### 复用：CDP 监视 console 的临时脚本（可重建）
分析时用过的 `cdp_helper.mjs`(eval+收 console/exception)、`reload_probe.mjs`(reload 后读 `capValue` 类型)、`test_slider.mjs`(复现滑块→10 与 hover)。均为诊断脚本,诊断完已删;下次可按需用 Node 全局 `WebSocket` 连 9222 重建。

### ⚠️ CDP 前两 bug 均与解析无关
这两个都是 viewer 前端 JS 问题,不是数据/解析问题。**勿再误归类到时间基准/解析。**

---

## 七、【已定·核心】插眼/反眼 combat-log 事件的真实类型（决定重写方案）

> 用户已确认(2026,实测 8946650558):这是 parser 完全改对的钥匙。**之前的"插眼无 combat-log 记录"是误判。**

### 1. 插眼(放置守卫) = `DotaCombatlogItem`,target 为空
- **判定特征**(同时满足):
  - cle `r#type()` == **`DotaCombatlogItem`**
  - `target_name` 为**空**(非守卫自身)
  - `inflictor_name` == **`item_ward_sentry`**(真眼)/ **`item_ward_observer`**(假眼)
- **时间**:`cle.timestamp()` = combat-log 游戏时钟(权威、精确)。
- **位置**:`location_x/y` = **`(None,None)`**(⚠️ use 事件**不带坐标**)。
- **实例(8946650558)**:`[cle=2007.300] DotaCombatlogItem atk=mirana tgt=(空) infl=item_ward_sentry` → 换算 game_clock = 2007.3 − 943.5 = **17:43.800** = combat-log 显示 `[17:43.800] Mirana uses Sentry Ward.` ✅

### 2. 反眼/眼被摧毁 = `DotaCombatlogDamage` / `DotaCombatlogDeath`,target=守卫自身
- **判定特征**(同时满足):
  - cle `r#type()` == **`DotaCombatlogDamage`** 或 **`DotaCombatlogDeath`**(两者对偶出现)
  - `target_name` == **`npc_dota_observer_wards`**(假眼)/ **`npc_dota_sentry_wards`**(真眼)
  - `value_name` == **`ability_lamp_use`**(⚠️ 不要用 value_name 判守卫类型,用 target_name)
- **阵营判别**:`attacker_team != target_team`(且 attacker 是敌方英雄/兵)→ **被反**;`attacker==target`(都是 `npc_dota_*_wards`)→ **自然过期**。
- **时间**:`cle.timestamp()` 游戏时钟。
- **位置**:⚠️ **同样无坐标**(`location_x/y=None`)。位置只从实体拿。
- `value`:对 Death/Damage 是**伤害数**,对 Purchase 才是 item index —— **别混用**。
- **实例(8946650558)**:`[cle=2012.000] DotaCombatlogDamage/Death atk=mirana tgt=npc_dota_observer_wards name=ability_lamp_use` → game_clock = 2012.0 − 943.5 = **17:48.500**;坐标(得自实体)= **(-3350.6, 7470.1)** = 天辉假眼被 Mirana(夜魇)反掉。

### 3. 关键区别表（判定依据）

| | **插眼 uses** | **反眼 destroys** |
|---|---|---|
| cle type | `DotaCombatlogItem` | `DotaCombatlogDamage` / `Death` |
| `target_name` | **空** | `npc_dota_observer_wards` / `sentry_wards` |
| `inflictor_name` | `item_ward_sentry` / `item_ward_observer` | `ability_lamp_use` |
| `value_name` | (空) | `ability_lamp_use` |
| 时间源 | `cle.timestamp()` 游戏时钟 | `cle.timestamp()` 游戏时钟 |
| 位置 | **(None,None) 无坐标** | 有坐标(守卫所在) |

### 4. 8946650558 核对样本（user 确认）
- `[17:43.800] Mirana uses Sentry Ward.` = **插眼**(tgt=空, infl=item_ward_sentry) → game_clock 17:43.8(correct)。
- 同一 window 的 `tgt=observer_wards, ability_lamp_use` = **反眼**(game_clock 17:48.5,位置 -3350.6,7470.1)。
- 两者都是 **Mirana(夜魇, team_id=3)**;插眼后 ~4.7s 反掉天辉假眼。

### 5. ⚠️ 插眼无位置 → 坐标仍须来自实体 Created(tick)
- `DotaCombatlogItem`(插眼)cle 是**精确游戏时钟**,但**无坐标、无队伍、无 entity/index**(实测 attacker/inflictor/target_is_self 有值,location_x/y、attacker_team、target_team、value 均无)。
- **`CMsgDotaCombatLogEntry` 全量字段表里没有任何 entity/handle/index 字段** → combat-log 无法连到具体实体。
- 坐标/队伍/type 只能从 **entity snapshots / ward_placed(实体 Created,tick 钟)** 拿。真眼=`CDOTA_NPC_Observer_Ward_TrueSight`,假眼=`CDOTA_NPC_Observer_Ward`,主键=`entity.index()`。
- **重写方向(唯一可行,顺序配对)**:
  - use(cle) 与 Created(tick) **各自单调顺序一致** → 按 **同队 + 同类型 + 时间顺序最近邻** 贪心配对,把 use-cle 绑定到实体拿坐标。
  - ⚠️ **不能假设 cle↔tick 的 offset 恒定**(随暂停变,实测 14.4~65s)。对的鲁棒性来自**顺序一致**,不是常数 offset。

### 5b. 三个必须处理的"破坏对账"坑（来自 .dem 权威结论）
1. **孤儿 use**:8817145578 有 **36 条** `item_ward_*` 在 ±2s 内无实体出生("用了眼但没落地"/错位)。
2. **重复 Created**:`on_entity(Created)` 重复触发 → `ward_placed` 真眼 94 行/89 独立、假眼 54/53。**先按 `entity_index` 去重再做 order 对齐。**
3. **dispenser 分不清真/假眼**:`inflictor=item_ward_dispenser`(66 条)覆盖真眼 39 + 假眼 27;只有真眼 50 用 `item_ward_sentry`、假眼 25 用 `item_ward_observer`。**判型必须用实体。**

### 5c. ward_use 现状是错的（死代码,须改）
- 当前 `WardUse` 抓 **`cle.obs_wards_placed()`**(恒 0,且那是 Playerstats 的累计假眼数,不是事件)→ **错的**。
- 应改抓 **`DotaCombatlogItem` + `inflictor=item_ward_sentry`/`item_ward_observer`**(attacker=英雄)的条目 → `ward_use` 应>0 且≈放置实体数。

## 八、【已实施】parser 重写：ward_use 改抓 DotaCombatlogItem（2026）

### 改动（dota_parse/src/parse.rs）
1. **`on_combat_log`**:删掉 `obs_wards_placed` 分支,改为 `DotaCombatlogItem` + `inflictor∈{item_ward_sentry, item_ward_observer}` + `is_attacker_hero` → push `WardUse{t: cle.timestamp, actor, ward_type}`。
2. **`WardUse` 结构体**:`{t, actor, x, y}` → `{t, actor, ward_type}`(去掉 x/y,新增 ward_type;无坐标)。
3. **`build_ward_event_rows`**:ward_use 写入不再硬编码 observer,用 `u.ward_type`;x/y 留 None;`target_id=None`。

### 验证（单场 8817145578,重跑重写）
- `ward_use`:恒 0 → **90 条**(sentry **60** / observer **30**)。
- 每条含 `game_time_sec`(=cle 游戏时钟)、`actor_id`(插眼英雄)、`ward_type`、`t_cle`;`x/y=NULL`(无坐标,符合 .dem)。
- **顺序配对可行性**:use-cle 与实体 placed-tick 的 offset **不恒定**(min 0 / max 66 / avg 21.7s);**4 个 use >60s 无实体**(孤儿)。→ **必须顺序配对,不能靠常数 offset**(已定)。

### 全量重跑
- 新增 `parse_all_full.py`(强制覆盖,不 skip,区别于 `parse_all_force.py` 的 has_game_state skip)。
- 已后台启动 `python parse_all_full.py 6`(970 场,约 70min)。

### 6. 双时钟污染(已定,仍是核心坑)
- `ward_placed.game_time_sec` = **tick 钟**(实体 Created);`ward_destroyed.game_time_sec` = **cle 钟**。两钟差 **~14.4s(8946650558)/ ~65s(8817145578)**,**随场不同**。
- 直接相减必错(884 验证:派 17:28.5 vs 真实 17:43.8)。**配对须用 t_tick**(destroy 的实体消失 tick)或改用插眼 cle。

### 7. 统一时钟口径（落库动作）
- **快照/实体** 用**回放时钟** `tick/30`;combat 的 placed/destroyed 用**游戏时钟** `cle.timestamp()`。
- 统一到"0:00 显示口径" = **`cle.timestamp() − game_start_cl`**(game_start_cl = `DotaCombatlogGameState` val=5 的 cle,即号角 0:00 锚点)。
- 或用 `t_tick` 桥接(见 `STRATEGY/DEM_FORMAT.md` §2.5、A1-A5)。

## 九、【已实施·分析层】q5_ward.py 顺序配对（use-cle ↔ 实体 tick）

### 用户决策
- **parser 只多存 cle,配对留给分析层** → 减少未来重 parse 的概率。
- **全部重跑**(随机验证才能发现系统性问题)。

### 实施（analysis/q5_ward.py,parse_match）
1. **读 `ward_use`**:`game_time_sec`(=cle) + `actor_id`(英雄名) + `ward_type`,LEFT JOIN `player_identity.hero_name` 得 `team_id`。
2. **use 驱动顺序配对**:同 `(team, ward_type)` 组内,每个 use-cle 配给"**时间最接近、未配对**"的实体。
   - ⚠️ cle 与 tick 是两个时钟域,offset 随暂停漂移(14.4~65s)→ **用时间最近邻,不用常数**。
   - offset ≤ 65s 才配对;否则记为孤儿 use。
3. **place 修正**:实体 `place` 改用配对的 **use-cle**(替换原 tick place_raw)。被反判定也统一用 cle 时窗。
4. **孤儿 use 单独输出** `q5_orphan_uses.csv`(经初步验证,8946650558 仅 2 个真正的孤儿 use,供总控到游戏里核实 toggle stacked ward 等)。

### 验证（8946650558 单场,esp. 案例真眼）
- 关键真眼 `ward:2663`(位置 -3931.8,7532.8):
  - 旧(错误):place = **17:28.50**(tick 被当 cle 用)
  - 新(正确):place = **17:43.80** = use-cle 2007.3 − game_start 943.5 ✅(正是 [17:43.800] Mirana uses Sentry Ward)
- orphan_uses:42(初版贪心) → **2**(use 驱动后真正失配)。

### 坑:曾出现的两个实现错误(已修)
- ① 实体遍历用哈希表无序 → use 顺序指针错配;改为按 `(team, wt, place_raw)` 排序 + use 驱动。
- ② ent_keys 丢了 x/y → 所有眼坐标相同(-4377.5,-1047.9);改为 ent_keys 存 x/y。

## 十、【下一阶段已定】全部重新解析（.dem 全面扩展）→ 生成**另一个 DB 库，不覆盖 Q5 版**

### 用户决策（2026）
- **Q5 先收尾** → 已闭环(真眼插下时间修正 + Q5B viewer 重建)。
- **之后全部重新解析**:按 `STRATEGY/DEM_FORMAT.md` 附录 D 的**解析器全面扩展**(combat-log 全部 11 种 + 信息实体 GamerulesProxy/DataSpectator/SpectatorGraphManager/PlayerController/PlayerPawn/Team 等)。
- **关键约束:新 DB 库与 Q5 版分离、不覆盖**。即扩展解析结果写入**独立目录**,`dems/db/`(Q5 用)保持不动,两者并存。

### 扩展解析新增项（来自 .dem 盘点）
- **不做**:SvcVoiceData/UmParticleManager/GeSos/NetSpawnGroup/legacy dota_* / GamerulesProxy 地图结构句柄。
- **新增解析**:
  - combat-log **全部 11 种**(Damage 6.4万/场、ModifierStackEvent 3.1万、Heal、Xp、Playerstats、Buyback、Killstreak、Multikill、CriticalDamage、FirstBlood、TeamBuildingKill),ModifierStackEvent 全记不丢。
  - 信息实体:GamerulesProxy(时钟/暂停/塔兵营摧毁/野怪盒/选人禁用/游戏模式/胜者)、DataSpectator(眼位/分路/胜率/roshan/rune)、SpectatorGraphManagerProxy(逐分钟胜率/净worth/金/XP)、PlayerController/PlayerPawn(死亡时刻)、GameManagerProxy、Team。
- **耗时基准**:加新后单场 ~33-40s(行数 114k→258k);全量 970 场 3 workers≈1.7h,8-12 workers≈1-1.5h。

### 待办（下一阶段启动时）
1. 新建**独立 DB 目录**(如 `dems/db_full/`),扩展解析写入此处,不碰 `dems/db/`(Q5 版)。
2. 按附录 D 重写/扩展 parser 的解析表;`parse_public.py --workers` 提至 8-12。
3. 扩展解析完成后再评估与 Q5 的关系(Q5 用 cle 时钟,扩展集含全部钟源,需统一口径)。

## 十一、【关键判断·已暂停精修】守卫(眼位)配对 = 底层精修,须先搞清机制再重解析

### 用户判断（2026）
> "感觉不太对,我感觉我们需要全部重新解析之后再研究,你现在这个结果我感觉从底层机制上还是一种精修。"

**结论:当前 segmap / 顺序配对方案是"精修",不是根本解。暂停继续,先搞清 .dem/combat-log 底层机制,再重新解析。**

### 已暴露的底层机制问题（决定必须重解析）
1. **`entity_index` 会被 Dota 回收复用**(已证实 8825993964 eidx=2876 两支眼共用:眼A tick1757-2183、眼B tick3373-4623,跨队/跨位置)。→ **按 entity_index 聚合必然错配**。
2. **place 用 segmap 插值**(从实体 tick 线性反推 cle)带 ~1 秒误差,且对**号角前**(0:00 前)外推不准 → **精修,不是根本解**。
3. **插眼 cle 本是精确游戏时钟**(`DotaCombatlogItem` = [17:43.800] 已验证),但当前"用实体 tick 反推"绕开了它。

### 根本解法方向（待底层机制结论后定）
- **守卫 place 应直接用 combat-log 插眼 cle**(`DotaCombatlogItem` 精确游戏时钟),而不是实体 tick + segmap。
- entity Created(tick) 只用于**取坐标/team/type**,不用于反推时间。
- 需先搞清:entity handle 复用机制 / 插眼 cle 如何与实体锚定(位置/顺序)。

### 待办（当前,等底层 agent 结论）
- 等 .dem 研究 agent 给出:entity index 复用真实语义 + 插眼 cle 与实体锚定的可靠方式。
- 拿到结论后再设计"守卫眼位"的 parser 重写 + 全面重解析(进独立新库)。
- ⚠️ 当前 `dems/db/` 的 Q5 版为"精修版",**不建议作为最终结论**,待重解析后替换。

### 结论给底层
- 守卫眼位的"插下时刻"必须从 combat-log 插眼 cle(DotaCombatlogItem)取,不靠 tick 反推。
- entity_index 可复用 → 不能用它作眼的唯一键。

## 十二、【已定·底层重解析方案】守卫眼位 Q5 从底层就正确（废 segmap/顺序配对）

### 用户定调（2026）
> "别再精修 segmap/顺序配对了。重新解析时:用 GamerulesProxy 里官方时钟字段做精确 tick↔cle;把 插眼cle/实体位置与类型/销毁cle 作为三个独立来源分别落库;分析层只用'队+型+时间+位置'做健壮关联,绝不以 entity_index 为主键。这样 Q5 从底层就对,1s 误差和号角前负数都消失。"

### 方案要点（底层重写,非精修）
1. **精确 tick↔cle**:用 **GamerulesProxy 官方时钟字段**(而非 ward_destroyed 交点插值的 segmap)。需先探明 GamerulesProxy 的时钟字段(m_flGameStartTime / m_nTotalPausedTicks / m_nPauseStartTick / m_nGameState 等)如何精确换算 tick→cle。
2. **三独立来源(分别落库,各自真实)**:
   - **插眼 cle**(combat-log `DotaCombatlogItem`,精确游戏时钟;已验 = [17:43.800])。
   - **实体位置与类型**(entity Created/CDOTA_NPC_Observer_Ward_TrueSight/CDOTA_NPC_Observer_Ward,取坐标/type)。
   - **销毁 cle**(combat-log `ward_destroyed` Death/Damage 的 cle,含 reason=dewarded/expired)。
3. **分析层健壮关联**:只用 **队 + 型 + 时间 + 位置** 做关联(插眼cle 与 实体 用队+型+时间/位置锚定),**绝不用 entity_index 作主键**。

### 预期效果
- **1s 误差消失**:place 直接用插眼 cle(精确,不再插值)。
- **号角前负数消失**:精确 tick↔cle 后,号角前插眼不再被外推成负。
- **entity_index 复用不再错配**:分析层不依赖它。

### 待办（底层 agent / 下一步）
1. 探明 **GamerulesProxy 官方时钟字段** 的精确 tick↔cle 换算方法。
2. 重写 parser:独立落 插眼cle / 实体位置类型 / 销毁cle 三来源。
3. 重写到**独立新库**(`dems/db_full/`,不碰 Q5 版)。
4. 分析层用"队+型+时间+位置"关联。

### ⚠️ 不再做的
- ~~segmap(ward_destroyed 交点插值)~~ → 弃。
- ~~use-cle 顺序配对~~ → 弃。
- ~~entity_index 作主键~~ → 弃。

## 十三、【已验证】GamerulesProxy 官方时钟字段 = 精确 tick↔cle（8946650558）

### 实测（probe_gamerules_clock, 8946650558）
- `CDOTAGamerulesProxy` 字段可采集到,含官方时钟:
  - `m_pGameRules.m_flGameStartTime = 943.500`(**号角绝对基值,精确**)。
  - `m_pGameRules.m_nGameState`(=5 即 GAME_IN_PROGRESS)、`m_nPauseStartTick`/`m_nTotalPausedTicks`(暂停)、`m_bGamePaused`。
- ⚠️ **关键**:gameState 变 5 时 R(回放秒)≈929,但 `m_flGameStartTime=943.5` → **号角基值应用 `m_flGameStartTime`**,不能用"gameState=5 出现时刻"(两者差 ~14s)。这解释了旧 game_state 基值偏差。

### 结论
- `m_flGameStartTime` 是**权威号角 0:00 基值** → 用它精确算任意 R 的显示时钟(`clock = R − base` 或 `cle − GS`),杀 segmap。
- 号角前(0:00 前)时间戳应得**负值(正确语义)**,不再是错误外推。

### 下一步
1. 用探针验证 **号角前** 的实体 R → 显示时钟(应为负,如 -1:30)。
2. 写 parser 显式采集 GamerulesProxy 时钟字段 + 用 `m_flGameStartTime` 做基值。

## 十四、【已落地】新架构:combat_log 通用表 + 实体空间层(970 场全量,db_full)

### 结论(底层 agent 已按 COMBAT_LOG_REWRITE.md 完成)
- **新库** `dems/db_full/<league>/<match>.db`(970 场全量,独立,不覆盖 Q5 版 `dems/db/`)。
- **combat_log 表**:一行=一条 combat 条目,`type_category` 枚举(=游戏内 toggle),全类型全量不聚合;字段含 `attacker/target/inflictor/damage_source/value_name`、`value`、`health`、`location_x/y`、`a_team/t_team`、`stack_count/modifier_duration`、`assist_players`、`is_*_hero`、`raw_json`、`t_cle/t_tick`。
- **散装 combat extractor 已删**:game_events 只剩 `building_spawn/destroyed` + `ward_placed`(实体空间层)。combat 叙事全部进 combat_log。
- **实体空间层保留**:`entity_snapshots`(ward/hero/building/networth)、`game_events.ward_placed`(位置/型/队/t_cle)。

### 8946650558 核对(已逐条验证)
- combat_log 分布:damage 25945/modifier 19703/xp 3617/death 3547/healing 3488/gold 2985/ability 2097/item 1939/playerstats 239/gamestate 9。
- **插眼 item+inflictor=item_ward_***:**给队友**保留(target 非空,t_self=false,如 cle=859.8 Spirit Breaker→Winter Wyvern,-1:23.7);真插眼 target 空。
- **KOTL dispenser**(cle=884.4,号角前 -0:59.1)已正确落 tx(此前散装遗漏)。
- **销毁 death target=npc_*_wards**:`a_team!=t_team`=被反(如 1000.2 队2反队3 / 1053.3 队3反队2=#6);`a_team==t_team`=过期(observer 自然到期)。
- **实体 ward_placed 带 t_cle**(GamerulesProxy 官方时钟精确游戏时钟)。

### 守卫数据新路径(分析层从 db_full 读)
- **插眼**:`combat_log WHERE type_category='item' AND inflictor LIKE 'item_ward%'`(排除 target 非空=给队友;dispenser 判型用实体)。
- **实体位置/型/队**:`game_events.event_type='ward_placed'`(t_cle);`entity_snapshots`(ward)。
- **销毁**:`combat_log WHERE type_category='death' AND target LIKE '%ward%'`(a_team≠t_team=被反)。
- **对齐**:`(队,型,时间最近邻 + 位置)`,绝不以 entity_index 为主键。

### 下一步(本次任务)
1. 重写 Q5 分析层(q5_ward.py)从 db_full 读(combat_log 叙事 + 实体空间)。
2. 重跑 Q5 统计 + 重建 Q5B viewer。
3. **推到公网**,总控在公网账目检查。

## 十五、【记录】GitHub Pages 部署方式 + 以前重大事件

### GitHub Pages 上传方式(✅ 已确认可复用)
- **发布仓库**:`publish_repo/`(项目根下独立 git repo)= `https://github.com/BigFatBlackWhale/DSH-Dota2.git`(push URL 已配 token,无需登录)。
- **发布脚本**:`publish.ps1`(项目根)。把 `analysis/output_review/*.html` 复制到 `publish_repo`,`git add -A` + `commit` + `push origin main`。
- **公网 URL 格式**:`https://bigfatblackwhale.github.io/DSH-Dota2/<file>.html`
  - Q1 `q1_value_zones_viewer.html` / Q2 `q2_all_teams_viewer.html` / Q3 `q3_rel_viewer.html` / 单场 `viewer_*.html`。
- **部署 Q5**:把 `analysis/output_review/q5b_ward_viewer.html` 拷进 `publish_repo` → `git add/commit/push`。

### 以前重大事件(教训)
1. **Q1/Q2/Q3 viewer 已部署公网**(URL 见上);方案见各 build_*.py + publish.ps1。
2. **Q5 viewer 曾死机 = 前端 JS bug**(非数据):Bug A `capValue.toFixed`(clamp 用 DOM range 字符串字段)、Bug B `onmousemove` 的 `px` 作用域。用 **Chrome DevTools 9222 + Node WebSocket CDP** 抓到真实报错。
3. **守卫时间换算弯路**(最终废弃"精修"):误判"插眼无 combat-log";"给队友"(target 非空)须排除;dispenser 判型用实体;entity_index 复用不能作主键;**segmap/顺序配对/贪心 = 精修 → 弃**。根本解 = GamerulesProxy 官方时钟 + combat_log 通用表。

### 当前任务(自己全跑,不用 subagent)
1. 重写 `analysis/q5_ward.py` 从 `dems/db_full/` 读(combat_log + 实体,队/型/cle+位置 对齐)。
2. 重跑 Q5 + 重建 `q5b_ward_viewer.html`。
3. 推公网(`publish_repo` → push)。

---

## 十六、【已修·订正】Q5 四处口径错误(owner 现场验证揪出, 8830423116 实证)

### 起因(用户拿游戏复核, 我报错了)
我报某支眼"6:39 插 / 15:47 被反 / 坐标(602,−946) 地图中心"。用户现场读游戏 log 纠正:**那支眼 8:53 才插、15:47 被反、在【地图左上方边缘】**;6:39 那条是**另一支眼自然到期**(log: `sentry ward is killed by sentry ward`)。

### 错误1:放置时刻用了错的时钟字段(已修)
- `game_events.ward_placed.properties.t_cle` **恒比 combat_log 体系早 540.26s**(全表 `t_cle − t_tick = −531.53`;而 combat_log 恒为 `+8.73`)。
- 旧代码在无 combat use 命中时 fallback 到实体自带的 `t_cle` → 插眼时刻提前 → 凭空造出"存活 500~900s 的超长真眼"。
- **修**:放置时刻一律由实体 `t_tick`(与 combat_log 同轴)**经 `t_tick→t_cle` 映射还原**;实体自带 `t_cle` 禁用。

### 错误2:销毁用"窗口内最早死亡"→ 相邻同队同型眼互相抢死亡(已修)
- 旧代码在 `[出生, 出生+寿命]` 内取**最早**的同队同型 death。实测 8830423116:中心眼 `eidx=2474`(15:39.07 插)**抢走了**左上眼 `eidx=3508`(8:52.20 插)在 15:46.67 的被反,并让 3508 反被判成"到期"。
- **关键约束**:`ward_placed` / `entity_snapshots` 只给**出生位置**;而 **Death 不带坐标**(`dota_parse/src/parse.rs` 注释明载 "Death entries carry no position")。→ 定位死亡**只能靠实体自身末现**。
- **实测标定**(40 场):实体快照跨期 = 寿命 + 尾差(1Hz 量化,主峰 ≈9s);到期死亡 `tt = 出生tt + 420` 精确到 0.0x s。
- **修**:① 到期**精确配对**(出生+寿命 ±2.5s, `attacker==target`);② 其余(non-self)按 **`实体末现 − 9s`** 一一对应(每个 death 只用一次);③ 兜底 = 出生+寿命。

### 错误3:到期口径(owner 裁定,已修)
- 权威口径(`parse.rs` 文档):**`attacker == target` ⇒ 自然到期**(守卫被"它自己"销毁);其余 = 被敌方英雄/小兵/塔/野怪打掉。
- 40 场 4916 条守卫死亡分类:到期 3289(66.9%)、敌方英雄反眼 1447(29.4%)、小兵/塔 90(1.8%)、中立野怪/召唤物 ~65(1.3%)、**同队英雄自毁 25(0.5%)**。
- 旧口径 `a_team != t_team` 会把这 25 条自毁当"非被反" → 或被当到期、或让早死的眼 fallback 成活满 420s(双错)。
- **owner 裁定:同队英雄自毁在统计意义上计入"被反"** → `isdew = (attacker != target)`。
- 分眼型(40 场):**假眼**死亡 1523 = 到期 969(63.6%) / 真·敌方反眼 544(35.7%) / 自毁 10(0.66%),无野怪;**真眼**死亡 3393 = 到期 2320(68.4%) / 敌方 1033(30.5%) / 自毁 15(0.44%) / 野怪 25(0.74%)。

### 错误4:到期时刻用死亡事件定时(已修)
- 判为 `expired` 的眼,销毁时刻**直接取 `出生 + 寿命`**(真眼 420 / 假眼 360);Death 只用于**确认**到期,不用它定时 → 消除 0~2.5s 记账误差(上一版全量残留 132 条 420.5~422.5s)。

### 判型可靠性(独立验证)
- 判型唯一来源 = **实体类名**(`ward_class_type()`: `CDOTA_NPC_Observer_Ward_TrueSight`=真眼 / `CDOTA_NPC_Observer_Ward`=假眼)。
- **不可**用 combat inflictor 判型 —— 40 场交叉表:`item_ward_dispenser` → observer **829** / sentry **1313**(两类都出);`item_ward_observer`→observer 694;`item_ward_sentry`→sentry 1488。
- **独立互验（2026-09 订正）**：早先写的"按逐场计数相等（40/40）"是**错误的验证方法** —— 抽样 40 场恰为 0，全量并不相等（真眼 placed 70,421 vs death 69,020；假眼 38,813 vs 38,208），差额来自**右删失**（比赛结束时仍存活的眼没有死亡事件，实测 232/970 场存在差额）。
  改用**游戏自身寿命机制**验证判型：200 场里所有 `attacker==target` 的到期眼，`(销毁 − 放置)` **精确等于该型寿命**（真眼 420 / 假眼 360；|偏差| ≤ 0.01s 命中率 **100%**，真眼 9,593/9,593、假眼 5,151/5,151）。若类名↔型对调，真眼会落在 +360 → 判型正确。证据落盘 `analysis/output_q6/q6_verify.json`。
- 探针 `probe_truesight`(直接读 dem)实测:combat log 的 target/attacker **不含实体类名**(`total=0`)→ 判型只能来自实体表。
- 地图方位校准(用建筑):Radiant 塔 x<0,y<0(左下);Dire 塔 x>0,y>0(右上)→ **中心=(0,0)**,左上方 = x<0 且 y>0。

### 订正后实测
- 60 场批量:超寿命(>寿命+0.5s)仅 5 条真眼 / 2 条假眼,最大超出 2.2s(修 4 后归零)。
- 970 场全量:见「十七」。

---

## 十七、【实测】订正后全量结果(970 场 / `dems/db_full`,分辨率 172)

- 真眼明细 **70,421** 条;假眼 **38,813** 条(计数与修复前一致 → 修复只改时间/归属,不改眼的识别)。
- `survival_sec`:min **0.5s**,max **420.0s**;`> 420s` = **0 条**。→ 前置修复(寿命判定改 cle 空间)后,上一版残留的 132 条(420.5~422.5s)以及唯一的 420.5s 边界条全部归零。
- 判定构成(真眼):**被反 22,570(32.0%)/ 到期 47,851(68.0%)**(前置修复后重建)。
- **右删失**:真眼 1,401 支 / 假眼 605 支在本场结束时仍存活 -> 无死亡事件,现按"出生+寿命"计(高估这部分存活,待 owner 裁定是否改为"存活到比赛结束")。
- 对照修复前:survival>420 有 7,203 条(占 10.2%,最小 −2,693s)。
- 样本实证(8830423116, 与用户现场一致):
  - `eidx=3508` 真眼 (-6248.5, 7396.8) **左上边缘**:8:52.20 插 → **15:46.67 被反**,存活 414.5s
  - `eidx=2474` 真眼 (603.8, −983.6) **中心**:15:39.07 插 → 16:28.30 被反,存活 49.2s
- 部署:`publish_repo` push `d8ac7db`(后于前置修复后重建为 `b2264c6`;Q6 为 `9c3bf46`)

---

## 十八、【2026-09 续】Q6 交付 + 三处新发现(判型验证法订正 / 右删失 / 滚轮卡死)

### 18.1 判型验证方法订正(重要:我早先的验证方法是错的)
- 早先写的"实体类名判型计数 vs Death 权威单位名**逐场相等(40/40)**"**不成立**:全量 970 场并不相等(真眼 placed 70,421 / death 69,020;假眼 38,813 / 38,208;**232/970 场**有差额)。抽样 40 场恰好为 0 属巧合。
- 真因 = **右删失**:比赛结束时仍存活的眼没有死亡事件 → placed 计数必然 ≥ death 计数。
- **改用游戏自身寿命机制验证(硬证据)**:200 场里所有 `attacker==target` 到期眼,`(销毁 − 放置)` **精确 = 该型寿命**(真眼 420 / 假眼 360;|偏差| ≤ 0.01s 命中率 **100%**,真眼 9,593/9,593、假眼 5,151/5,151)。若类名↔型对调,真眼会落在 +360 → 判型正确。证据:`analysis/output_q6/q6_verify.json`。

### 18.2 右删失(口径待 owner 裁定)
> ⚠ **本节结论已被 §19 推翻**:这里用的"比赛结束"是 `MAX(t_cle) FROM combat_log`,属**结算残留**(比真实结束晚 360~925s)。
> 正确的"比赛结束" = **远古被摧毁**;按正确口径,被截断的假眼是 **3,308 支(8.52%)**,而非下面写的 890 支(2.29%)。请以 §19 为准。
- 假眼 **890/38,813(2.29%)** 属"放置后比赛剩余 < 360s"被截断(真眼 1,401 支无死亡)。
- 现按 `出生+寿命` 当"到期" → 高估这部分存活。**对刁钻判定影响极小**:其中 193 支被判刁钻,而"真实最长存活 ≤60s"(可能误判)的最多 **4 支,占刁钻 0.04%**。
- 已在 `analysis/q6_observer.py` 实现开关 `--censor-at-end`(改 `destroy = 比赛结束时刻`,明细显示"存活到结束")。

### 18.3 刁钻口径开关(裁定后一条命令重算)
`--tricky-radius`(默认 1200;可 1050)、`--tricky-min-overlap`(默认 0;可 15/30/60 秒共存下限)、`--tricky-min-surv`(默认 60)。
8 场样本量级:默认 90 支(29.5%) ｜ 半径1050+共存≥30s+右删失 → 19 支(6.2%) ｜ 仅共存≥60s → 32 支(10.5%)。

### 18.4 Q6 viewer 交互 bug 与回归测试固化
- **滚轮卡死**(`1dfb0d8` 修):从"单格视野(172 单位)"滚轮缩小被 `nw < CS*1.5` 守卫整个 `return` → 用户卡在单格视图出不来。改为**钳制**(最小半格 86,超全图复位),拖拽也夹图内。对照 Q5B 用"钳制到最小 500",无此问题(已实测)。
- **回归测试固化**:`node analysis/q6_viewer_itest.js`(Node + DOM/Canvas 桩,真实事件序列,12 项断言,退出码 0/1)。本机沙箱 Chrome 起不来(crashpad 自杀)、TLS 不可用 → 这是唯一可行的前端验证手段;首跑即抓到滚轮卡死。 → `https://bigfatblackwhale.github.io/DSH-Dota2/q5b_ward_viewer.html`(前一版为 `7bb1872`)。

---

## 十九、【2026-09 续】根因订正:「比赛结束」的权威取法错了 → 末段存活被系统性高估

### 19.0 结论先行
- 18.2 写的"假眼 **890/38,813(2.29%)** 属右删失"是**错的** —— 因为那时用的"比赛结束时刻"是 `MAX(t_cle) FROM combat_log`,而它落在**结算残留**的末尾,比真实结束晚 **360~925s(中位 398s)**。
- 权威的"比赛结束" = **远古(Fort)被摧毁的那一刻**:`combat_log.type_category='death' AND target IN ('npc_dota_goodguys_fort','npc_dota_badguys_fort')`(970/970 场都有;实测 30 场全部命中)。
- 用权威结束重算:被截断的假眼是 **9.2~9.9%(30 场抽样 106/1153;5 场抽样 20/202)**,不是 2.29%。也就是说 **约 10% 的假眼"存活"原先被高估** —— 直接影响 Q6 头号指标"平均存活",尤其是 20+ 时间窗与"刁钻"判定。
- 修复落在**共享解析层** `analysis/q5_ward.py`(新增 `game_end_cle()`,`parse_match(..., censor_at_end=True)`),Q5B 与 Q6 同时受益。**Q5B 已交付的数字需要用重算后的数字替换**(Q5B 真眼同样被截断,量级见 19.3)。

### 19.1 证据链
1. **远古死亡 vs 战斗日志尾部**(3 场逐条打印):
   - `8825993964`:远古 45:20 被摧毁(disp 2720.3s),但 combat_log 一直记到 disp **3115.0s(51:55)**,尾部还含 observer/sentry 的死亡条目与 `gamestate` 条目。
   - 30 场统计:尾部长度 min **360.0s** / max **925.0s** / 中位 **397.7s**;尾部里的**守卫死亡共 354 条**(结算残留)。
2. **`ward_placed` 在远古死后为 0 条**(30 场 0 条)→ 没人再插眼,说明那段确实不是"比赛还在打"。
3. **时钟空间确认**(这条以前没写清,踩了坑):`game_events.game_time_sec` 与 `entity_snapshots.game_time_sec` 都是**t_tick(复播时钟)空间**,不是 t_cle。实证:最后一条 `ward_placed` 的 `game_time_sec=3672.0` 而同一行 `properties.t_tick=3672.97`(相等),`properties.t_cle=3271.7`(不等)。
   - 因此 `q5_ward.game_end_marker()`(取 `game_events.game_time_sec` 最大值)**不能**直接与 cle 空间的 `place` 相减;它当时只用于暂停块截断(同一空间内比较),没错,但被误当成"比赛结束"就会错。
4. **旧开关错在哪**:`--censor-at-end` 用 `MAX(t_cle) FROM combat_log` → 只在"比赛最后 0~6 分钟(其实是赛后残留段)"才生效,所以只标出 890 支;换成权威结束后同一口径标出 **9~10%**。

### 19.2 修复内容(机制级, 一处改三处受益)
- `q5_ward.game_end_cle(con, mid)`:**唯一**的"比赛结束"取法(远古死亡, cle 空间),注释里写明依据与实测数字。
- `q5_ward.parse_match(con, mid, censor_at_end=True)`:未被反的眼若 `出生+寿命 > 比赛结束` → `销毁 = 比赛结束`,`存活 = 结束 − 放置`,并置 `censored=True`(reason 由 `expired` 改 `censored`);被反的眼若死亡记录晚于结束 → 截断到结束并同样标 `censored`(实测假眼 0 条,真眼待全量核对);结束后才插的眼(实测 0 条)→ `reason='post_game'`。
- `--no-censor-at-end`:退回旧口径(存活=寿命, 是**上界**),供 owner 对照。
- **导出值自洽原则**(Q6 侧):`place/destroy` 先定到 0.1s,`surv = round(destroy−place,1)`,`win = f(导出 place)`,`tricky` 也按导出值(存活>60 且 共存>下限)判定 → 之前"存活 ≠ 销毁−放置(3,285 行)""存活恰好 60.0s 却标刁钻(26 行)""win 与 place 落在不同窗口"三类自相矛盾行**全部归零**,校验器不再需要任何豁免。

### 19.3 影响面(必须同步给 owner) —— 全量实测数字
- **Q6(假眼 38,813 支)**:
  | 口径 | 平均存活 | 刁钻 | 被反 | 截断行 |
  |---|---|---|---|---|
  | 新(截断按 结束−放置) | **265.0s** | **10,316(26.6%)** | 13,061(33.7%) | 3,308(8.52%) |
  | 旧(截断按寿命 360s) | 280.5s | 10,485(27.0%) | 13,061(33.7%) | 890(2.29%,判据本身是错的) |
  - 分窗:**0-7 窗完全不变**(313.9s / 1,936 支);7-20 窗 283.7 vs 284.5s;**20+ 窗 233.5 vs 264.2s(−30.7s)**。
  - 截断行平均存活 **178.3s**;其中 **515 支**真实存活 ≤ 60s(旧口径一律记 360.0s = 最严重高估);**165 支**旧判刁钻、新口径下不再成立。
  - 被反数、假眼/真眼总支数、判型、放置时刻匹配 **全部不变**。
- **Q5B(真眼 70,421 支)**:从两个版本的 viewer 内嵌数据**逐格**对比 —— 反假眼归因 9,384 / 反真眼 16,873 / 格数 15,577 / 被反 22,570 **全部不变**;
  只有"存活"列变:平均 **331.8s → 308.3s**(−23.5s),标截断 **0 → 7,879 支(11.2%)**,最大存活仍 420.0s。
- **已被推翻的旧交付样本**:`8888160657` Aurora Gaming 夜魇 (-1299,-4388) 原记"38:52.23→44:52.23 存活 360s、敌真眼 6 单位、共存 303s";
  真相 = 该场**远古 39:50.90 被摧毁**,眼 39:25.30 插下只活 **25.6s** → 旧数字正是残留段造成的。新清单见 `STRATEGY/Q6_OBSERVER_VIEWER.md §9`。


### 19.4 验证手段(可复跑)
- `python analysis/q6_check.py --sample 250` → **ALL PASS(exit 0)**;其中 **G 节**逐场取权威结束时刻,对**全部 38k 行**核三条:`存活 ≤ 结束−放置`(容差 0.2s=0.05 截断容差+0.1 导出网格)、截断行 `销毁 == 结束`、该截断的没漏标。
- `python analysis/q6_samples.py --tricky 3 / --censored 3 --short / --worst 2 / --at mid:x:y[:team]` 出可直接进游戏核对的证据链。
- `node analysis/q6_viewer_itest.js` → 30+ 断言全过(含多选战队、共存门槛、全量剔除截断眼 35,505 < 38,813)。
- `analysis/q5_ward.py --no-censor-at-end` 与默认跑对比,量化截断影响。

---

## 二十、【2026-09 续】owner 三条需求变更(改名 / 战队多选 / 刁钻加共存 ≥60s)

- **交付物改名**:`q6_observer_viewer.html` → **`q6_ward_viewer.html`**(旧 URL 留 `<meta refresh>` 跳转页, 免得已分享的链接 404 或停在旧数字)。commit `29d2940`。
- **战队筛选改多选**:40 支复选框 + 全选/清空, 空选 = 全部;聚合与钻取都跟随所选集合(前端聚合本来就按行过滤, 只改判据)。
- **刁钻加"与敌方真眼最长共存 ≥60s"**:开关 `--tricky-min-overlap` 默认 0 → **60**;判据改为
  `存活 > 60.0 且 共存(0.1s 网格) ≥ 门槛`;`n_es / d_min / ovl` 三列**同步按同一门槛**统计(口径可从导出数据 100% 复现)。
  依据:旧口径下 **31.3%** 的刁钻与敌真眼共存 <5s(真眼恰在假眼插下那刻到期), 语义上不算"近处有真眼却没被反"。

| 全量 970 场 | 旧(共存门槛 0) | 新(共存 ≥60s) |
|---|---|---|
| 刁钻 | 10,316(26.6%) | **4,028(10.4%)** |
| 被门槛排除(半径内有交集但不达 60s) | — | **10,266 支** |
| 被反 / 截断 / 假眼总数 | 13,061 / 3,308 / 38,813 | **完全不变** |
| 刁钻共存 min/中位/max | 0.1 / 21.0 / 360.0s | **60.1 / 170.7 / 360.0s** |
| 刁钻最近敌真眼落在 1050–1200 的占比 | 15.6% | **36.2%**(门槛砍掉的多是"真眼刚到期"的短共存条目) |

- 仍待 owner 裁定:**距离上限是否从 1200 收到 1050**(真视半径;现 36.2% 的刁钻落在真视照不到的 1050–1200 缓冲带)。

### 20.1 任务书剩余三条已补齐(commit `54e9f44`)
- **出场场次指标**:该格·当前筛选下去重场次(气泡与钻取汇总都显示);全量下每格最多 603 场(≤970 场,自检通过)。
- **点眼锁定**:先点格、再点该格内的眼点(命中 10px) → 显示 `match_id/坐标/双方队名/放置/销毁/存活/被反/截断/刁钻/期间敌方真眼`;点明细表任一行同效。
  ⚠ 设计取舍:曾把"点眼点"做成全局优先命中, 回归测试立刻抓到它**抢掉"点格钻取"**(点哪儿都可能命中眼点) → 改成"只在已选中的格内生效"。
- **多分辨率源数据**:新增 `analysis/q6_bins.py`, 从实例 JSON 秒级导出 1/4/16/172 四档 (格×窗×战队) 聚合 + 灵敏度对照表。
  实测 1/4/16 三档的被反率(33.6/32.5/31.7%)、刁钻率(10.3/9.9/8.9%)、平均存活(265.2/267.7/268.0s) 几乎不动 → **结论对分辨率不敏感**;
  172 档的"格均值"偏离(平均存活 249.5s) 主要是"格均值 vs 全局值"的加权差, 引用时要注意。
- **距离上限 1050 的精确影响(已算好, 等 owner 一句话)**:判据只要求"半径内存在共存 ≥60s 的敌真眼", 故 `d_min ≤ 1050` 的筛法与大半径重跑**等价** —— 
  半径 1200 → 刁钻 4,028(10.4%);**半径 1050 → 2,564(6.6%)**, 砍掉 1,464 支(现刁钻的 36.3%);分窗 0-7 634→328 / 7-20 1,576→957 / 20+ 1,818→1,279。

### 20.2 交付前的鲁棒性/自包含收口(第 5 轮)
- **最坏格压力测试**(回归测试新增):实例最多的格 **(42,24) 1,174 支** → 钻取表渲染 **40ms**、td 行 1,251,**无卡顿**(阈值 2000ms)。
  ⚠ 顺带订正:早先文档里"最大格 **2,442** 支"是**我把脚本输出的整数键读成了支数**(真实支数 1,174;键 2442 是 `cx+cy*100` 的编码) —— 已改。
- **空结果鲁棒性**(新增断言):极端筛选(某队 + 只看刁钻 + 剔除截断眼)下聚合与空格钻取都不抛异常, 出"该格在当前筛选下没有假眼"空态。
- **D7 自包含检查**(新增):viewer 内**不含任何外部资源引用**(`http(s)://` / `//` / `url(...)`), 保证"单文件、双击即开、离线可用"。
- **E 网格边界(信息项)**:19 支(0.049%)眼落在 `|x|` 或 `|y|` 略超 8600 的图外角 → 格号会算出 -1/100。
  分析器不夹取格号;viewer 按世界坐标画点/上色不受影响;`q6_bins.py` 导出时夹进边界格。只影响明细 CSV 里这 19 行的 `cell_x/cell_y`。
- **判型互验改为常驻脚本** `analysis/q6_verify.py`(`--sample 0` = 全部 970 场), 输出 `q6_verify.json`:含
  `到期未截断且 (销毁−放置) == 寿命` 的命中率、超寿命行数、以及**逐型计数对账**(眼数 / 权威单位名死亡数 / 截断数)。
  全量结果:假眼 **22,444/22,444 = 100%**(360s)、真眼 **39,959/39,959 = 100%**(420s);超寿命 **0**;
  对账 假眼 38,813 / 38,208 / 截断 3,308,真眼 70,421 / 69,020 / 截断 7,892(差额 605 / 1,401 = 整场没有死亡记录的眼)。

### 20.3 明细补"任意交集"口径 + 格号夹取(commit `bdafc8c`)
- **问题**:按"共存 ≥60s"门槛统计时, 一支只共存 30 秒的敌真眼会被算成 **0 支**, 看明细的人容易以为数据漏了(实测 **10,266 支**属此类)。
- **修法**:新增三列 **`n_es_any` / `d_min_any` / `ovl_any`**(半径 1200 内窗口**只要有交集**的敌真眼);
  页面钻取表与点眼详情显示 **达标/任意**(如 "0 支/1");"最长共存"列改用**任意**口径。**刁钻判据不变**(仍用达标口径 `ovl ≥ 60`,
  两者等价性: 任意最长共存 ≥60 ⟺ 存在一支共存 ≥60 的真眼)。
- **格号夹取**:极少数眼落在 `|x|` 略超 MAP_HALF 的图外角(实测 19 支), 明细 CSV 的 `cell_x/cell_y` 现夹进 0..99(以前会出现 -1/100)。
- 校验器新增 **B10**(达标 ⊆ 任意、距离存在性与支数一致)与 **C7b**(250 支样本上独立重算任意口径三列全等);回归测试增至 **63 项**。
- 数字不变:刁钻 **4,028** / 被反 13,061 / 截断 3,308 / 假眼 38,813。

### 20.4 前端 bug:底图不跟随视野(owner 实测发现, commit `6c10c59`)
- **现象**(owner 原话"底图缩放有问题, 没有跟着走"):放大/平移后, 热力块与眼点按视野变换重画了, 但**底图永远是整张全图的缩小版** → 底图与数据错位。
- **根因**:`render()` 里一直写 `ctx.drawImage(bgimg,0,0,CSX,CSX)`(5 参 = 整张贴满画布), 完全没走视野变换;Q5B 用的是 **9 参源裁剪**(把视野对应的世界矩形映射到图像像素后铺满画布), Q6 漏了这一步。
- **修法**:放大/平移时改成
  `p0 = w2p(视矩形左上)`, `p1 = w2p(视矩形右下)`, `drawImage(bgimg, p0.x, p0.y, p1.x-p0.x, p1.y-p0.y, 0, 0, CSX, CSX)`;全图时保持整张贴。
- **为什么回归测试没抓到(教训)**:Node 的 Canvas 桩把 `drawImage` **参数丢了**(只计数), 而且 `bgReady` 一直是 `false`(桩的 `Image.onload` 不会触发) → 底图这段分支**根本没执行**。
  修好桩之后(记录参数 + 暴露 `forceBg()`), 断言立刻红了 6 条, 修复后转绿:
  - 全图 = 5 参;放大 = 9 参且**裁剪宽高 == 视野世界矩形**(590.4×590.4 实测 == 期望)、左上角一致、目标铺满画布
  - 平移后**裁剪原点跟着移动**(214.2 → 86.6)
  - 单格视野裁剪 ≈ 172×K = **8.43px**
  断言总数 63 → **71**。
- **教训小结**:桩只"数调用次数"是不够的 —— 任何"参数决定正确性"的绘制(裁剪矩形、颜色、坐标)都要把参数记下来断言, 且要保证被测分支真的被走到(这里靠 `bgReady` 强制打开)。

### 20.5 前端两处交互订正 + 一个隐藏 bug(owner 实测, commit `c363486`)
- **点格一次性放大 100 倍**:原先点格直接把视野设成"那一格"(172 单位, 全图 17200 → 100×), 周边全看不见。
  改为 `CLICK_ZOOM=4`:以该格为中心按**当前视野放大 4 倍**(最小半格 86);连续点不同格继续 4 倍;点同一格复位。
  (回归断言:`12040 → 3010 → 753`, 且视野中心 == 该格中心)
- **眼点是小圆圈而不是官方图标**:`drawDots()` 一直在画 `arc`(直径 7~9px), 而页面里**早就内嵌了官方假眼图标 base64**(1,922 字节, `npc_dota_ward_base.png`)却从未使用。
  现改为 `drawImage(obsIcon, …)`:选中格 **28px**(与 Q5B 同尺寸)、只看视野时 16px、过密(>900)自动缩到 ~10px 并在描述栏注明;
  颜色编码(橙=刁钻/蓝=普通/红=被反)改画在**图标外圈**, 锁定眼加白圈; 图标加载失败时兜底画实心点。
  (回归断言:每个眼点都用官方图标画一次、尺寸 ≥20px、只画选中格里的眼点)
- **隐藏 bug**:`bgimg.onload` 里调用的是**不存在的 `draw()`**(渲染函数叫 `render`) → 真实浏览器里图加载完成时该回调抛 `ReferenceError`,
  底图要等下一次交互才出现。已改 `render()`, 并把 Node 桩的 Image 改成**异步**触发 onload(同步触发会撞 TDZ, 也就掩盖了这个错误), 新增两条断言。
- 回归测试 71 → **84 项断言**; 校验器仍 **ALL PASS**; 数字不变(刁钻 4,028 / 被反 13,061 / 截断 3,308)。

### 20.6 频率热图重做(owner: "总频率也是很重要的热图", commit `83ab319`)
- **问题 1: 原始计数不可比**。`假眼出现次数` 是该格的眼数, 会被"该战队打了多少场"淹没(有的队 200 场、有的 10 场)。
  → 新增两个归一化口径(分母 = **当前筛选下的去重场次数**, 换战队/窗口时自动变):
  - **每场出现次数** = 支数 ÷ 分母场次(全量最热格(42,24) **1.210 次/场**)
  - **出现率** = 该格至少 1 支假眼的场次数 ÷ 分母场次(同格 **62.2%** —— 六成比赛都会在这个点位插眼; 第二名格(76,54) 59.5%)
- **问题 2: 色标把热点压平**。计数是长尾分布: 实测 **p95 = 31 而 max = 1,174**; 自动上限取 95 分位时 **221 格(5.2%)被压成同一色**,
  热点全糊成一片红。→ ① 计数类指标(n_obs / n_match / n_tricky)自动上限改取 **p99**(压顶 1.0%); ② 计数类改**对数色标**
  (`t = log(1+v)/log(1+上限)`, 实测 t(1)=0.151 而线性只有 0.010, 低端拉开 15 倍), 率/均值类仍线性; 描述栏标出"对数/线性色标"。
- 气泡与钻取汇总补上: 每场次数、出现率、**分母场次**。
- 回归测试 84 → **93 项**(断言: 分母 == 当前筛选去重场次并随筛选变化、逐格 `每场次数 == 支数÷分母`、`出现率 == 出场÷分母`、
  `出现率 ≤ 100%`、计数对数色标端点与拉开倍数、率类仍线性)。

### 20.7 UI 交互三条订正(owner 实测, commit `c70265d`)
1. **点格必须每次都放大 4 倍**(含反复点同一个格)。原先"点同一格 = 复位到全图", 用户看到的是"每次点都只是 全图/4"。
   现改为**连点持续 4 倍**(最小半格 86), 回全图用新增的 **`复位视野` 按钮**或滚轮缩小。
   ⚠ 还有第二层原因(测试才挖出来): 选中格后 16 支眼的 **28px 图标在那个缩放级别(格子仅 25px)会盖满整格**,
   "再点一下"必然命中图标 → 走成"锁定该眼", 表现就是"点它不放大"。现在 **眼点图标只在放大到 ≤8 格(1376 单位)时才绘制**
   (那时格子 ≥128px, 图标分得开、点得准), 浅层缩放一律"点格放大 4 倍"; 任何缩放级别都能**点明细表行**锁定某支眼。
2. **平移改为按住鼠标中键拖动**(owner 要求), 左键拖动不再平移。
   顺带修一个真 bug: 原 `dragMoved`(拖动后吞掉下一次点击)守卫在中键拖动下**不会自清** —— 浏览器中键只发 `auxclick`、不发 `click`,
   于是**下一次左键点击会被吃掉**。左键本来也不再平移, 该守卫已删除。
3. **新增阵营 toggle**(全部/天辉/夜魇): 数据 `team` 2/3, 可与战队多选/时间窗叠加; **频率指标的分母场次跟着重算**。
   实测 天辉 19,434 支 + 夜魇 19,379 支 = 38,813 ✓。
- 回归测试 93 → **111 项**(v12); 校验器 **ALL PASS**; 数据数字不变(刁钻 4,028 / 被反 13,061 / 截断 3,308)。

### 20.8 部署可信度:公网校验 + 构建戳记(owner 反馈"改了但我完全没看出来", commit `693546e`/`3978b30`)
- **现象**:owner 说 UI 改动"完全没看出来"。排查结果: **改动确实已发布** —— 公网 HTTP 200、含 `setSide/resetView/ICON_MIN_ZOOM_W` 等全部新标记、
  内嵌 38,813 条实例与源 JSON 一致。原因是 **GitHub Pages 发 `cache-control: max-age=600`**, 普通刷新(F5)会继续用浏览器缓存;
  另外若从**旧地址**(`q6_observer_viewer.html`)打开, 命中的可能是改名前的整页缓存。
- **纠正一个此前的错误认知**: 之前记录"本机 TLS 不可用、抓不了公网页"。实测 **Node 自带 OpenSSL 可以直接 fetch** 公网页
  (PowerShell 的 schannel 不行, 但 Node 可以) —— 于是部署校验从"只能相信已推送"变成**可自动比对公网内容**。
- **新增 `analysis/q6_public_check.js`**: 拉公网页面逐项比对 ①**构建戳记** ②新功能标记 ③内嵌 38,813 条实例是否与源 JSON 逐行相同 ④换行归一后 sha256。
  实测: `戳记 == 本地` / `sha256(归一)相同: true` / `内嵌 38813 == 源 JSON` → **PASS**。
  (仓库是 LF、工作区是 CRLF, 直接比字节会差几百字节 —— 归一后才是同一份, 别被字节数误导。)
- **页面头加了构建戳记**("构建 YYYY-MM-DD HH:MM · 功能版本 vN"), 以后"你看到的是哪一版"一眼可验;
  旧地址的跳转页也改成带 `?v=13` 并写明"若在旧地址看到完整地图页, 那是缓存, 请 Ctrl+F5"。

### 20.9 点格缩放口径终版(v15, commit `76f5c02`)
- owner 明确:「通过点击方块格进行的放大**就只有 4x 和非 4x 这两个档位**, 剩下的让用户用滚轮缩放」。
- 规则:点格 = 切到 **4 倍档(全图/4 = 4300 单位)并以该格为中心**;点别的格只**重新居中**(不叠乘);
  点已选中的格不动;已用滚轮缩到 4 倍以内时点格**保持滚轮那个档位**(既不拉回 4 倍、也不继续放大);
  其它档位交给滚轮, 回全图用 `复位视野`。断言实测:`12040→4300`、`别的格 4300→4300`、`滚轮后 3010→3010`、`同格 4300→4300`。
- **三轮才对齐的教训(写进 §8 的 7c/7g/7h)**:我先做成"点一次 = 一格铺满屏(100×)", 再改成"每次叠乘 4 倍", 都不对。
  交互档位这类主观口径,**先要一句话对齐再动手**, 不要靠猜;而且桩测试只能验证"我实现的东西对不对", 验证不了"这是不是 owner 想要的"。

### 20.10 v16 四条(commit `5db7400`)
- **拖动不再带着缩放**:根因是**很多鼠标按住滚轮(中键)时设备仍持续发 `wheel` 事件**, 那些事件被滚轮处理器当成缩放 → 拖着拖着视野在变。
  修法:滚轮处理器开头 `if(dragging || ((e.buttons|0) & 6)){ return; }` —— 中键(位 4)/右键(位 2)正被按住时完全不吃滚轮。
  ⚠ 我先写的是"拖动结束后 250ms 内忽略", 结果**回归测试连续事件全被吃掉**(`极限放大 0 单位`、右键/Shift 拖动不平移) —— 时间窗这类魔法既不好测也不确定,
  改成 `e.buttons` 位掩码后一次通过。
- **4× 档就显示假眼准确位置**:有选中格时, **4 倍档(4300 单位)及以内都画眼点**;尺寸阶梯 **12px(4 倍档) / 20px(≤8 格) / 28px(≤2 格)**;
  画序把普通眼放底层、刁钻/被反压上层。
- **明细表 = 中心格 + 周围 8 格**:新增「格」列(`C`中心/`N`邻格 + 格号), 中心格优先、其余按格距再按时刻;3×3 在大格上可上千行 → **4000 行上限 + 明确截断提示**。
- **布局**:`flex-wrap:nowrap` + `canvas{width:100%;height:auto}` → 窗口不够宽时**地图按比例缩小**, 明细表始终在地图右侧(不再被挤到下面)。
  画布命中换算本来就用 `getBoundingClientRect()` 算缩放比, 所以缩小后点击/拖动依旧准确。
- 回归 115 → **117 项**; 校验器 **ALL PASS**; 数据数字不变。

### 20.11 v17 布局 + v18 owner 两条(commit `7250db1` / `7ad564d`)
- **v17 布局两条**(owner 实测: 地图超出分辨率、与明细表对不齐):
  ① 画布是正方形, 原先 `width:100%` + flex-grow 在大屏被拉宽, 等比放大后**高度溢出** → 改成
     `canvas{width:min(100%, max(320px, calc(100vh - 430px))); aspect-ratio:1/1}`(**边长 = min(列宽, 视口高−页面占用)**);
  ② 控件原来在左栏地图**上方**, 导致右栏明细表与地图顶部差一整个控件块 → 控件抽成**整行块放在两栏之上**, 两栏顶部对齐; 右栏宽 `clamp(360~660px)`。
  回归加 **7 条"布局契约"断言**(本机无法渲染 CSS, 于是把布局规则钉死: nowrap / 边长公式 / 1:1 / clamp / 控件块位置 / 画布在内 / 控件元素仍在)。
- **v18 owner 推送前两条**:
  ① **页面完全去掉"刁钻"统计**(开关/指标/明细列/气泡与汇总计数/橙色编码) —— owner 要重新考虑该统计方式;
     **数据层完全不动**(`tricky` 列、分析器计算、校验器校验都保留), 图例写明"已按 owner 要求移除", 新口径定了把 UI 打开即可;
  ② **4 支中国战队不提供单项筛选**(`Xtreme Gaming` / `Vici Gaming` / `Team Resilience` / `Yakutou Brothers`, 索引 6/32/34/39):
     复选框 40 → **36**; 其数据仍计入"不勾任何一支 = 全部战队"(实测 全选 36 支 = 34,703 支 / 全部 = 38,813 支, **这 4 队共 4,110 支**)。
     构建期按**名字精确匹配**(找不到直接报错退出), 注入前端 `hidden_orgs`; `setAllOrg(true)` 只勾可选的 36 支。
- 回归 117 → **132 项**; 校验器 **ALL PASS**; 数据数字不变(刁钻 4,028 仍在数据里, 只是页面不再展示)。

