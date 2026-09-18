# 多场分析交互式地图（Multi-Match Interactive Map）—— 交互模式规范

> **归属**：本文件是 D2Rep 项目中一个**可复用的交互模式**的完整文档，来源是 Q5B（真眼位分析 viewer）。未来任何"把逐格统计画成地图 + 点格钻取 + 放大锁定单事件"的交互，都应复用这套模式。
> **实现参考**：`analysis/build_q5_html.py`（Q5B 生成器，读取 `q5_ward.py` 产物）。生成单文件自包含 HTML（内嵌 base64 底图 + 数据 JSON），双击即开。
> **核心思想**：**多场比赛的统计聚合到一张地图上，用格（cell）作为统计单元；点格钻取到"该格的逐场明细"，并可放大锁定到单个事件（某次反眼）以核对录像。**

---

## 0. 一句话

一个自包含的 HTML `<canvas>` 交互地图：把 N 场比赛的**逐格聚合统计**（如"每格真眼平均反掉几支假眼"）画成颜色方块；支持 **队伍/时间窗/指标** 三重筛选、**值热力上限** 与 **出现次数下限** 滑块、**滚轮自由缩放 + 拖拽平移**、**点格自动放大钻取**（右侧列表逐场明细）、**点击某次反眼图标锁定该事件**（比赛ID+坐标+双方队名+反眼队伍，供核对录像）。

---

## 1. 数据模型（先于交互，决定一切）

地图上每个**格（cell）**是统计聚合的最小单元；**一场比赛的一支眼**是钻取的最小单位。

- **格坐标**：`cell = floor((world + MAP_HALF) / CELL_SIZE)`。Q5B 用 `CELL_SIZE=172`、`MAP_HALF=8600` → 100×100 格网格（`2*8600/172=100`）。
- **格数据**（每格含多个字段，`IDX` 索引定位）：
  | IDX | 字段 | 含义 |
  |---|---|---|
  | 0 | x | 格中心世界 x |
  | 1 | y | 格中心世界 y |
  | 2 | win | 时间窗(0=0-7,1=7-15,2=15分+) |
  | 3 | team | 队伍码(2天辉/3夜魇) |
  | 4 | n_sen | 该格真眼数 |
  | 5 | ng | 涉及的比赛数 |
  | 6-8 | avg_jy/avg_zy/avg_all | 平均反假眼/反真眼/总反眼(每支真眼平均) |
  | 9-11 | jy_total/zy_total/all_total | 总反假眼/真眼/总反眼数 |
- **钻取明细**（点格后）：`SENG[cell_key]` = 逐场记录列表，每条含 `[match_id, place_clock, destroy_clock, survival, 反假眼数, 被反?, 反真眼数, 队伍码, win, 反假眼时刻[], 反真眼时刻[], 被反假眼位置[], 被反真眼位置[]]`。
- **队伍名映射** `MATCHTEAMS[ match_id ] = {radiant, dire}`（用于钻取面板显示对战双方）。

> **为什么这样分层**：格 = 聚合统计（宏观热力）；格内逐场明细 = 微观可核对；单次反眼 icon = 最细粒度事件（含坐标+队伍）。

---

## 2. 坐标系统（世界 ↔ 画布）

- **世界坐标（world）**：DOTA 地图单位，范围 `MAP_HALF=8600`（±8600）。
- **标定**：地图底图 PNG 与真实地图对齐，用常量 `CALIB_K / CALIB_OFFX / CALIB_REF_Y`：`w2p(x,y) = [OFFX + K*x, REF_Y - K*y]`（y 轴翻转，因为屏幕 y 向下）。
- **画布**：`CSX=1024` 正方形 canvas。
- **视图矩形（viewRect）**：`[wx0,wx1,wy0,wy1]`（世界矩形）。`viewRect=null` = 全图（正常大小）；非空 = 放大状态。
  - `w2pView(x,y)`：全图用 `w2p`；放大时把世界点映射到画布：`[fx*CSX, (1-fy)*CSX]`（fx=(x-wx0)/(wx1-wx0)，fy=(y-wy0)/(wy1-wy0)）。
  - `calibFromPx(px,py)`：画布像素 → 世界（w2pView 的逆），用于鼠标事件定位。

---

## 3. 渲染管线（`render(cap)`）

每次重绘按顺序：
1. 清屏 + 填深色底。
2. **底图**：`bgimg`（base64 PNG），画布上画全图或裁剪 `viewRect` 对应区域；透明度 = `mop` 滑块（`globalAlpha`）。
3. **格方块**：对 `cellsFor()`（见 §5 筛选）每格，算 `w2pView(格中心)`，画一个**颜色方块**：
   - 颜色 = `interp(SEQ, min(1, v/cap))`，`v=valOf(c)=当前指标值`，`cap=值热力上限`。
   - 大小 `sz = max(3, min(CELLW*0.92, 3 + sqrt(n_sen)*2.2 + (viewRect? CELLW*0.3:0)))`——随缩放、随出现次数变。
   - `cellpixel` 是 172 世界单位对应的屏幕边长，随缩放变化。
4. **放大选中格**：`selCX/selCY` 非空时，画格边框 + 外圈高亮圈 + 格坐标文字（`(selCX,selCY)`）。
5. **标注被反眼图标**：仅在选中格（`selCX!=null`）时，渲染该格真眼反掉的每支假眼（绿色 obs icon）/真眼（红色 sen icon）的**准确位置**（`w2pView(反眼位置)`），并重建 `annMarks[]`（供 hover/click 命中检测）。
6. **色标 + 说明**：更新 `#cgrad`(渐变)、`#cmin/#cmax`(0 眼/上限值)、`#curdesc`(队伍·时间窗·指标名·上限)。

> **重绘入口**：`draw()`（带 `_drawing` 重入锁 + try/finally，防并发重绘）。`render(cap)` 是纯绘制。画布缩放/平移/筛选都调 `draw()` 触发重绘。

---

## 4. 筛选控件（3 组 + 2 滑块）

**筛选项决定 `cellsFor()`（画哪些格）与 `bounds()`（如何算色阶上限）：**

| 控件 | 语义 | 实现 |
|---|---|---|
| **队伍 checkbox**（天辉/夜魇） | 只显示勾选队伍 | `teamSel={2:bool,3:bool}`；`setTeam()` 遍历 `.tmchk` |
| **时间窗 toggle**（0-7/7-15/15+） | 只显示该时间窗 | `wsel`；`setWin(w)` |
| **平均指标**（平均反假眼/反真眼/总） | 切换颜色含义 | `metric`；`setMetric(m)` |
| **值热力上限滑块** `#sl` | 色阶上限（各指标自适应） | `onSlide()`→`render(capValue)`；`resetCap()`→自动 |
| **出现次数下限滑块** `#ns` | 只显示 `n_sen>=minNsen` 的格 | `setN()`(rAF 节流)→`draw()` |
| **底图透明度** `#mop` | 底图明暗 | `setMapOp()` |

- `cellsFor()` = `WINC.filter(c => c[win]===wsel && teamSel[c[team]] && c[n_sen]>=minNsen)`。
- `bounds()`：计算当前筛选下所有格指标值 p95 → 自动色阶上限 `cap`（缓存于 `RC[key]`）。`setN/resetCap` 后 `capValue` 变 null → `draw()` 重算。

---

## 5. 鼠标交互（缩放 / 平移 / 点格 / 锁定 / hover）

**五个层级，按优先级**：

| 交互 | 触发 | 行为 |
|---|---|---|
| **滚轮缩放** | `cv.onwheel` | 以鼠标下世界点为中心缩放（上滚×0.8 放大 / 下滚×1.25 缩小），`clampView` 限制范围(≥500、≤全图、不越界)，然后 `draw()` |
| **拖拽平移** | `cv.onmousedown`(左/中键) + `onmousemove`(拖拽中) | 按住左键拖动 → `viewRect` 平移（dx/dy 换算成世界位移），`clampView` + `draw()`；`onmouseup/leave` 收尾。**若点在 annMark 图标附近(距离<16)则不进入拖拽**(留给 click) |
| **点格钻取** | `cv.onclick` | 见下方 4 分支 |
| **hover 高亮** | `cv.onmousemove`(非拖拽) | 找 `annMarks` 里距离<24 的最近标注 → `highlightHover(m)`：高亮右侧对应行 + 显示"对战·反眼队伍"信息箱；无则清空 |
| **点击锁定** | `cv.onclick` | 命中 annMark(距离<20) → `pinnedMark=m` + `showMarkInfo(m)`：锁定显示比赛ID+事件地点+双方队名+被摧毁时间+反眼队伍，直到再次点击地图 |

**`cv.onclick` 的 4 分支逻辑（关键）：**
1. **点击到被反眼图标**（`annMarks` 中距离<20 最近者）→ `pinnedMark=hitMark; showMarkInfo(hitMark); return`（锁定该事件，不进入格子）。
2. **点击其它位置** → 清除 `pinnedMark`（若有），`clearHover()`。
3. **再点同一个已选中格**（`viewRect && selCX===cx && selCY===cy`）→ 取消"独显该格+标注"，保持放大，改回显示其它方块（等价于中键拉近看）。
4. **否则进入该格** → `selCX=cx; selCY=cy; setZoom(cx,cy); renderDrill(key)`（自动放大到该格 + 右侧渲染该格逐场明细）。

> **为什么 `annMarks` 判定要先于格子判定**：因为图标在格内代表"单次反眼事件"，是比"格子"更细的命中对象；点图标应锁定事件而非进入格子。

---

## 6. 放大/钻取

- **进入格子（setZoom）**：`viewRect = [cxm-2200, cxm+2200, cym-2200, cym+2200]`（以格中心放大到 ~4 倍，显示约 1/4 全图宽，保留邻域上下文），然后 `draw()`。放大时只显示该格方块 + 该格被反眼图标标注。
- **恢复正常大小**：`resetZoom()` → `viewRect=null; selCX=selCY=null; draw(); 清空钻取面板`。
- **自由缩放（手动）**：滚轮/拖拽进入自由缩放 = `viewRect` 任意矩形 + `selCX=null`（不进格、不显示标注，显示所有方块），便于对照不同区域。

---

## 7. 钻取面板（右侧，点格后）

- **汇总**（`#drillsum`）：本格 · 格坐标 · 时间窗 · 队伍 · 真眼数；**反眼率** = 反到≥1个敌方假眼的真眼占比；**平均反假眼/反真眼/总**。
- **明细表**（`#drill tbody`，每行一支真眼）：`match_id | 队伍 | 放置 | 销毁 | 存活 | 反假眼 | 反假眼时刻 | 反真眼 | 反真眼时刻 | 被反?`。
  - 时间 `fmt(sec)` 转 `mm:ss`，负值/超720 显 `⚠`（异常标记）。
  - 每行 `data-mid` 供 hover/click 与右侧行高亮联动的匹配。
- **hover/锁定信息箱**（`#hoverinfo`，顶部浮动）：hover 显示"对战·反眼队伍"；点击锁定显示"比赛ID + 事件地点 + 双方队名 + 被摧毁时间 + 反眼队伍"。

---

## 8. 状态机（全局变量）

```
viewRect:    null(全图) | [wx0,wx1,wy0,wy1](放大)      — 缩放/平移/进入格子
selCX/CY:    null(未选格) | (cx,cy)(选中格，放大钻取)   — 点格进入
pinnedMark:  null(未锁定) | mark(锁定的被反眼事件)      — 点击任意图标锁定
annMarks[]:  当前渲染的标注点(重建于每次 render)
hoverMark:   当前 hover 的标注点
wsel:        时间窗(0/1/2)
teamSel:     {2:bool,3:bool} 队伍筛选
metric:      指标名(avg_jy/avg_zy/avg_all/jy_total/...)
minNsen:     出现次数下限
capValue:    值热力上限(可手动；null=自动)
```

**入口**：`setTeam(); setWin(0); setMetric('avg_jy');` 初始化后首绘 `draw()`。

---

## 9. 已知坑与修复（务必继承）

1. **`capValue.toFixed` 崩溃**：`draw()` 里 clamp 用 `SL.max/SL.min`——DOM range 的 `.max/.min` 是**字符串**，`capValue=SL.max` 会把 `capValue` 变成字符串，之后 `.toFixed` 每次 throw → 地图永不刷新。**修复**：clamp 用**数字**运算再回写：
   ```js
   let cv=+capValue; if(isNaN(cv)||cv<=0) cv=b.cap;
   if(cv>+SL.max) cv=+SL.max; if(cv<+SL.min) cv=+SL.min; capValue=cv;
   ```
2. **`px` 作用域**：`onmousemove` 的 `const px/py` 若声明在 `if(drag){...}` 块内、块外(悬停高亮)引用 → `px is not defined`。**修复**：把 `const rect=cv.getBoundingClientRect(); const px=..., py=...` **提到 `if(drag)` 块之外**。
3. **重绘重入**：`draw()` 用 `_drawing` 锁 + try/finally，防并发重绘导致卡顿。
4. **滑块抖动/死循环**：`setN`(rAF 节流)、`refreshNS` 不强制回写 `minNsen`(只防越界)。
5. **点格 vs 拖拽冲突**：`mousedown` 先判断是否点在 annMark(距离<16)附近，是则交给 click 不进入拖拽。
6. **数据越大越慢**：块方块仅遍历 `cellsFor()`(筛选后)，不是全量；钻取明细只在点格后渲染该格。

---

## 10. 生成方式（可复用）

- **数据源**：`q5_ward.py` 产物 → `q5_sen_win_172.json`(格聚合) + `q5_sentry_detail_172.csv`(逐场明细，用于 `SENG` 分组) + `q5_matches.json`(队伍名)。
- **生成器**：`build_q5_html.py` 用 `@@PLACEHOLDER@@` 把数据 JSON + base64 底图/图标嵌入 HTML 模板，输出单文件 `q5b_ward_viewer.html`(自包含，双击即开)。
- **部署**：拷到 `publish_repo/`(GitHub Pages) → `git add/commit/push` → `https://bigfatblackwhale.github.io/DSH-Dota2/<file>.html`。

---

## 11. 复用到新场景的最小改动

只需替换：① `DAT`(格数据) 与 `IDX`(字段索引)；② `SENG`(钻取明细)；③ `MAPIMG`/`CALIB_*`(底图+标定)；④ 颜色语义/指标名(§4 字典)。**交互框架(缩放/平移/点格/锁定/hover/筛选/钻取)完全复用，不重写。**
