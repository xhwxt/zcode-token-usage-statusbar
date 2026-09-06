# 设计笔记（design notes）

> 面向维护者与想改代码的人：各数字的统计口径、性能实测、诊断排查手段与踩坑记录。用户向的功能说明见 [README](../README.md)。

## 数据口径

- `input_tokens` 已含 cache_read；轮数一律用 `count(distinct turn_id)`（`turn_usage` 表覆盖不全）。
- 缓存命中率 = cache_read ÷ input_tokens（input 已含 cache_read）。缓存写入是本次请求新写进供应商缓存的量（计费高于普通输入），缓存命中是直接复用已有缓存前缀的量。
- token/s = 最近一次请求的 output_tokens ÷ 生成耗时（completed_at − first_token_at）。db 只在请求完成时落库，故该值每完成一次请求更新一次，不是流式过程中的实时速率。
- 上下文容量 = 最近一次 completed 请求的 input_tokens ÷ 上下文窗口（会话压缩后自然回落，不显示峰值）。
- "当前会话"识别（状态条）：主链路是**泵按窗口注入 mine**——渲染端经客户端自带 IPC 通道 `zcode:sync-active-task-session` 上报本窗口焦点会话 id，主进程推送时按窗口带上；overlay 只显示 mine 对应的池条目，池里没有（新会话无数据）就显示零值，**绝不回退到别的会话**（多窗口共享 localStorage 时旧启发式必错，实测）。兜底（辅助窗口/泵无该窗口信息）：读 localStorage 的 `zcode-v4-last-session:v1:<工作区路径>` 键（ZCode 切换会话即更新），值 = 会话 id，与快照 `recent`（12 条）求交集；多工作区候选时优先取刚切换的、否则取最近活跃的。**顶栏的会话标题不在可扫描的 DOM 文本节点里，标题扫描方案实测永远失败，勿回退。**
- "当前会话"（CLI / MCP `scope=current`）= 最近 30 分钟内有请求的 session（`current_session`，状态条不用此口径）。
- 子代理消耗 = 当前会话通过 `session.parent_id` 关联的全部子代理（`query_source='subagent'`）累计 token；● 表示 30 秒内有子代理请求（运行中）。
- 子代理任务名 = 派发时 Agent 工具调用的 `description` 参数（与 ZCode 右侧"子智能体目录"面板同源，存 db `part` 表 Agent part 的 `state.input.description`）。与消耗记录的关联：主用**官方回执**——part 完成后 `state.output` 尾部 `agentId: agent_xxx` 行 → 子会话 id `sess_subagent_<agentId>`（客户端硬关联，零歧义）；兜底仅给运行中未出回执的条目：prompt 前缀匹配（子会话 title=prompt 前 57 字+"..."）且候选唯一才绑；配不上的显示线路名，宁无名不错名。
- 与 ZCode 自带"设置→用量"互补：自带走供应商云端接口（套餐额度/剩余），本工具走本地 db（会话排行/上下文容量/对话内查询）。

## 性能开销（实测，db 731MB / 会话活跃场景）

- **架构是事件驱动，不是轮询**：数字刷新跟着"请求完成→db 落盘"走；空闲时零进程零轮询，仅 30s 心跳 stat 一次 mtime（fs.watch 事件不保证送达，心跳不可去——Node 官方文档明确）。
- **单次拉取（常驻 python）：均摊 ~118ms、重算峰值 ~200ms**（常驻化之前为 663ms 冷 / 485ms 热）。收益构成：常驻省解释器启动费 60-100ms；recent 池瘦身（仅兜底候选给完整快照，长尾轻量行 38ms→1ms）；三次全表 group by 合并为一次扫描 + 2s TTL 缓存（聚合慢变量最大陈旧 ~3.5s，无感）；任务名回执解析进程内缓存（29ms→命中 0）。
- **常驻成本 ~17MB 内存**；意外退出自动重启，30s 内 3 次判不稳定自动回退一次性短进程，`resident: false` 可强制回退。
- **端到端数字延迟最坏 ~2s**（去抖 300ms + 限频 1500ms + 查询 200ms），大头是限频——要更跟手调小 `activity_min_ms`，查询侧已无杠杆。
- 全表聚合（会话池/今日合计）是唯一随库体积线性增长的项（现 55+30ms），db 涨大后上调 `zusage.py` 顶部 `_AGG_TTL_MS`（如 4-5s）再摊薄一半以上，架构不用动。

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
| 条周期性闪烁（约 0.4s 一次） | 条几何上盖住了可见性判定用的"输入框中心点"→ 命中检测自遮挡。条必须在输入框中心点所在矩形之外 |
| 悬停 tooltip 每次数据推送就闪 | 推送无条件重建 DOM + 重画 tooltip → 内容比对（lastHtml）+ 签名幂等（tipSig）+ 鼠标在条上时隐藏总闸 |
| diag 停在旧快照、永远没有 mount 字段 | collectDiag 对象引用分裂（现原地更新，见上） |
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
- **子代理任务名勿用时间配对（错配实锤）**：`session.title` 只是首条输入前 57 字截断、不是派发名；part 与子会话的创建时间差虽实测仅 20~230ms，但 error（取消/限额）派发的子会话可能已建+有消耗却永无回执，且多个任务书前 57 字常是公共开头——时间最近邻会把失败派发的名安给来源不明的子会话。必须用回执 `agentId:` 行硬关联，多候选宁无名。
- **状态条条面别放回输入框中心点矩形**：命中检测自遮挡会造成周期性闪烁（内嵌形态时期实测）。
