# ZCode Token 用量状态栏

给 [ZCode](https://zcode.ai) 桌面客户端（Electron 应用）加上一个悬浮的 token 用量状态条。数据源是本地 SQLite（`~/.zcode/cli/db/db.sqlite` 的 `model_usage` / `turn_usage` / `tool_usage` 表，每次模型请求一行），**全程只读、不联网**。

平台：Windows ｜ 许可：[MIT](LICENSE)

> 文档与界面统一使用中文名「ZCode Token 用量状态栏」；仓库与 MCP 注册名为 `zcode-token-usage-statusbar`（全称的英文写法），代码内部标识符为 `zusage`。

## 为什么是注入而不是插件？

ZCode 的插件机制（`plugin.json`）只能提供 MCP / skills / commands / hooks，**没有客户端 UI 能力**；状态条要画进主窗口，唯一路线是往 `app.asar` 主入口注入一行 loader。MCP 查询部分则是标准的插件级能力，一键安装会自动注册。

## 功能

- **输入框下方状态条**（实时）：token/s（最近一次请求的生成速度）、上下文（微进度条+百分比，随水位变色、超限亮红）、本轮（token / 缓存命中率 / 次数 / 轮耗时 / 首字）、会话累计、工具调用（含错误数）、今日合计、代码变更统计、子代理消耗（当前会话，运行中带 ● 指示），⚙ 面板可开关各显示项与上下文窗口 override。
- **悬停明细**（自绘 tooltip，向上弹出）：token 项显示 输入/输出/缓存命中/缓存写入/思考；工具项显示调用明细；子代理项悬停只显示汇总并提示可点击（v50）——点击弹出固定明细面板（不随鼠标，与设置面板同机制），面板内页签切换 汇总+各子代理（v49/v50），每个子代理显示派发任务名+消耗明细（v52/v53）。
- **上下文窗口自动识别**：优先读 ZCode 原生 UI（输入框工具行按钮文本"…总量 N"，服务端下发、自动跟随模型）→ 模型目录查表 → `config.json` 兜底。
- **会话跟随**：读 localStorage 的会话键，每个对话窗口只显示它自己的数据；快照池外的会话也能显示。
- **MCP 对话内查询**：`token_usage(scope)` 工具，scope 支持 current / today / week / days:N / sessions:N / models:days / session:<id前缀>。
- **CLI**：`python zusage.py [now|today|json|days N|sessions [N]|models [days]|watch [秒]]`。
- **/usage 命令**：对话输入框触发 MCP 查询。

## 文件说明

| 文件 | 作用 |
|---|---|
| `install.py` | **一键安装**：探测 ZCode 安装位置 → 复制运行时到数据目录 → 生成 config → 注入 asar → 注册 MCP → 装 /usage 命令 → 弹监控窗口。`--remove` 卸载，`--dry-run` 预览，`--no-mcp` 只装状态条，`--dev` 开发模式（不复制，注入直指本仓库目录）。 |
| `overlay.js` | 状态条本体（渲染进程注入，自包含 IIFE）。fixed 悬浮在**输入框视觉卡片正下方**（给卡片加 margin-bottom 上移让位），rAF 每帧跟随。勿把条放进输入框中心点所在矩形——命中检测自遮挡会造成周期性闪烁（v12-v14 实测）。 |
| `inject-main.cjs` | 主进程 loader：向每个窗口注入 overlay.js + **触发式监听 db 写入**（fs.watch db 目录，有写入→去抖 300ms→查询推送；空闲零进程零轮询，仅 30s 兜底心跳）。 |
| `patch_install.py` | asar 注入/卸载工具（install.py 的底层，可单独用）：备份 → 在入口 `out/main/index.js` 尾部追加 dynamic import 行 → 重打包 → 自检 → 原子替换。幂等，自动替换旧注入行（迁移友好）。安装成功自动弹出监控窗口。 |
| `install_monitor.py` | 安装监控窗口（常驻命令行，每 10 秒检测一次）：持续提醒重启 ZCode；重启后通过 diag 更新确认注入加载，显示成功并自动退出。运行中原子替换失败（生成 .tmp）时，它还会在 ZCode 退出后自动完成替换——**收尾不依赖计划任务**。 |
| `zusage.py` | 查询库 + CLI + 机器可读快照（`json` 子命令，状态条数据源）。 |
| `usage_mcp.py` | 零依赖 stdio MCP server。 |
| `config.example.json` | 运行配置模板（安装时生成到数据目录的 `config.json`；含本机路径，不入库）。 |

**数据目录 `~/.zcode/zcode-token-usage-statusbar/`** 是标准安装的运行目录：运行时副本（`inject-main.cjs`/`overlay.js`/`zusage.py`/`usage_mcp.py`）、`config.json`、诊断产物 `diag-<n>.json`（每窗口一份，主进程定期回写）都在这里。本仓库只是源码，**clone 目录可以随意搬走或删除，不影响已安装的实例**。

## 安装

前提：Windows；Python 3.8+（零第三方依赖）；fuses `EmbeddedAsarIntegrityValidation=0`（ZCode 当前版本实测为 0）。

```bash
git clone https://github.com/xhwxt/zcode-token-usage-statusbar.git
cd zcode-token-usage-statusbar
python install.py
```

一条命令完成：探测 ZCode 安装位置（找不到时询问，或 `--asar` 指定）→ **复制运行时到数据目录 `~/.zcode/zcode-token-usage-statusbar/`** → 迁移/生成 config.json → 注入 asar（注入行指向数据目录副本）→ 注册 MCP（server 名 `zcode-token-usage-statusbar`，指向数据目录副本）→ 安装 /usage 命令 → 弹出「安装监控」窗口（每 10 秒检测一次，提醒重启 ZCode；重启后检测到注入加载即显示成功并自动关闭；万一运行中原子替换失败，监控窗口会在你退出 ZCode 后自动完成替换）。

**ZCode 升级会覆盖 app.asar，重跑一次 `python install.py` 即可**（监控窗口提示"未检测到注入加载"通常就是这个原因）。

<details>
<summary>手动分步安装（等价于 install.py 做的事）</summary>

```bash
# 0) 数据目录：复制运行时 + 配置（改 python_path 为你的 python.exe 绝对路径）
mkdir -p ~/.zcode/zcode-token-usage-statusbar
cp inject-main.cjs overlay.js zusage.py usage_mcp.py ~/.zcode/zcode-token-usage-statusbar/
cp config.example.json ~/.zcode/zcode-token-usage-statusbar/config.json   # 然后编辑它

# 1) 状态条：注入 asar（注入行须指向数据目录的 inject-main.cjs；ZCode 运行中也可执行；
#    ZCode 不在 D:\ZCode 时先改脚本顶部 ASAR，或直接用 install.py）
python patch_install.py install

# 2) MCP：在 ~/.zcode/cli/config.json 的 mcp.servers 注册（指向数据目录副本）：
#   "zcode-token-usage-statusbar": { "command": "<python 绝对路径>", "args": ["<home>/.zcode/zcode-token-usage-statusbar/usage_mcp.py"] }

# 3) /usage 命令：把 usage.command.md 复制到 ~/.zcode/commands/usage.md
```

</details>

## 更新规则（重要）

改的是**仓库源码**，生效分两步：`git pull` 后重跑 `python install.py` 同步到数据目录副本，再按下表生效（注入行不变时 asar 不重打包，重跑是秒级）：

| 改了什么 | 生效方式 |
|---|---|
| `overlay.js` | 重跑 install.py 同步副本后，`hot_reload` 开着时 ~2 秒热更新，免重启（loader 检测副本 mtime，`new Function` 语法校验通过才注入，防载入写一半的文件）；`hot_reload: false` 关闭后改完需重启 |
| `zusage.py` | 重跑 install.py 同步副本后，下次轮询即生效（每次轮询都是新进程） |
| `config.json`（数据目录） | 下次拉取时生效（拉取由 db 写入触发；完全空闲时最迟一个兜底心跳，默认 30s） |
| `inject-main.cjs` 本身 / 首次安装 / 标准↔dev 互切 | 重跑 `python install.py` 后**需重启 ZCode**（注入行含绝对路径，脚本自动替换旧行） |

## 配置（config.json）

位置：标准安装在数据目录 `~/.zcode/zcode-token-usage-statusbar/config.json`（`--dev` 安装在仓库目录）。

```json
{
  "python_path": "python.exe 绝对路径",
  "activity_min_ms": 1500,  // 有 db 写入时的最小拉取间隔（活动期限频，到点自动补拉最后一笔）
  "heartbeat_ms": 30000,    // 兜底心跳：防 fs.watch 文件事件丢失；空闲时拉取只由此触发
  "hot_reload": true,       // overlay.js 热更新开关（调试用）；false=改完需重启 ZCode
  "context_window": 1000000 // 上下文兜底值（原生读数/模型目录都失败时用）
}
```

## 数据口径

- `input_tokens` 已含 cache_read；轮数一律用 `count(distinct turn_id)`（`turn_usage` 表覆盖不全）。
- 缓存命中率 = cache_read ÷ input_tokens（input 已含 cache_read）。缓存写入是本次请求新写进供应商缓存的量（计费高于普通输入），缓存命中是直接复用已有缓存前缀的量。
- token/s = 最近一次请求的 output_tokens ÷ 生成耗时（completed_at − first_token_at）。db 只在请求完成时落库，故该值每完成一次请求更新一次，不是流式过程中的实时速率。
- 上下文水位 = 最近一次 completed 请求的 input_tokens ÷ 上下文窗口（会话压缩后自然回落，不显示峰值）。
- "当前会话"识别：读 localStorage 的 `zcode-v4-last-session:v1:<工作区路径>` 键（ZCode 切换会话即更新，实测跟踪可靠），值 = 会话 id，与快照 `recent`（最近 12 个活跃会话）求交集；多工作区候选时优先取刚切换的、否则取最近活跃的；无交集回退"最近活跃会话"。**顶栏的会话标题不在可扫描的 DOM 文本节点里，标题扫描方案实测永远失败，勿回退。**
- "当前会话" = 最近 30 分钟内有请求的 session。
- 子代理消耗 = 当前会话通过 `session.parent_id` 关联的全部子代理（`query_source='subagent'`）累计 token；● 表示 30 秒内有子代理请求（运行中）。
- 子代理任务名（v52/v53）= 派发时 Agent 工具调用的 `description` 参数（与 ZCode 右侧"子智能体目录"面板同源，存 db `part` 表 Agent part 的 `state.input.description`）。与消耗记录的关联：主用**官方回执**——part 完成后 `state.output` 尾部 `agentId: agent_xxx` 行 → 子会话 id `sess_subagent_<agentId>`（客户端硬关联，零歧义）；兜底仅给运行中未出回执的条目：prompt 前缀匹配（子会话 title=prompt 前 57 字+"..."）且候选唯一才绑；配不上的显示线路名，宁无名不错名。
- 与 ZCode 自带"设置→用量"互补：自带走供应商云端接口（套餐额度/剩余），本工具走本地 db（会话排行/上下文容量/对话内查询）。

## 诊断与排查

状态条异常时先看数据目录里的 `diag-<n>.json`（`~/.zcode/zcode-token-usage-statusbar/`；`--dev` 安装在仓库目录。每窗口一份，只有成功挂载过的窗口才写，防止后台空窗口污染）。注意：拉取由 db 写入触发，**完全空闲时 diag 不更新属正常**（无变化可记录）；但客户端**启动后第一次拉取**必写（监控窗口据此确认注入生效）。

- `fatal` / `trackErr`：脚本异常与堆栈。
- `mount`：挂载诊断——`composerRect`/`anchorRect`/`barRect`（坐标）、`barHit`（**条中心点的命中元素**：条显示不出来但坐标正常时，看这里被谁盖住）、`nativeCtx`（原生窗口读数）。
- `tipStats`：自绘 tooltip 的 mousemove 计数与 show/hide 计数、最后一次显示/隐藏内容——悬停异常时先看这里。
- `shallow`：各输入框选择器的命中数；`shadowRoots`/`iframes`：DOM 深度环境。

常见症状速查：

| 症状 | 历史根因 |
|---|---|
| 条不显示，但 diag 坐标全对 | z 序问题：条贴在输入框下方，z-index 需压过相关容器的 Tailwind `z-20`（现为 50，实测不压权限菜单） |
| 条显示时输入框下方有空白 | 样式表进了 head 被 React 清掉 → position:fixed 失效退化为 static 占位。样式表必须挂在条元素内部 |
| 条周期性闪烁（约 0.4s 一次） | 条几何上盖住了可见性判定用的"输入框中心点"→ 命中检测自遮挡。条必须在输入框中心点所在矩形之外（v15 起外挂于卡片上沿） |
| 悬停 tooltip 每次数据推送就闪 | 推送无条件重建 DOM + 重画 tooltip → 内容比对（lastHtml）+ 签名幂等（tipSig）+ 鼠标在条上时隐藏总闸（v33.x） |
| diag 停在旧快照、永远没有 mount 字段 | v14 及以前的 collectDiag 对象引用分裂（v15 起原地更新，见上） |
| 设置页/覆盖层里条仍出现 | 覆盖层不卸载聊天 DOM 也不改几何，须用 elementFromPoint 命中检测判断被盖 |
| 热更新后条消失/打架 | 旧实例 interval 把删掉的条插回。代际守卫（`__zusageGen`）解决 |

## 踩坑记录（改这些代码前必读）

- **asar 注入行必须用 dynamic import**：入口是 ESM（package.json `type:module`），`require` 会 ReferenceError 被静默吞掉。loader 必须是 `.cjs`（在 type:module 包里仍按 CJS 加载）。
- **客户端运行中 asar 大多数情况可原子替换**（运行中进程仍读旧句柄），`os.replace` 失败才退化为 `.tmp` 待退出替换——收尾由 `install_monitor.py` 监控窗口自动完成。
- **收尾勿用 ZCode 自己的定时机制（CronCreate）**：调度器就是客户端自身，客户端退出期间不触发 = 死锁。也不要用 schtasks 了——监控窗口更直接，还能顺带确认生效。
- **勿向 React 布局流插 DOM**：会被重渲染清除或卷进滚动流。条是 body 级 fixed 元素，逐帧跟随输入框矩形。
- **可见性判定与条的几何必须互斥**：可见性用"输入框中心点 elementFromPoint"判定，条若悬浮在该点所在矩形内（如内嵌输入框形态），会判输入框被盖 → 400ms 迟滞到期隐藏一帧再显示 = 周期性闪烁。
- **tooltip 必须挂 document.body + document 捕获阶段 mousemove**：挂条内会被消息流层叠上下文遮挡；悬停显示要向上弹（原生 title 方向不可控）。字符串内容要显式 `textContent` 赋值，条目必须有 `.it` 类供索引——二者缺一会得到"空胶囊"。
- **诊断对象只能原地更新**：`window.__zusageDiag` 一旦被整体替换，其它闭包持有的旧引用（FATAL）就写进孤儿对象，主进程泵永远取不到新诊断。
- **rAF 自调度循环必须 try/catch + finally 续帧**：与 setInterval 不同，回调抛一次异常循环就静默死掉。
- **Git Bash (MSYS) 会把 `/FI` 等开关转成 Windows 路径**：tasklist/schtasks 一律用 Python subprocess + list 参数 + `decode('gbk','replace')` 调用。
- **多窗口**：ZCode 有多个窗口（含后台小窗），loader 对所有窗口注入；诊断只允许成功挂载的窗口写（everMounted 门）。
- **WAL 模式**：db 写入主要落在 `db.sqlite-wal`，判断"有新数据"要同时 stat 三件套的 mtime。
- **子代理任务名勿用时间配对（v52 错配实锤）**：`session.title` 只是首条输入前 57 字截断、不是派发名；part 与子会话的创建时间差虽实测仅 20~230ms，但 error（取消/限额）派发的子会话可能已建+有消耗却永无回执，且多个任务书前 57 字常是公共开头——时间最近邻会把失败派发的名安给来源不明的子会话。必须用回执 `agentId:` 行硬关联，多候选宁无名。

## 已知限制

- 仅 Windows（tasklist 检测、`CREATE_NEW_CONSOLE`、asar 路径均平台相关）。
- ZCode 安装位置自动探测常见目录，非标准位置用 `python install.py --asar <路径>` 指定。
- 依赖 fuses `EmbeddedAsarIntegrityValidation=0`。官方一旦收紧此 fuse 或改入口结构，注入路线即失效（届时 `python install.py --remove` 恢复原版）。
- 修改客户端 asar 属非官方注入方式，ZCode 升级会覆盖，需重跑 install。
- 上下文超限亮红依赖 db 中 `context_exceeded` 标记行；触发条件是请求真被服务端拒绝，无法本地模拟测试。

## 卸载

```bash
python install.py --remove    # 恢复原版 asar（需先退出 ZCode）+ 移除 MCP 注册与 /usage 命令 + 删除数据目录
# 或只卸状态条：python patch_install.py remove
```

## 目录迁移

标准安装下 clone 目录只是源码：**删掉或搬走都不影响已安装的实例**（注入行与 MCP 注册指向数据目录 `~/.zcode/zcode-token-usage-statusbar/`，运行时全套基于 `__dirname`/`__file__` 自定位）。换机器/换目录重新 clone 后重跑 `python install.py` 即可，会自动替换 asar 里的旧注入行（无需先 remove）。开发者改代码想即时生效用 `python install.py --dev`（注入直指仓库目录，改文件热更新即达；切回标准形态重跑不带 `--dev` 的 install 即可）。

## License

[MIT](LICENSE)
