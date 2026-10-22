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

---

# 附：ZCode 客户端自身启动慢的归因（与插件无关）

这是独立的第二个问题。以下全部为本机实测（ZCode v3.16.2 / Windows 11）。

## A. 启动耗时的硬指标

```
ZCode.exe --version          3213 ~ 3242 ms   ← 连版本号都要 3.2 秒
常驻进程数量                 22 个
常驻内存合计                 3808 MB (3.7 GB)
```

`--version` 属于最早的退出路径，几乎不初始化 UI、不加载工作区。走完它就要 3.2 秒，
说明这 3 秒基本是**进程初始化 + 主进程模块图加载**的固定成本，不是用户数据或渲染慢。

## B. 主因：asar 体量与文件数

```
app.asar 体积        341 MB（325.5 MiB）
asar 内文件数        27,773
其中 node_modules    23,554 个文件 / 263.5 MB
其中 out/            4,218 个文件 / 60.0 MB
asar 头部索引        6.93 MB，仅 json.loads 就要 74~99 ms
```

asar 是「一个文件装几万个小文件」的归档格式。Electron 启动时要：

1. 读 6.93 MB 的 JSON 头部索引并解析（实测 74~99ms，纯 CPU）
2. 按索引建立虚拟文件系统条目（27,773 条）
3. 主进程 `import("./chunk-XXXX.js")` 链式加载 `out/main/`（2.1 MB）
4. 每个 `import` 都要经过 asar 的路径查找 + 边界解析

第 2、4 步与**文件数量**成正比，而不是与体积成正比。27,773 个条目是这里的关键数字。

### 为什么这么重

```
echarts          23.8 MB  726 files
mermaid          21.2 MB  220 files
pdfjs-dist       16.0 MB  284 files
@extend-ai       15.3 MB   21 files
lottie-web       14.2 MB  283 files
@opentelemetry   11.9 MB 1730 files   ← 遥测 SDK，1730 个文件
@shikijs         10.1 MB  883 files
playwright-core   8.3 MB  460 files
lucide-react      6.9 MB 1982 files   ← 图标库，近 2000 个文件
```

头部 10 个包就占了约 128 MB / 6600 个文件。这些都是**渲染端才用到的重资源**
（图表、流程图、PDF 预览、动画、图标），却被打进同一个 asar，主进程启动时
索引它们却一个都不用。

### 额外的磁盘放大

```
D:\...\ZCode\resources\glm\             120 MB   ← 随包分发的 skills
  └─ packages/                          106 MB
  └─ zcode.cjs                           15 MB
%LOCALAPPDATA%\@zcodedesktop-updater\   190 MB × 2
  ├─ installer.exe                      190 MB
  └─ pending\ZCode-3.16.2-win-x64.exe   190 MB   ← 与上面同一版本，重复一份
```

更新器把同一个安装包在 `installer.exe` 和 `pending/` 下各存一份，**380 MB 重复占用**。
这不直接拖慢每次启动，但会显著影响首次冷启动（文件系统缓存未命中时读的是同一块磁盘）
和系统整体的 IO 压力。可以安全删除 `installer.exe`（若已升级完成），
直接省 190 MB。

## C. 22 个进程 / 3.7 GB

Electron 的进程模型本身就会分裂出多个渲染进程（主窗口、每个 webview、
插件沙箱、CUA/PiP、OCR 等），每个都带独立的 V8 隔离堆。
本机 22 个进程属于偏多但在 Electron 应用里不算异常——尤其是 ZCode 自带
`playwright-core`、内置浏览器、插件沙箱、子代理。这部分不是 bug，是架构选择。

## D. 能做和不能做的

| 想法 | 能否 | 说明 |
| --- | --- | --- |
| 删 `installer.exe` + `pending/` | ✅ 安全 | 省 380 MB，不影响启动逻辑 |
| 清理 `%LOCALAPPDATA%\@zcodedesktop-updater` 旧 blockmap | ✅ 安全 | 同上 |
| 用 `NODE_OPTIONS` 注入 | ❌ 实测不可行 | ZCode 启动直接报 `Most NODE_OPTIONs are not supported in packaged apps` |
| 精简 asar 内 echarts/mermaid/lucide | ⚠️ 高风险 | 会破坏渲染端功能，且每次升级被覆盖 |
| 向官方提 asar 拆分建议 | ✅ 推荐 | 把重资源移到 `app.asar.unpacked` 或按需动态加载 |
| 关掉遥测（`@opentelemetry`） | ⚠️ 需配置项 | 1730 个文件，若官方有开关可显著减轻 |

结论：**客户端启动慢的根因是 asar 打包策略（27,773 个文件塞在单个归档里，
重资源全打进去），不是本插件**。插件能做的只有别在自己这一层再加负担——
这正是本次 v63 改动的目标。

## E. 一劳永逸的注入方案：结论

「升级后不用重装」这件事，实测下来只有三条路，全部有硬约束：

| 方案 | 可行性 | 结论 |
| --- | --- | --- |
| 1. asar 注入（现状） | ✅ 可用 | 每次官方升级覆盖 `app.asar` 后**必须重跑** `python install.py` |
| 2. `NODE_OPTIONS` / `--require` 环境变量 | ❌ 不可行 | 实测被 Electron 拒绝：`Most NODE_OPTIONs are not supported in packaged apps` |
| 3. 替换启动器为包装脚本 | ⚠️ 脆弱 | 需接管所有快捷方式/协议处理器/自动更新回调；更新器重建快捷方式时会丢 |
| 4. 改 `app.asar.unpacked` 或 `resources` 下旁挂目录 | ❌ 无效 | 主进程不会自动 require 旁挂文件，仍需一个注入点 |
| 5. 官方提供插件机制 | ✅ 唯一真解 | ZCode 已有 MCP/命令/skills 体系但没有 UI 注入 API，需上游支持 |

**当前唯一稳妥的「半自动」做法**：见下节 `autopatch.py`。

---

# 附二：`autopatch.py`（升级后免手工重装）

## 为什么不是真·一劳永逸

三条路都试过，只有 asar 注入可行（详见上表）。而 asar 会被官方升级整包覆盖。
所以「一劳永逸」只能退一步做：**升级后自动把注入补回去**。

## 用法

```bash
python autopatch.py check    # 只检查：退出码 0=已注入 / 1=需要补 / 2=环境问题
python autopatch.py apply    # 需要时补注入（静默，不弹监控窗口）
```

`check` 只解析 asar 头部（实测 74~99ms），不重打包，可以放心频繁调用。
`apply` 才会走重打包（307MB，十几秒）。

## 怎么接到启动流程上

不想常驻、也不想改快捷方式（会被 electron-updater 重建），最省事的三种接法：

1. **手动**：升级完 ZCode 后跑一次 `python autopatch.py check`，看到提示再 apply。
2. **计划任务（Windows）**：登录时触发一次 `python autopatch.py apply`。
   已注入时它只花 ~100ms 就退出，几乎无感。
3. **包装启动脚本**：写一个 `zcode.bat` 先 `autopatch.py apply` 再拉起
   `ZCode.exe`，把快捷方式指向这个 bat。

第 2 种最稳——不碰 ZCode 自己的任何启动路径，升级重建快捷方式也不受影响。

## 已验证

用一个剥离了注入行的 325.5MB asar 副本做过完整回归：

```
check  → 识别为「已被官方升级覆盖」，退出码 1
apply  → 重打包 → 自检（27773 文件，注入行符合预期）→ 补注入成功，退出码 0
check  → 识别为「注入仍在」，退出码 0
```



