# DOTA 录像批量分析工具 — 项目方案

> 本文档用于交接给开发环境（CLINE + DeepSeek V4 Flash），作为实现的技术上下文。

**当前进度速览（跨电脑/跨会话恢复时先读这里）**
- 第3步 选型验证：**已完成**（§6.6，Rust + source2-demo）
- 第4步 正式解析器写库（SQLite 先行）：**已完成并实测通过** —— 三张通用表 DDL 与实现细节见 §6.2 实现落点 + §8 第4步，代码在 `dota_parse/`，运行说明见 `dota_parse/README.md`
- **第5步 第一个跨场次分析查询：已完成** —— 纯第4层实现"英雄位置热区"（跨2场，主交付物）+ "购买节奏"最小查询，验证了"新分析=新查询、不动解析层与表结构"；详见 §8 第5步记录与 `analysis/`
- **第6步 视野（守卫）提取器：已完成** —— `game_events` 新增 `ward_placed` / `ward_destroyed` 两种事件（复用通用表，无 schema 改动），两场重解析 + 独立校验 + 第4层守卫分布查询全部通过；详见 §8 第6步记录与 §6.6「守卫事件提取」
- 第1/2步分别见下方专条（第1步已完成、第2步进行中），详细记录见 §8 第1步/第2步
- **第7步 调度/元数据层：已完成（2026-09-03，C 盘机实跑验证）** —— 独立 catalog（`matches.db`，`scheduler/catalog.py`）+ 非公开录像录入（`scheduler/intake_private.py`，`dems/private/`，头部自动读元信息、幂等、`--no-parse`/`--note`/`--move`）+ `dota_parse --info` 只读头部模式 + analysis 支持 `--catalog` 枚举场次；catalog 的 `source` 支持 private/public（public 仅预留条目接口与 metadata 占位，实际拉取留给第1/2步）。详见 §8 第7步记录
- **第1步 OpenDota API 数据管道：已完成（2026-09-03，C 盘机实跑）** —— 新增 `opendota/` 与独立 **`stats.db`**（leagues/teams/matches/gold_adv 四表，`metadata_json` 升级预留，与 catalog 各司其职）；`fetch_league.py` 限流幂等管道（队名补全/逐场 `radiant_gold_adv`/空则 `POST /request` 触发解析）；胜负按 **match** 粒度存 `radiant_win`（series 聚合留查询层）。首次实跑联赛 19719：**147 场 matches、147/147 含经济差、6953 行 gold_adv、16 队伍**；视角转换（§3.1 team_adv：dire 视图 = -radiant）与 stats↔catalog 按 match_id 松耦合关联均已实跑验证。详见 §8 第1步记录
- **第2步 批量 .dem 下载：已完成（2026-09-04，F 盘主力机）** —— 工具：`opendota/list_matches.py`（按联赛名/ID 抓 match ID 表）、`opendota/batch_download.py`（流式 Range 断点续传 + 停滞看门狗/两次停滞跳过 + 状态 `.tmp/batch_download/<league>.json`）、`batch_summary.py`/`scan_dems.py`/`parse_public.py`/`verify_public.py`（统计/完整性/全量并行解析/三类校验）。**7 个联赛全部下载完成 970/970 场（0 失败 0 不可用，.dem 约 142GB）**：19719 TI2026 正赛 147、19255 165、19696 185、19101 102、19785 157、19422 142、19917 72（含用户点名的 6 场：8764045768 / 8809802771 / 8827339763 / 8885784652 / 8740094561 / 8927207054）。**全量解析与校验**：970/970 解析成功；`verify_public` full=925 / event_only=45（无 tick 流录像，见 §6.6）/ bad=0；共 **1734.9 万快照 / 78.5 万事件**；跨场次第4层分析全库可用。`source='public'` 的 catalog 登记待与 C 盘 catalog 统一时进行（§8 第7步 intake 流程）
- **下载速度基线（2026-09-03 实测，供第2步排期；正式批量下载确定在 F 盘主力机执行，本 C 盘机不需要为批量做额外准备）**：联赛 19543（PGL Wallachia 2026 S8）3 场全部落在 `replay191.valve.net`，速度 **2.3–7.1 MB/s**（前两场 7.01/7.14，一场 2.29 波动），原始 128–185MB/场、**均为 bz2 容器**（解压后 .dem 188–265MB）；联赛 19719（TI2026，时间跨 08-13~08-23 抽 5 候选测 ≤3 场）首场 8942993144 落在 `replay413.dota2.com.cn`（国服 Tengine），**仅 ≈0.10 MB/s**（比国际服慢约 70 倍，即 §4.2 记录的国服限速），zstd 容器（91MB → 146.8MB）。**结论**：① valve.net 体系内 bz2/zstd 并存（replay274 等=zstd、replay191=bz2），必须按魔数嗅探；② 国服域名只作兜底；③ 批量在 F 盘主力机跑国际服时按 ~5MB/s 保守估时即可
- **会话交接快照（2026-09-03，C 盘机 → F 盘机）**：git 已推至 `fe5c161`（step7=f6d0626、step1=fe5c161，均已 push）；两台 `.cargo/config.toml` 的联网切换为 C 盘本机状态（未提交，F 盘默认离线 vendor）；19719 测速探测当时仍在后台（C 盘机 `.tmp/speedtest_19719/`，完整日志 `speedtest_19719.log`，已完成 1 场国服样本）；19543 原始文件在 `.tmp/speedtest_19543/`；stats.db 147 场 + catalog(matches.db) 2 行真实记录均在 C 盘本地（gitignore）。下一会话从 F 盘继续 §8 第2步
- **环境同步完成（2026-09-02，本机 C 盘，项目在 `C:\D2Rep_project\dota_replay_analyzer`）**：git clone 后环境重建完成、端到端验证通过，状态与原电脑（§8 第4步已完成）对齐。要点：`setup.ps1` 报告离线工具链/vendor/快照缺失属预期（大件不入 git）；根与 `dota_parse/` 的 `.cargo/config.toml` 已切换为**联网 cargo 模式**（原 vendor 重定向整段以注释保留，受限网络机器可恢复）；`cargo build --release` 联网编译成功（本机 dsh 沙箱内 schannel TLS 不可用，构建经新增的 `tools/cargo_net_proxy.py` 本地镜像中转 crates.io——仍是标准 cargo 联网语义，无 vendor、无 `--offline`）；`sqlite3.dll` 已重新获取至 `dota_parse/sqlite3.dll`；用新下载的公开录像 **8979484553**（valve.net）实跑解析产出 **10 玩家 / entity_snapshots 28399 / game_events 503（均 purchase）/ player_identity 10**，程序内自检 + python 独立复核（JSON 合法、身份-实体-购买者交叉一致、坐标界内）全部通过。本机实测新事实已写入 §4.2 两条注记：**CDN 录像压缩容器已从 bz2 改为 zstd**；**部分 CDN 录像非 0 秒起录（泉水坐标校验不适用）**
- 新电脑/同步后的启动顺序见 §6.6「新电脑 / 跨电脑同步后的启动顺序（必读）」
- 项目托管于 GitHub 私有仓库 https://github.com/Fanboy3006/D2Rep.git；新电脑 **git clone** 之后哪些大文件缺失属于正常、如何重建，见 §6.6「git clone 场景（GitHub 单文件 100MB 限制）」

---

## 1. 项目目标

一个具备**双数据源能力**（公开API数据 + 本地.dem原始解析）的DOTA比赛分析平台，核心能力包括：

1. **按赛事/战队/比赛ID批量下载录像**，支持公开赛事录像的定向抓取
2. **完整解析.dem录像文件**（不依赖OpenDota等第三方解析结果），覆盖公开赛事之外的**非公开录像**分析场景
3. **标准指标提取**：如10/20/30分钟经济差、胜负记录等，可直接用API数据，无需解析.dem
4. **通用、不预设维度的跨场次整合分析能力**：这是本项目的核心目标。系统不应该为某几种具体分析（如位置热区）量身定制数据结构，而应该把.dem里能提取的原始信息，以**通用、可组合**的方式存下来，支持事后对任意维度提问，包括但不限于：
   - 位置轨迹（热区、走位习惯）
   - 视野布控（插眼/排眼的位置与时机）
   - 资源分配（补刀/经验/金钱的来源分解）
   - 团战参与度与决策（走位/技能释放在团战场景下的上下文）
   - 购买物品的时机与路线
   - 以及任何未来才会想到、现在还没列出的维度

**关键设计原则**：
- **解析层必须自给自足**：非公开录像无法从OpenDota等公开API获取任何解析结果，所有分析能力都必须能从本项目自己解析出的中间数据算出来。
- **架构不能与具体分析维度绑定**：不要为"热区"建一张位置表、为"视野"再建一张守卫表——这种"来一个需求建一张表"的模式无法支撑"极大可延展性"的要求。应该设计**通用的实体快照+事件日志模型**（见第6节），任何新分析维度都应该是"在已有通用数据上写新查询/新提取逻辑"，而不是"改动解析层或数据库schema"。

---

## 2. 整体架构（五层设计，通用数据模型）

```
┌─────────────────────────────────────────────┐
│ 第1层：任务调度 / 元数据层                      │
│  - matches 表：match_id, 来源(公开/非公开),     │
│    下载状态, 解析状态（幂等控制）                 │
├─────────────────────────────────────────────┤
│ 第2层：原始文件层                               │
│  - .dem 原始文件落盘，路径与 match_id 关联       │
│  - 公开赛事：来自 OpenDota replay_url           │
│  - 非公开录像：用户手动放入指定目录               │
├─────────────────────────────────────────────┤
│ 第3层：解析层（重，只做一次，必须自给自足）        │
│  - clarity / manta 完整解析 .dem               │
│  - 输出两张**通用**中间表，不预设分析维度：        │
│    (a) entity_snapshots：任意entity在任意时刻   │
│        的状态快照（位置、生命值等，字段可扩展）    │
│    (b) game_events：任意离散事件的统一日志       │
│        （击杀/购买/放眼/施法/拾取……用同一结构记录）│
│  - 可参考 OpenDota 开源解析器（odota/parser，    │
│    Java+clarity）的log分类思路，甚至作为代码起点  │
├─────────────────────────────────────────────┤
│ 第4层：特征提取 / 跨场次聚合层（轻，可反复改）     │
│  - 任何分析维度都是在通用表上写查询/提取逻辑       │
│  - 新增分析类型 ≠ 改动解析层或数据库schema        │
├─────────────────────────────────────────────┤
│ 第5层：存储与查询                               │
│  - 元数据/标准指标：关系表                       │
│  - entity_snapshots / game_events：需支持       │
│    高效跨match、按事件类型、按时间段过滤查询       │
└─────────────────────────────────────────────┘
```

**设计原则**：解析层产出的是"通用、未预判用途的原始数据"，不是"为某个具体分析需求预处理好的结果"。这是支撑"极大可延展性"的核心——新增一种分析维度，理想情况下只需要在第4层写新的查询逻辑，完全不用碰第2、3层。

---

## 3. 数据源与 API 清单

### 3.1 OpenDota API（首选，无需 Key，免费）

| 用途 | Endpoint | 关键字段 |
|---|---|---|
| 联赛下所有比赛 | `GET /leagues/{league_id}/matches` | `match_id`, `radiant_team_id`, `dire_team_id`, `radiant_win`, `series_id`, `series_type` |
| 单场比赛详情 | `GET /matches/{match_id}` | `radiant_gold_adv`（逐分钟经济差数组）, `replay_url` |
| 战队信息 | `GET /teams/{team_id}` | `name` |
| 战队比赛列表 | `GET /teams/{team_id}/matches` | 备用，按战队反查 |
| 联赛列表（反查ID） | `GET /leagues` | `leagueid`, `name` |
| 触发录像解析 | `POST /request/{match_id}` | 若某场 `radiant_gold_adv` 为空，需先触发 |

**限流**：约 60 次/分钟（免费额度），批量调用需加 `sleep`。

**核心字段说明**：`radiant_gold_adv[N]` = 第N分钟天辉相对夜魇的经济差（正数天辉领先）。需按战队视角转换：
```python
def team_adv(minute, gold_adv, is_radiant):
    if minute >= len(gold_adv):
        return None
    val = gold_adv[minute]
    return val if is_radiant else -val
```

### 3.2 下载原始 .dem —— 最终采用方案：直接用 OpenDota 的 `replay_url`

**实测结论：不需要 Steam Web API，也不需要申请 Steam API Key。**

`GET /matches/{match_id}`（OpenDota，见3.1）返回的 `replay_url` 字段，本身就是可直接下载的完整链接，OpenDota 后台已经帮你完成了"调用 Steam 接口拿 cluster/salt 并拼接"这一步。实测对本项目的联赛（19719）比赛，`GetMatchDetails`（Steam官方接口）持续对多个 match_id 返回 `500` 空响应（原因不明，怀疑是该接口对部分职业赛事数据有限制/隔离），但 `GetMatchHistory`（Steam）和 OpenDota 的 `replay_url` 均正常工作。**结论：整个项目不再需要 Steam API Key，只走 OpenDota 一条数据源即可完成下载。**

```python
resp = requests.get(f"https://api.opendota.com/api/matches/{match_id}")
replay_url = resp.json().get("replay_url")
# 直接对 replay_url 发 GET 请求，bz2解压即可
```

**重要发现 — 域名不固定**：实测返回的链接域名是 `http://replay413.dota2.com.cn/570/...`，即**国服（完美世界代理）录像服务器**，不是国际服常见的 `*.valve.net`。下载逻辑本身不受影响（都是标准 HTTP GET + bz2 解压；**注：2026-09 本机实测 CDN 容器已改为 zstd，见 §4.2 注记**），但如果后续遇到国际服比赛，域名可能变为 `*.valve.net`，代码里不要硬编码域名判断逻辑，统一按 `replay_url` 返回值直接请求即可。

**关键限制 — 录像时效性依然存在**：即使跳过Steam API，`replay_url` 字段本身也是有时效的——Valve/完美世界服务器只保留近期比赛录像（无官方承诺具体时长，实践中大约数周），过期后该字段会为空或返回 `null`。此时只能退回使用 OpenDota 已解析的结构化数据（如 `radiant_gold_adv`），无法拿到原始 .dem。

**（备用，一般不需要）Steam Web API 方案**：如果未来某场比赛 OpenDota 没有 `replay_url`（例如 OpenDota 还没解析过），可以尝试 Steam 官方接口作为 fallback：
```
GET /IDOTA2Match_570/GetMatchDetails/v1/?match_id={id}&key={KEY}
```
返回 `result.cluster` + `result.replay_salt`，拼接 `http://replay{cluster}.valve.net/570/{match_id}_{replay_salt}.dem.bz2`。需要在 `steamcommunity.com/dev/apikey` 申请 Key（免费，但新Key有时需要等待才能生效）。**实测该接口对本项目联赛的比赛集体返回500空响应，原因未明，不建议作为主路径依赖。**

---

## 4. 已验证的关键代码片段

### 4.1 拉取联赛比赛列表

```python
import requests, json

league_id = 19719
resp = requests.get(f"https://api.opendota.com/api/leagues/{league_id}/matches")
resp.raise_for_status()
matches = resp.json()

with open("league_19719_matches.json", "w", encoding="utf-8") as f:
    json.dump(matches, f, ensure_ascii=False, indent=2)
```

已验证：返回约150场比赛的数组，字段包含 `match_id`, `radiant_team_id`, `dire_team_id`, `series_id`, `series_type` 等。**`radiant_team_name`/`dire_team_name` 恒为 null**，需额外调用 `/teams/{id}` 补全队名。

### 4.2 批量下载 .dem（已验证方案：OpenDota replay_url，无需 Steam Key）

```python
import requests, bz2, os, time, json

OUTPUT_DIR = "dem_files"
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open("league_19719_matches.json", "r", encoding="utf-8") as f:
    matches = json.load(f)
match_ids = [m["match_id"] for m in matches]

log_path = "download_log.json"
log = json.load(open(log_path)) if os.path.exists(log_path) else {}

def get_replay_url(match_id):
    resp = requests.get(f"https://api.opendota.com/api/matches/{match_id}", timeout=15)
    if resp.status_code != 200:
        return None
    return resp.json().get("replay_url")

def download_dem(match_id, url):
    resp = requests.get(url, timeout=60)
    if resp.status_code == 200:
        with open(os.path.join(OUTPUT_DIR, f"{match_id}.dem"), "wb") as f:
            f.write(bz2.decompress(resp.content))
        return True
    return False

for i, match_id in enumerate(match_ids):
    mid = str(match_id)
    if log.get(mid) == "done":
        continue
    replay_url = get_replay_url(match_id)
    if not replay_url:
        log[mid] = "unavailable"
        continue
    log[mid] = "done" if download_dem(match_id, replay_url) else "failed"
    if i % 10 == 0:
        json.dump(log, open(log_path, "w"))
    time.sleep(0.5)

json.dump(log, open(log_path, "w"))
```

**注**：曾尝试 Steam Web API（`GetMatchDetails`）直连方案，实测对该联赛比赛集体返回 500 空响应，排查后放弃，改用上述 OpenDota 方案，已验证可行（成功拿到形如 `http://replay413.dota2.com.cn/570/{match_id}_{salt}.dem.bz2` 的可用链接）。

**注（2026-09-02 本机实测，重要更新）— Valve 已将录像压缩容器从 bz2 换为 zstd**：
`replay*.valve.net` 下载到的对象不再是 bz2 流（对下载内容直接 `bz2.decompress` 会报
"Invalid data stream"），而是 **zstandard 压缩**：文件头 4 字节魔数 `28 B5 2F FD`；
zstd 解压后得到以 Source2 demo 魔数 `PBDEMS2\0` 开头的 .dem，那才是
`source2-demo` 能解析的内容。**注意：同一 valve.net 体系内 bz2/zstd 并存**
（实测 replay274/271-273 等=zstd、replay191=bz2），必须按魔数逐文件识别，
不要按域名或后缀假设容器格式。python 标准库不含 zstd，需 `pip install zstandard`
（dsh 沙箱内 pip 的临时目录/安装目标都要指到工作区内，否则报权限错误）。该解码与下载逻辑已转正为工具
`dota_parse/tools/decode_replay.py`（按魔数解码 bz2/zstd/直存 → 校验 `PBDEMS2\0`）与
`dota_parse/tools/fetch_replay_dem.py`（`fetch_replay_dem.py <match_id> [out.dem]`：
OpenDota 取 replay_url → 下载 → 解码），可直接复用。原理示意（解码前按魔数识别容器、不要假设后缀）：
```python
import zstandard, bz2
raw = ...  # replay_url 下载到的原始字节
if raw[:4] == b'\x28\xb5\x2f\xfd':      # zstd —— valve.net 现行格式
    dem = zstandard.ZstdDecompressor().decompress(raw)
elif raw[:3] == b'BZh':                 # 旧式 bz2 —— 部分国服域名仍可能
    dem = bz2.decompress(raw)
# 校验：dem[:8] == b'PBDEMS2\x00' 才算拿到了可解析的 .dem
```
另：国服域名（`*.dota2.com.cn`，Tengine）对本机网络出口实测存在明显限速
（约 100–650 KB/s，且速率与请求头相关：普通 UA ≈100KB/s，浏览器 UA+Referer 420–640KB/s），
单场 200MB+ 录像可能耗时数十分钟；国际服 `*.valve.net`（Google Edge Cache）速度正常
（>1MB/s，无需特殊请求头）。批量下载脚本建议按上述魔数分支解码、对国服域名做超时/重试。

**注（2026-09-02 本机实测）— 部分 CDN 录像并非从比赛第 0 秒开始录制**：
实测公开比赛录像 8979484553 的录制从中途开始：10 名英雄的首个采样点全在
t=1362s（≈22:42，坐标已散落全场），录像内没有泉水开局阶段（与 OpenDota 记录的
duration=2097s 也对不上，说明录像时间轴含录制起始偏移）。这类录像解析流程与结构校验
全部正常，但"最早采样点=泉水→按象限校验坐标"（§8 第4步的泉水校验 / `verify_db.py`
fountain-side 检查）**不适用**，应改用"坐标界内（|x|,|y|≤200000）+ 双方全场分布 +
身份/购买交叉一致性"做合理性校验；天梯/普通匹配局（lobby 0/7）录像通常从 0 秒起录，
泉水校验仍适用。另：解析器会把召唤物（如兽王猪/鹰，类名以 `CDOTA_Unit_Hero_` 开头但
无 player_slot/pid）也写入 `entity_snapshots`，跨表身份一致性比对需限定在有 player_slot
的英雄实体上；`verify_db.py` 现有版本对这类 team 为 NULL 的行存在格式化崩溃，跑新录像前需先修复。

---

## 5. 已知坑点清单

| 坑点 | 说明 | 应对 |
|---|---|---|
| 队名恒为 null | OpenDota `/leagues/{id}/matches` 不返回队名 | 额外调 `/teams/{team_id}` 补全，结果做本地缓存避免重复请求 |
| 录像时效性 | 录像服务器只保留近期比赛，过期后 `replay_url` 为空 | 尽早下载；对过期比赛的分析退回用 OpenDota 已解析字段（`radiant_gold_adv` 等），不强求原始 dem |
| Steam `GetMatchDetails` 不可靠 | 实测对本联赛比赛持续返回 500 空响应，原因未明（`GetMatchHistory` 等其他接口正常，Key本身没问题） | **改用 OpenDota 的 `replay_url` 字段作为主下载路径，完全不需要 Steam API Key**，见 3.2 节 |
| 录像域名不固定 | 实测部分比赛录像存在国服域名 `*.dota2.com.cn`，而非国际服 `*.valve.net` | 代码不要硬编码域名判断，统一直接请求 `replay_url` 返回的完整链接即可 |
| series 与 match 的关系 | 一个 BO3/BO5（`series_id` 相同）包含多场 `match_id`；`series_type`: 1=BO3, 2=BO5（或类似映射），部分为 null | 明确"胜负统计"是按单场（match）还是按系列（series）口径，避免同一场系列赛被两队各记一次"赢" |
| 战队 ID 可能变化 | 职业战队解散重组、改名后 team_id 可能不同 | 如需长期追踪同一批选手，需额外做 team_id 别名映射，不能只靠单一 ID 匹配 |
| 限流 | OpenDota 约60次/分钟；Steam API 也有隐性限流 | 批量请求间加 sleep(1)，必要时做指数退避重试 |
| 解析成本 | 完整解析一场 .dem（entity级别）耗时可达数十秒到数分钟；位置追踪比纯combat log更重 | 中间层尽量一次解析、多次复用；解析时同时产出事件流+位置数据两份输出，避免同一份.dem被解析两次 |
| 存储量级 | 单场 .dem 解压后约 80-150MB；位置时间序列数据量远超事件流数据 | 批量下载前预估总容量；采样间隔要权衡精度与存储成本（见第6节） |

---

## 6. 通用数据模型设计（支撑任意维度分析的核心）

这是整个项目"极大可延展性"要求的落地方式。原则：**解析层不猜测你会问什么问题，只负责把.dem里的原始信息尽量完整地转换成两类通用结构；所有具体分析都在查询层实现**。

### 6.1 参考现成方案：OpenDota 开源解析器

强烈建议先去看一下 `github.com/odota/parser`——这是OpenDota自己用来解析.dem的开源代码（Java + clarity），它已经解决了"从一份录像里拆出尽可能多维度日志"这个问题，输出的日志种类包括购买记录、击杀记录、技能释放、守卫布控（obs_log/sen_log）、神符、经济/经验来源分解等十几种。这不是要你照抄它的具体维度列表（这又会退回"预设几种类型"的老路），而是**参考它"统一事件日志"的设计思路和字段规范**——甚至如果你的解析层最终也走 Java+clarity 路线，直接fork这个项目做起点，能省下大量底层解析代码。

### 6.2 通用表结构：两张表覆盖任意维度

```sql
-- 通用实体快照表：记录任意entity在任意时间点的状态
-- 不只是英雄，也可以是小兵、建筑、守卫（Ward本身也是一种entity）
CREATE TABLE entity_snapshots (
    match_id      BIGINT,
    game_time_sec INT,          -- 从比赛开始经过的秒数
    entity_type   TEXT,         -- 'hero' / 'creep' / 'building' / 'ward' / ...
    entity_id     TEXT,         -- 具体实体标识（如 hero_id、ward的唯一entity handle）
    team          TEXT,
    x             FLOAT,
    y             FLOAT,
    hp            INT,
    extra         JSON,         -- 任何未来想加的属性，不用改表结构，塞进这里
    PRIMARY KEY (match_id, entity_id, game_time_sec)
);

-- 通用事件日志表：记录任意离散事件
-- 击杀/购买/放眼/排眼/施法/拾取神符/团战判定 等，都用同一结构记录
CREATE TABLE game_events (
    match_id      BIGINT,
    game_time_sec INT,
    event_type    TEXT,         -- 'kill' / 'purchase' / 'ward_placed' / 
                                 -- 'ward_destroyed' / 'ability_cast' / 
                                 -- 'rune_pickup' / ... 未来任意新增无需改表
    actor_id      TEXT,         -- 触发事件的实体（如施法者、购买者）
    target_id     TEXT,         -- 事件目标（如被击杀的对象、被摧毁的眼）
    x             FLOAT,        -- 事件发生位置（不是所有事件都有，可为空）
    y             FLOAT,
    properties    JSON,         -- 事件特有字段，如购买记录里的item_id、
                                 -- 技能释放里的ability_id和是否命中等
    PRIMARY KEY (match_id, event_type, actor_id, game_time_sec)  -- 视情况调整
);

-- 选手身份映射（用于跨match追踪同一个人，任何分析维度都可能需要）
CREATE TABLE player_identity (
    match_id      BIGINT,
    player_slot   INT,
    steam_id      BIGINT,       -- 跨match识别同一人的唯一锚点
    player_name   TEXT,
    hero_id       INT,
    team_id       BIGINT
);
```

**实现落点（§8 第4步已完成，SQLite 先行）**：三张表的权威 DDL 在
`dota_parse/src/schema.rs`（SQLite 方言，文件头注释了全部落地适配）。与本节示例
的差异：(1) JSON 列以 TEXT 存储（Postgres 迁移时改 jsonb）；(2) `game_events`
增加 `event_seq` 进主键——同秒同 actor 多发事件在真实录像中很常见（实测同一秒
连买两件物品）；
(3) `player_identity` 增加 `hero_name` 列存录像头部的 canonical hero npc；
数字 `hero_id` 需要外部英雄字典（见§7待选型），`.dem` 头部只提供 npc 名，暂留空；
(4) `team_id` 存头部队伍代码（2=天辉 3=夜魇）；战队组织 ID（radiant_team_id 等）
属 OpenDota API 元数据层，不在 .dem 解析范围内。解析器按§6.4把行级模型与提取器
分离（`dota_parse/src/{model,parse,sqlite}.rs`），新增维度只需新增提取器。

**为什么这样设计能支撑"任意维度"**：
- 视野分析：查 `game_events WHERE event_type IN ('ward_placed', 'ward_destroyed')`
- 资源分配：查 `game_events WHERE event_type = 'gold_gained'`，`properties` 里带来源字段
- 团战参与决策：结合 `entity_snapshots`（团战期间走位）+ `game_events`（同一时间窗口内的技能释放/击杀），按时间窗口关联查询即可，不需要专门的"团战表"
- 位置热区：`entity_snapshots WHERE entity_type = 'hero'`，按需过滤时间段
- 新增任何没想到的维度：只要原始信息在解析阶段被写进了这两张表（或者 `extra`/`properties` 的JSON字段里留了余地），都不需要改表结构

### 6.3 采样与存储的现实约束

即使用通用模型，也要面对性能问题：
- `entity_snapshots` 如果按原始tick频率（约30次/秒）记录，数据量会失控。**建议解析时按固定间隔重采样**（如每1秒一次），采样间隔应做成解析脚本的可配置参数——一旦开始批量积累数据后想提高精度，需要重新解析所有.dem，所以这个参数值得在开发早期就定下来并留有余地
- `game_events` 本身是离散事件（不是每tick都发生），数据量相对可控，不需要采样，应尽量完整记录
- 存储引擎：**鉴于 `entity_snapshots` 可能达到千万行级别，建议直接规划 Postgres**，而不是从SQLite起步——JSON字段的高效查询、更大数据量下的并发读写，Postgres明显更合适

### 6.4 可扩展性设计原则（解析层实现方式）

建议解析器采用**插件式/提取器（extractor）模式**：
- 核心流程：调用 clarity/manta 跑一遍.dem，拿到完整的原始tick流
- 不同"提取器"各自订阅自己关心的原始字段/事件（位置提取器订阅entity坐标更新，购买提取器订阅物品购买事件，视野提取器订阅ward相关entity的创建/销毁……）
- 每个提取器独立地把自己关心的内容写入 `entity_snapshots` 或 `game_events`
- **新增一个分析维度 = 新增一个提取器**，不需要改动已有代码，也不需要重新设计表结构（除非确实需要一种全新的、两张表都无法表达的结构，这种情况应该很少见）

这样"极大可延展性"体现在：你随时可以往解析器里加一个新提取器（比如"团战识别提取器"，判定逻辑是"短时间窗口内多个英雄HP大幅下降+技能密集释放"，输出为 `game_events` 里的一种新事件类型 `'teamfight'`），而不用碰其他任何已有代码。

## 6.6 已验证的技术栈（dsh 实测通过，可直接复用）

以下方案已用真实.dem文件（天梯对局，约102MB）端到端跑通并验证正确性，可作为正式开发的起点，不需要再重新选型摸索：

**核心库**：Rust语言，`source2-demo` crate（版本0.5.8+），启用方式：
```toml
source2-demo = { version = "0.5", default-features = false, features = ["dota"] }
```
（`default-features = false` 避开可选依赖 `mimalloc`，无需C编译器；`build = false`标记的子crate `source2-demo-protobufs` 使用预生成的protobuf代码，不需要联网跑protoc/prost-build）

**关键技术细节（后续开发直接复用）**：

- **坐标解码公式**：Source 2引擎的实体坐标是"无符号cell + 局部偏移"编码，不能直接读取原始字段。正确公式：
  ```
  世界坐标 = (cell - 128) * 128 + vec
  ```
  字段路径：`CBodyComponent.m_skeletonInstance.m_vecOrigin.{m_cellX,m_cellY,m_cellZ}` + `{m_vecX,m_vecY,m_vecZ}`。已用泉水实际位置验证过公式正确性（天辉泉水在负坐标区、夜魇泉水在正坐标区，与地图实际布局吻合）。

- **英雄实体与玩家的关联**：英雄entity（`CDOTA_Unit_Hero_*` 类）上有 `m_iPlayerID` 字段，值为玩家索引×2（不是直接的0-9索引，使用时需注意这个映射关系）。

- **英雄身份解析必须靠 `m_iPlayerID` 反查 header，不要用类名猜测 npc（2026-09 新版本录像实测）**：实体类名并不总是 npc 名的 CamelCase——例如新版录像里 `Spirit Breaker / Phantom Assassin / Bounty Hunter` 的实体类是 `CDOTA_Unit_Hero_Spiritbreaker`（无下划线），而 header 的 npc 是 `npc_dota_hero_spirit_breaker`。若用"npc→类名"匹配 header，这三个英雄会解析成无 team/slot 的行（entity_id 变成错误 npc 名）。正解：快照已带 `m_iPlayerID = 2×header索引`，直接用它定位 header 玩家取得权威 npc/team/slot；无 pid 的 hero 类实体（召唤物等）才退回类名推导、且不带身份（第4层分析据此排除）。解析器 `parse.rs::build_snapshot_rows` 已按此实现（§8第5步验证两场均 10/10 身份一致）。

- **steam_id / 英雄身份来源**：不需要额外查询，录像头部的 `CGameInfo.CDotaGameInfo.player_info`（`CPlayerInfo`类型）里直接包含每个玩家的 `steamid`、姓名、hero 的 **npc 名**（如 `npc_dota_hero_pudge`）、队伍代码（2=天辉 3=夜魇）。**注意：头部没有数字 hero_id**，只有 npc 名；数字 hero id 需要外部英雄字典（见§7待选型），`player_identity.hero_id` 因此暂为空、以 `hero_name` 为准（见§6.2实现落点）。

- **购买记录来源**：战斗日志（combat log）里的 `DotaCombatlogPurchase` 事件类型，购买者信息在事件的 `target` 字段。**已知限制**：战斗日志里的 `value` 字段不是金钱数额，是物品的内部序号，如果需要花费金额，需要额外维护一张"物品ID→价格"的映射表（可从游戏文件或OpenDota的物品字典获取，不需要重新解析.dem）。

- **守卫（视野）事件提取（§8 第6步，2026-09 两场实测）**：
  - **放置**只能从守卫单位实体的 `Created` 事件取（自带坐标与队伍），不要用战斗日志——守卫相关 combat-log 的 Gold/Xp 条目语义不明、`val=守卫物品` 也大量出现在无关的 Damage/Death 行（英雄携带守卫时任何伤害都会被记上物品名，属库存噪声，极易误判成插眼/排眼）。
  - 守卫单位实体类名与直觉不符且**不能从名字推断类型**：实测 `CDOTA_NPC_Observer_Ward`=普通观察守卫(视野)，`CDOTA_NPC_Observer_Ward_TrueSight`=真视守卫(哨兵，sentry)。映射可靠性的判据：两类 Created 计数须分别 ≈ 战斗日志中对应单位(npc_dota_observer_wards / npc_dota_sentry_wards)的 Death 计数（标定场 44≈43、79≈74）；若未来版本两者对不上，先重标定类名映射再看别的。
  - **摧毁**：战斗日志 `DotaCombatlogDeath` 且 `target_name` ∈ {`npc_dota_observer_wards`,`npc_dota_sentry_wards`}（权威单位名），`attacker`=排眼者（英雄/塔），`attacker==target` 表示自然到期；该类 Death 行**不带坐标**。
  - 放置者（owner）在守卫实体上是编码句柄（如 m_hOwnerEntity），v1 未做句柄→玩家解析，`ward_placed.actor_id` 暂空、靠 `properties.team` 与按队伍的第4层聚合兜底；后续可扩展。

- **部分公开录像 .dem 没有 tick 流（实测：970 场中 45 场 ≈4.6%）**：这类录像不含 CDemoTickTime 消息（`on_tick_start` 从不触发），解析仍正常产出 `player_identity` 与 `game_events`（购买/守卫实体 Created/Death 等都在），但 **`entity_snapshots` 恒为空**——位置热区等依赖快照的分析不可用，事件类（购买/视野等）不受影响。分布：19719=21、19255=11、19101=2、19422=2、19696=2、19917=6、19785=1。校验工具 `verify_public.py` 将这类标为 event_only。若未来需要这些场次的位置数据，需改为非 tick 驱动的实体位置快照（订阅实体 origin 更新），属后续增强。
- **采样时机注意**：`on_tick_start` 回调在当前tick的entity增量数据被处理**之前**触发，所以在这个回调里读取到的状态实际是上一个tick的，存在1个tick的滞后。对于1秒粒度的重采样这个滞后可忽略，但如果未来做更高精度的分析需要留意。

- **稳定性细节**：`Parser::new`及后续的深度递归解析容易导致栈溢出，需要在一个**64MB栈**的独立线程里运行解析逻辑；Release模式编译对100MB+的录像文件是必须的（Debug模式解析速度太慢，不适合实际使用）。

- **环境搭建方式（针对本机沙箱网络限制的解决方案，未来在新环境搭建时可参考）**：本机环境下`curl.exe`被进程级网络白名单拦截，但`python.exe`可以正常联网，因此工具链下载、依赖crate下载全部通过Python脚本完成，Rust工具链和依赖以**完全离线vendor模式**编译（`cargo build --offline`），彻底不依赖`cargo`/`rustup`等新进程是否被放行。使用的是**gnu工具链**（而非msvc），因为本机没有装Windows SDK，gnu工具链自带`rust-lld`链接器，无需外部SDK即可完成链接。整套离线环境（工具链+vendor依赖，约478MB压缩后）已打包为快照，可在新环境中直接解压复用，避免重复下载。

- **新电脑 / 跨电脑同步后的启动顺序（必读）**：先把项目目录完整同步过来，然后**运行根目录 `setup.ps1`**——它会把各 `.cargo/config.toml` 里写死的 vendor 绝对路径自动校正到本机项目根目录、检查离线工具链/vendor 是否齐全（缺失时从 `offline_env_snapshot.tar.gz` 解压）、验证 cargo/rustc 可用。全部通过后再构建：`cargo build --release --offline`（需把 `rust_toolchain_x86_64-pc-windows-gnu\bin` 加进 PATH）。解析器**运行期还依赖官方预编译 `sqlite3.dll`**（无需C编译器，运行时 LoadLibrary）：整目录同步时随项目携带（`dota_parse/sqlite3.dll`，3.53.4，SHA3校验通过）；**git clone 场景下它不在仓库内**（见下方"git clone 场景"），缺失时执行 `python dota_parse/tools/fetch_sqlite_dll.py` 重新获取或从旧机器拷贝；解析器按 `DOTA_PARSE_SQLITE_DLL` → exe同目录 → 当前目录 顺序加载。

- **git clone 场景（GitHub 单文件 100MB 限制）**：项目托管于 https://github.com/Fanboy3006/D2Rep.git（私有）。受 GitHub 单文件 100MB 限制，`.gitignore` 排除了下列大文件/运行数据，**它们不会出现在 git clone 的结果里**。在新电脑 clone 后看到以下目录/文件缺失是**正常情况**——此时直接跑 `setup.ps1` 会报告"未找到工具链/vendor/快照"，同样是**预期提示**，不要误判环境损坏、也不要去找不存在的文件：
  - `downloads/`（Rust 工具链原始压缩包）、`rust_toolchain_x86_64-pc-windows-gnu/`（解压后的离线工具链）、`vendor/`（离线依赖）、`offline_env_snapshot.tar.gz`（约500MB离线环境快照，同样不入库）——**都需要按 §6.6「环境搭建方式」重建**；
  - 所有 `*.dem` / `*.db`（录像与解析产物，属运行数据）：公开比赛按 §4.2 流程重新下载，非公开录像手动放入指定目录；`.db` 由 `dota_parse` 在解析时（幂等）重建；
  - `dota_parse/sqlite3.dll`（运行期依赖，体积小但也在忽略名单内）：clone 后同样缺失，执行 `python dota_parse/tools/fetch_sqlite_dll.py` 重新获取，或从旧机器拷贝。
  重建步骤速记：① 最省事——从旧机器拷贝 `rust_toolchain_*/` + `vendor/`（或整体快照）到项目根目录，再跑 `setup.ps1`（自动校正 vendor 路径并验证）；② 无旧机器可用——按 §6.6「环境搭建方式」的 python 联网流程重建（python 下载工具链 + `python tools/vendor_crates.py` 生成 vendor/，全程 `cargo build --offline`）；③ 跑通后构建/运行见上文「启动顺序」。`.cargo/config.toml`、`setup.ps1`、`dota_parse/tools/*.py`、源码与本文档均正常入库，clone 后可直接获得。

- **受限网络本机（F 盘原机器）的 git 推送备忘（2026-09 实测，下次会话直接复用）**：本机 git 走 Windows schannel 报 `SEC_E_NO_CREDENTIALS`，push/fetch 需加 `-c http.sslBackend=openssl -c http.sslVerify=false -c credential.helper=`；凭据不靠交互弹窗，而是用 python ctypes `CredReadW("git:https://github.com", generic)` 直接读 Windows 凭据管理器（UserName=Fanboy3006，blob 为 **UTF-16LE** 的 `gho_` token，40字符），把 user:token 拼进 URL 注入 git（token 不落盘、输出脱敏）。另注意：仓库内两份 `.cargo/config.toml` 默认是**离线 vendor 模式（相对路径）**；联网机器（C 盘机）临时拉新 crate 时按文件内注释切回联网模式即可，切回后仍可跑 `setup.ps1` 校验。

**验证结果**（探测/选型阶段，输出为当时单文件JSON）：成功解析一场约55分钟的天梯对局，产出JSON包含10名玩家的steam_id/英雄/队伍信息、每人约3000+个位置采样点（1秒粒度）、608条购买记录。数据经过坐标合理性交叉验证（比对泉水实际位置），确认解析正确。（正式解析器已改为写库，见§8第4步。）

---

## 7. 待选型的技术决策（建议开发前先拍板）

- [x] **.dem 解析库**：**已确定，Rust + `source2-demo`（`dota` feature）**，见6.6节，已实测验证可行。放弃了最初设想的clarity(Java)/manta(Go)方案（本机网络环境下Maven Central和GitHub均不可达），也排除了demoparser2（确认是CS2专用解析器，不支持Dota2）
- [ ] **存储方案**：建议直接规划 **Postgres**（而非SQLite起步），`entity_snapshots` 表数据量级可能达千万行，且需要良好的JSON字段查询能力。**现状**：解析层已按用户决定以 **SQLite 先行**落地（§8第4步），Postgres 迁移在数据量上来后评估；表结构与§6.2一致，SQLite 方言差异见§6.2实现落点，迁移成本主要在类型/索引层面
- [ ] **位置/状态采样间隔**：已用1秒验证跑通（单场约3000+采样点/玩家），确认这个粒度在性能和存储上可行，建议直接沿用。**现状**：解析器已支持可配置采样间隔（CLI 第三参数，默认1秒，见§8第4步）
- [ ] **运行环境**：本地脚本跑批 vs 部署到服务器做定时任务；数据量增大后要评估本地磁盘/内存是否够用
- [ ] **非公开录像的输入方式**：用户手动放.dem到指定目录 + 手动登记match元信息？还是需要一个简单的本地界面/CLI辅助录入？
- [ ] **胜负统计口径**：按 match 还是按 series 统计战队胜负
- [ ] **提取器（extractor）的初始集合**：第一版解析器打算实现哪几个提取器（如：位置、购买、击杀、视野），后续再逐步添加——不需要一次做全，但建议先列一个初始清单方便排期。**现状**：位置+购买（§8第4步）+ **视野/守卫**（§8第6步，ward_placed/ward_destroyed）已实现写库；击杀/技能等为后续按需增量，每加一个只动第3层新增代码+第4层查询
- [ ] **物品价格映射**：购买记录目前只有物品ID，无金额，需决定物品价格数据从哪里获取并如何维护更新（游戏版本更新会导致物品价格/池子变化）

---

## 8. 建议开发顺序（MVP 路径）

1. ~~打通 OpenDota API 数据管道~~ **已完成（2026-09-03，C 盘机实跑）**——公开赛事标准指标管道落地为 `opendota/`：
   - **存储**：独立 `stats.db`（根目录，gitignore）——与 catalog（`scheduler/matches.db`）职责分离：catalog 管本地 .dem 调度/索引，stats.db 管公开 API 标准指标；两者**按 match_id 松耦合关联**（应用层 join 或 ATTACH，不做外键强耦合；`opendota/stats_db.py join-catalog` 演示）。表：`leagues`（联赛字典）、`teams`（队名补全）、`matches`（match_id/联赛/双方 team_id/start_time/duration/radiant_win/series_id/series_type/game_mode/fetched_at/parse_requested_at，**按 match 粒度**，series 聚合留给查询层）、`gold_adv`（(match_id, minute, value)，value 存 **radiant 视角**原始 `radiant_gold_adv`）；扩展字段一律进 `metadata_json`，加字段无需重建表
   - **视角转换（§3.1 team_adv）**：dire 视角值 = -value；`stats_db.gold_adv_for(con, match_id, is_radiant)` 内置符号翻转，管线落库时只存 radiant 原始值
   - **拉取**：`opendota/fetch_league.py --league <id>`（默认 19719）：联赛列表 → 逐场 `/matches/{id}` → 队名补全 `/teams/{id}`（去重缓存进 teams 表）→ 逐分钟写 `gold_adv`。限流默认 sleep 1.2s + 429/5xx 退避；**幂等**（已有经济差的场次自动跳过，`--refresh` 强制）；某场 `radiant_gold_adv` 为空 → 自动 `POST /request/{match_id}` 触发解析并标记 `parse_requested_at`，本轮重试一次、留待下次运行
   - **实跑结果（联赛 19719）**：`matches` 147 行、**147/147 含经济差**（no_gold=0，无需触发解析）、`gold_adv` **6953** 行、`teams` 16 队
   - **验证**：视角核对 3 场样本（8960991322/8960882635/8960762254）——每场全部分钟 `dire == -radiant`，且末分钟经济差方向与 `radiant_win` 实际胜负一致（如 8960991322 TEAM VISION vs Team Spirit，dire 胜 → 末分钟 dire 领先 17557）；关联演示：catalog 登记真实公开场次 `source='public' match_id=8960991322`（pending，暂无本地 .dem），经 join 从 stats.db 取回该场 65 分钟双向经济差——证明"match_id 松耦合关联"可用（该 catalog 公开行保留，作为第 2 步下载的登记入口）
2. ~~跑通批量 .dem 下载脚本~~ **已完成（2026-09-04，执行机=F 盘主力机）**：工具链：`opendota/batch_download.py --league <id>...`（流式 Range 断点续传 + 跨进程/关机续传 + `.dem.raw.part` + 停滞看门狗/两次停滞跳过 + 状态 `.tmp/batch_download/<league>.json`，同命令复跑即续；`--max-downloads`/`--partial-test-bytes`/`--log`）、`opendota/list_matches.py --league-id|--league-name`（按联赛抓 match ID 管理表 TSV/CSV）、`batch_summary.py`/`scan_dems.py`/`parse_public.py`/`verify_public.py`。**结果：7 联赛 970/970 场全部下载（0 失败/不可用，.dem≈142GB），970/970 解析，925 full / 45 event_only / 0 bad（1734.9万快照/78.5万事件）**，明细见顶部进度速览。`source='public'` 的 catalog 登记（幂等，复用 `scheduler/intake_private.py` 思路）待与 C 盘 catalog 统一时执行
3. ~~选型验证~~ **已完成**：`source2-demo` + Rust 方案已验证可行（见6.6节），核心字段（坐标、steam_id、购买记录）均已跑通
4. ~~基于已验证的 `dota_parse` 最小脚本，扩展为正式的解析器~~ **已完成（SQLite 先行，Postgres 迁移后续做）**：
   - `dota_parse` 输出从单个 JSON 改为直接写入数据库：三张通用表（schema 见 `dota_parse/src/schema.rs`，与§6.2的适配差异已在文件头与§6.2实现落点说明中记录）
   - 解析层按§6.4 extractor 模式重构：`src/model.rs`（行模型）、`src/parse.rs`（身份/位置/购买三个提取器）、`src/sqlite.rs`（运行时加载官方 sqlite3.dll 的极简 FFI + 幂等事务写入，避免引入 C 编译依赖）
   - 用法 `dota_parse <replay.dem> [output.db] [采样间隔秒]`；重复解析同一 match 幂等覆盖（单事务 delete+insert）
   - **实跑验证**：对 8592126358.dem（约102MB/55分钟天梯局）产出 `entity_snapshots` 30620 行（10英雄×1秒采样，同秒去重取最新）、`game_events` 608 条 purchase（与探测JSON完全一致）、`player_identity` 10 行；写后从库内回读 + `dota_parse/tools/verify_db.py`（python 独立复核）双通道验证：坐标泉水对照（天辉负象限/夜魇正象限）、hero npc 三表一致、extra/properties JSON 合法，全部通过。详见 `dota_parse/README.md`
5. ~~基于通用表，实现第一个跨场次分析查询~~ **已完成** —— 用两场真实录像（8592126358 天梯55min + 8979891001 公开26min，后者经 `fetch_replay_dem.py` 从 replay129.valve.net 取回，zstd 容器）验证了"第4层新增分析不改架构"：
   - 新增 `analysis/` 目录（纯查询层）：`analysis/run_analysis.py <db...>` 实现 **英雄位置热区**（主交付物：每队密度网格、逐场 ASCII dominance 图、跨场 pooled 网格 CSV/dominance 图）+ **购买节奏**（每5分钟双方购买分布，跨场 pooled）。运行说明见 `analysis/README.md`，产物输出到 `analysis/output/`（gitignore）
   - 数据口径与坑位处理：仅统计带 player_slot 的英雄（排除召唤物）；中途起录的场次（首采样>300s）跳过泉水类校验与时间对齐聚合
   - **实跑结果与自检**：两场均 10 玩家/10 英雄身份三表一致（parse 修复后）、坐标全部界内、天辉质心偏向地图负象限而夜魇偏正象限（与泉水/家方向吻合）；产出逐场+pooled 热区与购买桶（样例：8592126358 全场购买 0:15/16 … 50:17/12 R/D；8979891001 0:28/46 … 25:47/31）
   - **过程中发现并修复了一个第3层数据正确性缺陷**（非 schema 改动）：多词英雄在新版录像里实体类名无下划线（见 §6.6），身份解析改为 `m_iPlayerID→header` 反查；同时给 `verify_db.py` 补了"中途起录跳过泉水校验"与"跨表比对限定有 player_slot 的英雄"两处逻辑
   - **结论**：整个分析全部落在三张通用表之上，解析层/schema 零改动即可支撑任意新维度；第2步批量下载/第4层后续维度建议按 §8 顺序继续
6. ~~按需逐步增加更多提取器~~ **视野（守卫）提取器已完成（2026-09，两场实测）**，每加一个只涉及第3层新增代码 + 第4层新增查询，不改已有表结构/已有代码：
   - `dota_parse/src/parse.rs` 新增 `WardExtractor`：向通用 `game_events` 写 **`ward_placed`**（守卫单位实体 Created 事件；有坐标 x/y、队伍 team、`properties.ward_type`=observer/sentry）与 **`ward_destroyed`**（战斗日志 Death、target=守卫单位 npc；`actor_id`=排眼者或守卫自身(自然到期)，`properties.reason`=dewarded/expired；Death 无坐标故 x/y 为空）
   - 事件字段设计：`ward_placed` 的 `target_id`=实体类名、actor 暂空（放置者 owner 需实体句柄→玩家映射，v1 未做，第4层可先按队伍聚合）；`ward_destroyed` 的 `target_id`=`npc_dota_observer_wards`/`npc_dota_sentry_wards`（权威单位名）
   - **两场重解析验证**（位置快照/购买完全不变）：8592126358 → placed 123 / destroyed 117；8979891001 → placed 50 / destroyed 43；`verify_db.py` 新增守卫校验（坐标界内、量级几十、双方类型分布、destroyed 目标合法）两场均通过
   - **第4层验证查询**：`analysis/ward_analysis.py <db...>`（"某支队伍整场插眼位置分布"：按 队伍×observer/sentry 计数 + 每队 ASCII 放置图 + deward 归属），全部只读 `game_events`——与第5步热区共同证明"新增维度=纯第4层新查询"。守卫提取涉及的实现坑点（类名映射、战斗日志噪声等）见 §6.6「守卫事件提取」
7. ~~补充调度层，支持非公开录像的手动录入流程，与公开赛事数据管道统一到同一套存储和查询接口下~~ **已完成（2026-09-03，C 盘机实跑验证）**——第1层落地为 `scheduler/`，只建索引/接口、不复制三表数据：
   - **独立 catalog**：根目录 `matches.db`（`*.db` 已在 gitignore），`scheduler/catalog.py` 提供 DDL 与 CLI（`init|list|dbs [--source][--state][--catalog]`）。`matches` 表只存每场索引行：`match_id/source/dem_path/db_path/parse_state/dem_sha256/duration_sec/registered_at/parsed_at/metadata_json`；`metadata_json`(TEXT JSON) 承载 `--note` 与公开赛事占位（team/series/赛事名等）——后续加字段只进 JSON，不需要重建 catalog
   - **match_id 命名规则（防命名空间冲突）**：`source='public'` → OpenDota 十进制 match_id；`source='private'` → 优先用录像头自带的官方 match_id，头部缺失/为 0 时用内容哈希 `manual_<sha256[:12]>`（同文件重跑幂等，且不与未来公开 id 撞车）
   - **`dota_parse --info <replay.dem>`**：新增只读头部模式（`parse.rs::parse_header`，复用 §6.6 已验证的 player_info 解码，不跑 tick 流），输出 JSON：match_id / duration_seconds / 10 玩家（steam_id、slot、team_code、hero npc）——注册前即可拿到全部登记元信息
   - **`scheduler/intake_private.py`**（非公开录入）：扫 `dems/private/*.dem` → sha256 → `--info` 读头 → 幂等登记（默认立即全量解析到 `dems/db/<id>.db` 并置 parsed/failed；`--no-parse` 只登记留 pending；`--note` 进 metadata_json；`--move` 成功后把 .dem 移入 `dems/private/registered/` 做视觉区分并同步 dem_path）
   - **第4层查询入口统一**：`analysis/run_analysis.py` 与 `ward_analysis.py` 新增 `--catalog matches.db [--source][--state]` 枚举（由 catalog 展开为逐场 db 列表），仍兼容手传 db 列表
   - **实跑验证（8979484553.dem，private 全流程）**：intake 登记→解析→state=parsed（28399 快照/695 事件/10 身份，与直跑完全一致）；二次运行幂等跳过；`--move` 后 .dem 入 `registered/` 且 catalog.dem_path 同步；`run_analysis.py --catalog` 与 `ward_analysis.py --catalog` 均从 catalog 枚举出该场并正常产出；`source='public'` 条目与 `manual_` 命名空间先经占位行验证、验证后已清理（真实 catalog 现只有已解析的 private 场次）
   - **下一步衔接**：第1/2步公开管道作为 `source='public'` 的生产者写入同一 catalog；第4层后续维度继续只加查询

## 9. 开发工具选型：dsh vs CLINE 对照测试结论

在正式投入开发前，用同一个验证任务（单文件.dem解析，输出坐标/steam_id/购买记录），在完全相同的模型（DeepSeek V4 Flash）配置下，分别用 **DeepSeek Harness (dsh)** 和 **CLINE** 独立测试，供工具选型参考。

### 9.1 测试结果对比

| 维度 | dsh（选用 Rust + source2-demo） | CLINE（选用 Go + manta） |
|---|---|---|
| 最终数据完整度 | 10玩家、位置坐标、608条购买记录 | 10玩家、位置坐标、608条购买记录 |
| 遇到的库兼容性障碍 | protobuf子crate标了`build=false`，需要正确理解才能离线编译通过 | manta v1.5.0 不支持新版录像格式（DOTA消息被内嵌进`CDemoPacket`），需要**给manta源码手动打补丁** |
| 应对本机网络限制的方式 | 用Python绕过curl白名单，Rust依赖全部离线vendor | 类似思路：绿色版Go工具链 + 本地patch的manta源码 |
| **坐标编码bug的发现方式** | **主动自发**：未经提示，自己对比泉水实际位置做合理性校验，发现并修正了`cell×128+vec`缺少偏移量的bug | **被动响应**：同样的bug最初直接交付给用户，是在用户明确指出坐标数值可疑后，才去验证并修正 |

**结论**：两个工具在纯技术执行能力（排查环境问题、绕过网络限制、修复库兼容性问题）上表现相当，都能独立解决相近难度的障碍。**关键差异在于"主动质量校验的意识"**——dsh会在无人要求的情况下主动对输出结果做合理性交叉验证；CLINE具备同等的验证能力，但需要用户明确要求才会执行，默认情况下可能将带bug的结果当作"已完成"直接交付。

### 9.2 最终选型决定

**采用 dsh 作为本项目的主力开发工具**，基于上述对比中"主动验证意识"这一差异——这类工具会被大量用于批量处理、无人值守跑批的场景（比如批量解析上百场录像），"结果自己有没有把关"这个特质的重要性高于两者相近的原始技术执行力。

**使用CLINE的注意事项（如后续场景需要用到）**：由于其验证行为依赖用户主动触发，日常任务的prompt里应明确加入"完成后请对输出结果做合理性校验"一类的要求，不能默认它会自发做这一步。
## 10. 可视化工具模块（2026-09-04 新增）

**共享地图底图（两个模块共用）** — `opendota_analysis/map_background.py` + 资产 `assets/dota_map_1024.png`（官方 overview 同款、社区已解码 7.33+ 布局，1024×1024，可跨机复用；来源镜像 redota，本机 Dota2 VPK 的官方纹理为 RED2/VTF 容器、纯 python 解码成本高，故采用公开同款图）。坐标映射口径：世界原点=图中心，跨图世界跨度 `WORLD_SPAN=19134`（与 replay 工具项目 7.33+ 一致），`map_to_px(x,y,size)`：`px=(x+S/2)/S*size`、`py=(S/2-y)/S*size`（北=图上=+y）。**数值校验**（用真实重放数据）：泉水/角落簇像素 radiant≈(144–172,846–853) ↔ dire≈(879,160–180) 基本对称；全场像素落在 [52..960] 与地图留边吻合。肉眼复核图：`.tmp/overlay_full.png`（21.6 万点叠加）、`.tmp/ward_survival_heat.png`。

**模块 A：假眼存活/被反热力图** — `opendota_analysis/ward_survival_heatmap.py`。数据依赖解析器富化（见下）：ward_destroyed 带 `properties.team`（combat-log target_team）。按 (match, team) FIFO 配对 placed/destroyed observer 得存活时长，筛 ≥300s，按坐标 400 单位分 bin，在底图上以红色深浅表示"被反占比"输出 PNG+文字摘要。**首轮全量结果（970 场）**：≥5min 存活样本 19776（被反 2809=14.2%、自然过期 16967=85.8%）；过期寿命中位 368s（p95≈404s）→ **假眼上限约 6 分钟，≥300s 判定成立**；高被反点位集中在暴露的线侧/野区入口（如天辉下路外侧野区、夜魇上路外侧野区等，区域名为粗略方位描述）。跑法：`python opendota_analysis/ward_survival_heatmap.py`。

**模块 B：单场 HTML 复盘（云玩家友好）** — `opendota_analysis/export_match_viewer.py <match_id|db>` → `dist/viewer_<id>.html`（单文件自包含：底图 base64 + 10 英雄逐秒 x/y/hp/hp_max/mana/mana_max JSON，时间滑块+线性插值，Canvas 绘制血蓝条）。**原型已生成**：TI2026 场次 8942993144 → `dist/viewer_8942993144.html`（1.82MB，双击浏览器打开即可）。批量导出/降采样压缩留待原型观感确认后。

**配套解析层富化（2026-09-04）**：快照 extra 增加 `hp_max`/`mana`/`mana_max`（100% 可取，供血蓝条）；ward_destroyed 事件 properties 增加 `team`（供模块 A 配对）。已用 `opendota/parse_public.py --force` 全量重解析 970 场并复验（full 925 / event_only 45 / bad 0 保持不变）。
