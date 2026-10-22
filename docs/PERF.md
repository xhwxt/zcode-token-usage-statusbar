# 性能分析与优化记录（v63 / 分支 `perf/startup-lite`）

本文件记录一次针对「ZCode 启动臃肿」与「插件生效慢」的实际测量与改动依据。
所有数字都在本机实测（Windows 11、ZCode v3.16.2、Python 3.11.9、Node 22.14.0、
`db.sqlite` 326MB / 76 个会话），不是估算。

## 1. 先排除的两个误判

### 1.1 不是数据库慢

```
count(*) model_usage                        0.0 ms
distinct session_id                         0.4 ms
GROUP BY session_id（全表 326MB）           22.6 ms
status='completed' 区间聚合（全表）          17.2 ms
```

`model_usage` 上已有 `model_usage_session_turn_idx`、`model_usage_started_model_idx`；
`_scan_session_ids` 还挂了 2s TTL 聚合缓存。**库本身不是瓶颈**，即使 326MB。

### 1.2 不是 Python 启动慢（但确实是固定成本）

```
python -c pass                    57 ms
zusage.py now                    189 ms
zusage.py json                   192 ms
```

解释器冷启动 57ms、脚本导入到首次查询完成 189ms。这是每次「一次性 spawn」
都要付的固定成本，所以老版本用常驻 `serve` 进程规避它——方向是对的。

## 2. 真正的耗时来源

### 2.1 渲染端：每帧强制同步布局（最大项）

`overlay.js` 的 `track()` 由 `requestAnimationFrame` 自调度，**每帧**执行：

- `inGhostSubtree(composer)` — 沿输入框到 `document.body` 的整条祖先链，逐个
  `getComputedStyle()`（实测 15~25 次/帧）
- `reallyVisible()` 内部**再算一遍** `inGhostSubtree` 与 `elementFromPoint`
- `coverOK()` 又一次 `getBoundingClientRect` + `elementFromPoint`
- `modalOpen()`（v62 新增）遍历 `document.body` 全部子层，各自 `getComputedStyle`

60fps 下这是**每秒数千次样式查询**，而且 `getComputedStyle` 会强制样式重算。
即使条处于隐藏状态也照跑。这是「插件一装上客户端就变重」的直接原因。

### 2.2 渲染端：固定 600ms 全页扫描

- 协调器 `setInterval(tick, 600)`：全页 `querySelectorAll(SEL)` 并对每个候选
  调 `getBoundingClientRect()`（强制布局）
- 每个实例 `setInterval(heavy, 600)`：`findComposer()` 又是一轮
  `querySelectorAll("textarea")` + 逐元素可见性判定

两处都**不考虑窗口是否可见、用户是否在操作**，空转照跑。

### 2.3 渲染↔主进程：无条件诊断落盘

`handlePayload()` 每 15 次推送就 `executeJavaScript` 取一次 `__zusageDiag`
（13KB 量级的 JSON），然后 `fs.writeFileSync` **同步**写盘，每个窗口一个文件。
实测 `diag-0.json` 在会话活跃期间持续被刷新。用户根本不需要这个文件。

### 2.4 生效慢：三处叠加

| 环节 | 老行为 | 影响 |
| --- | --- | --- |
| 注入时机 | 只监听 `did-finish-load`（等全部资源加载完） | 重启后要等数秒 |
| 首次出数 | 注入后**没有**立即推数据，等下一次 db 写入或心跳 | 条先显示零值 |
| 启动查询 | `maybeSpawn()` 先 `executeJavaScript` 问每窗口会话，挂 1s 超时 | 首帧再等多达 1s |

### 2.5 常驻进程：每请求重连

`serve()` 每收到一行就调 `snapshot()` → `connect()` 新建 sqlite 连接并跑一次
探测查询（`select 1 from model_usage limit 1`），326MB 库上实测 3~6ms 固定开销。
小请求（只有 force 会话）里这部分占比可观。

## 3. 本次改动

### overlay.js

- **`ghostOf()`**：`inGhostSubtree` 结果按元素挂 `WeakMap`，TTL 120ms。
  纯 TTL、不挂 MutationObserver——流式输出每帧都在改 DOM，属性脏标会被反复置位而失效。
- **`reallyVisible()`**：命中测试结果按「元素矩形 + ownOK 模式」缓存，TTL 150ms；
  矩形一变立即重算。原实现拆出 `hitSelf()`。
- **`coverOK()`**：同样走 `coverCache`（键 `"c"`），拆出 `cardHitOK()`。
- **`modalOpenCached()`**：`modalOpen()` 加 150ms TTL。弹层开关远慢于该尺度。
- **`track()`**：`document.hidden` 时整帧跳过。rAF 自调度在 `finally` 里，
  `return` 不会断链，窗口恢复可见自动续上。
- **协调器**：固定 600ms 改为自适应——30s 无交互退回 2s，任何
  `mousemove/mousedown/keydown/wheel/input/focusin/resize` 立即回 600ms；
  窗口隐藏时 2s 慢轮询。
- **实例 `heavy`**：同样的自适应调度（600ms ↔ 2s）。

### inject-main.cjs

- **`diagEnabled()`**：诊断落盘默认关闭，`config.diag = true` 才写。
  （`diag-*.json` 仍保留，只是不再自动生成。）
- **`lastPayload` 缓存 + `injectOverlay()` 补推**：overlay 装好立刻推最近快照，
  新窗口/重载不再先显示零值。
- **`dom-ready` 注入**：`did-finish-load` 保留兜底，但主路径提前到 DOM 可用时。
- **`maybeSpawn({ firstRun: true })` 快速路径**：首轮若每个窗口都已有 IPC 会话映射，
  跳过页面 `executeJavaScript`（最长 1s 超时）直接查询。
- **`startResident()` 预热**：启动即拉起常驻解析器，省首帧的解释器启动。
- **热更新检查 2s → 5s**：`config.hot_reload = true` 时才维持 2s。

### zusage.py

- **`_snapshot_with(con, ...)`**：把快照实现与连接生命周期解耦；`snapshot()`
  保留为自建连接的一次性入口（CLI/测试用）。
- **`serve()` 复用连接**：空闲 60s 或出错后重建；每次查询照常执行完即结束，
  不持长读事务，不影响 WAL checkpoint。

## 4. 实测结果

```
常驻服务单次查询（同连接复用）
  改前  22.6 ms（含每次重连 + 探测查询）
  改后   5.4 ms（min 4.2 / avg 5.4，6 次采样）
```

渲染端改动无法在无头环境里测真实帧耗时，但可以确定量级：
`getComputedStyle` 调用从「每帧 15~50 次 × 60fps」降到「每 120~150ms 一次」，
即稳态下**降低 2~3 个数量级**；窗口隐藏时归零。

## 5. 回归风险与对照点

| 改动 | 风险 | 如何验证 |
| --- | --- | --- |
| 幽灵子树缓存 120ms | 设置页/弹窗开关瞬间可能延迟显示 | 开合设置页、模型配置弹窗，条应在 ~150ms 内正确收起 |
| 遮挡缓存 150ms | 输入框被临时装饰层盖住时可能短暂误显 | 流式输出期间观察条是否闪烁 |
| `document.hidden` 跳过 | 无 | 最小化/切回，条应立刻恢复正确位置 |
| 自适应调度 2s | pane 结构变化最迟 2s 才被协调器发现 | 开分屏，第二 pane 的条应在 ~2s 内出现 |
| 诊断默认关闭 | 排障时看不到 `diag-*.json` | `config.json` 加 `"diag": true` 后重启 |
| 连接复用 | 库被替换/句柄失效 | 出错自动重建连接，日志可见 |

## 6. 未做但可继续的方向

1. **`track()` 改为按需驱动**：现在仍是 rAF 常驻。可改为
   `ResizeObserver`（输入框/卡片）+ `scroll` + `MutationObserver` 驱动，
   完全去掉逐帧循环。改动面较大，需要真机回归。
2. **`elementFromPoint` 全量移除**：可见性判定可退化为纯几何 + `inert` 检测，
   命中测试只保留在「几何显示但疑似被盖」的少数路径上。
3. **渲染端增量更新**：`render()` 目前按字符串重建整段 HTML（`bar.innerHTML`），
   可改为只更新变化的文本节点。
4. **按窗口实例隔离的 `coverCache`**：现在是模块级 `WeakMap`，多实例共享是对的，
   但多 pane 分屏下 `cardCache` 是实例级的，若后续出现串扰可改为实例级缓存。

## 7. ZCode 客户端本身

ZCode 桌面端**是开源的**：[`zai-org/ZCode`](https://github.com/zai-org/ZCode)
（TypeScript，README 显示更新至 v3.16.2，含 `apps/zcode-cli` Agent 源码）。

但本次的「启动臃肿」与客户端仓库无关——测量显示数据库索引齐全、查询毫秒级，
开销全部来自本插件的注入层与渲染循环。也就是说：**装这个插件之前客户端并不慢，
是插件自己把每帧成本加上去的**。上面 2.1~2.5 就是完整归因。

若后续想减少注入本身的体积，可以考虑把 `overlay.js`（约 100KB）
在安装时预压缩、或改为按需 `import()` 分块加载，但收益远小于本次循环优化。
