# STRATEGY / 战略与架构

> 本目录是 DOTA 分析项目的**多会话协作宪法**。它定义四层分工、各层边界、交接协议，保证每个会话**上下文纯净**。
> **与代码/数据区隔**：代码在 `opendota/`、`opendota_analysis/`、`dota_parse/`；数据产物在 `analysis/`；本目录只放**架构与战略文档**。

## 快速开始（每个会话开场必读）

1. 读 `00_AGENT_MAP.md` —— 四层结构 + 使用原则。
2. 读自己那层的文档：
   | 你要开的会话 | 读这个 |
   |---|---|
   | 地图复现 | `01_MAP_REPLAY.md` |
   | 数据分析 | `02_DATA_ANALYSIS.md` + `06_QUESTION_BANK.md` |
   | 逻辑（问题/意义） | `03_LOGIC.md` + `06_QUESTION_BANK.md` |
   | 总控/对外 | `04_ORCHESTRATOR.md` |
   | 任何跨层协作 | `05_HANDOFF.md` |
3. 跨层需求 → 填 `05_HANDOFF.md` 模板，写到 `06_QUESTION_BANK.md` 对应条目。

## 文档一览

| 文件 | 内容 |
|---|---|
| `00_AGENT_MAP.md` | 总纲：四层结构、原则、如何使用 |
| `01_MAP_REPLAY.md` | ① 地图层：边界/产物/待修项（野点错位 BUG）/大版本重做 |
| `02_DATA_ANALYSIS.md` | ② 数据层：边界/交付规范/交接/已知产物 |
| `03_LOGIC.md` | ③ 逻辑层：什么值得问/方法论/自身坦诚定位 |
| `04_ORCHESTRATOR.md` | ④ 总控：优先级/对外交流/发帖准备 |
| `05_HANDOFF.md` | 交接模板：跨层协作用 |
| `06_QUESTION_BANK.md` | 问题银行：Q1-Q4 已提名问题 |

## 维护约定
- 各层文档由该层会话维护（只改自己那份），避免越权污染。
- 问题银行 `06` 只追加不删除；状态用标记流转。
- 若架构需改 → 改 `00_AGENT_MAP.md` 并在各层同步。
