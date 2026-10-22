# ZCode Token 用量状态栏

[English](README.md) | 简体中文

给 [ZCode](https://zcode.ai) 桌面客户端（Electron 应用）加上一个悬浮的 token 用量状态条。数据源是本地 SQLite（`~/.zcode/cli/db/db.sqlite` 的 `model_usage` / `turn_usage` / `tool_usage` 表，每次模型请求一行），**全程只读、不联网**。

平台：Windows、macOS ｜ 许可：[MIT](LICENSE)

## 先选版本：你是哪一种用户？

本仓库有**两个互不依赖的版本**，装错了不会报错，只会「什么都没发生」——所以先对一下：

| 你的情况 | 装哪个 | 入口 | 装法 |
|---|---|---|---|
| 用 **ZCode 桌面客户端**（Windows / macOS 的 Electron 应用） | **桌面版** | `install.py` | 根目录 `python install.py` |
| 用**自托管 ZCode 的网页**（自己跑的 server + nginx，浏览器/手机访问） | **网页版** | [`web/`](web/README.zh-CN.md) | 见 web/ 的 README，**不要**跑 `install.py` |
| 两个都用 | 两个都装 | — | 互不干扰，可共存 |

**一句话判断**：你打开 ZCode 是用一个**装好的应用程序**，还是在**浏览器里输网址**？

- 桌面版往 `app.asar` 注入一行 loader，**只对 Electron 客户端有效**；在服务器上跑它不会有任何效果。
- 网页版靠 nginx 注入 + 一个只读旁挂服务，**只对你自己伺服的网页有效**；官方云端的页面没有注入点。

> 文档与界面统一使用中文名「ZCode Token 用量状态栏」；仓库与 MCP 注册名为 `zcode-token-usage-statusbar`（全称的英文写法），代码内部标识符为 `zusage`。

## 为什么是注入而不是插件？

ZCode 的插件机制（`plugin.json`）只能提供 MCP / skills / commands / hooks，**没有客户端 UI 能力**；状态条要画进主窗口，唯一路线是往 `app.asar` 主入口注入一行 loader。MCP 查询部分则是标准的插件级能力，一键安装会自动注册。

（网页版的处境一样：插件清单只能贡献 agent/command/skill/hook/mcp 五种组件，官方
`CONTEXT.md` 写明、协议层也是封闭枚举。区别是网页版没有 asar，所以改在 nginx 层注入。）

## 功能

状态条悬浮在 ZCode 对话窗口最底部，实时显示本窗口的 token 用量与运行状态——数据只读本地数据库（每次模型请求一行），全程不联网、零轮询。

![状态条整体效果](docs/tour/shots/hero.png)

### ① 生成速度

![生成速度](docs/tour/shots/item-1.png)

条面第一项显示最近一次完成请求的生成速度（输出 tokens ÷ 生成耗时，从首 token 到完成），按快慢三档变色：≥70 绿、40–70 黄、<40 红——当前模型快不快，一眼可读。每次请求完成落库后自动刷新。

### ② 上下文容量

![上下文容量](docs/tour/shots/item-2.png)

微进度条 + 百分比显示当前会话的上下文占比（最近一次请求的总输入 ÷ 上下文窗口容量），颜色随占比从绿转黄再转红；百万级大窗口提前预警（40%/60% 档），其余窗口 70%/85% 档。请求因超出窗口容量被拒绝时，进度条亮红闪烁并弹出告警气泡，给出三条处置建议（回滚上一轮对话 / 换用上下文窗口更大的模型继续，或压缩精简本会话 / 新开会话）。窗口大小无需手动配置：优先读 ZCode 原生 UI（服务端下发、自动跟随模型）→ 模型目录查表 → `config.json` 兜底，也可以在 ⚙ 设置里手动覆盖。

### ③ 本轮

![本轮](docs/tour/shots/item-3.png)

显示最近一轮对话（从你发消息到本轮完成）的 token 消耗、缓存命中率、模型请求次数、单次请求耗时与首字延迟。悬停可看完整拆分：输入 / 输出 / 缓存命中 / 缓存写入 / 思考，以及轮总耗时与工具调用次数。

### ④ 会话累计

![会话累计](docs/tour/shots/item-4.png)

当前会话全部请求的总消耗与轮次、请求次数统计；代码变更（+新增 / -删除行数与涉及文件数）也在这一项的明细里。每个对话窗口只显示它自己的数据（泵按窗口注入焦点会话），多窗口互不串显；悬停同样有 输入 / 输出 / 缓存命中 / 缓存写入 / 思考 的五项拆分。

### ⑤ 工具调用

![工具调用](docs/tour/shots/item-5.png)

当前会话的工具使用总次数；出现错误时右侧亮红色错误数徽标，工具是否稳定一目了然。悬停可看各工具的调用次数、耗时与错误明细。

### ⑥ 今日合计

![今日合计](docs/tour/shots/item-6.png)

今天所有会话的 token 总消耗，跨会话汇总、随每次请求完成实时重算，日用量一目了然。

### ⑦ 子代理

![子代理明细面板](docs/tour/shots/item-7.png)

当前会话后台子代理的 token 消耗，独立统计、不计入会话累计；蓝色 ● 表示有子代理正在运行。悬停显示汇总，点击弹出固定明细面板——按页签切换汇总与各子代理，每个子代理显示派发任务名与输入/输出/缓存/思考明细。

### ⑧ 设置面板

![设置面板](docs/tour/shots/item-8.png)

点击 ⚙ 打开设置：各显示项独立开关、上下文窗口大小手动覆盖、界面语言切换（中文 / English，即时生效并记住选择）。悬停条面任意条目都会弹出明细 tooltip（纯查看），需要操作的内容（如子代理明细）则用点击弹出的固定面板，互不打架。

### 其它能力

- **上下文窗口自动识别**：优先读 ZCode 原生 UI（输入框工具行按钮文本"…总量 N"，服务端下发、自动跟随模型）→ 模型目录查表 → `config.json` 兜底。
- **会话跟随**：泵按窗口注入本窗口焦点会话 id（渲染端经客户端 IPC 通道上报），每个对话窗口只显示它自己的数据，池里没有就显示零值、绝不串显别的会话。
- **SSH 远程会话支持（v9）**：桌面客户端连远端 zcode-server 时，会话数据落在远端机的 `~/.zcode/cli/db/db.sqlite`，本地库查不到。配置 `config.json` 的 `remote` 段后，切换到远端对话会**自动改为 SSH 到远端机查询**：条面出现 ☁「远端」徽标（悬停显示主机名与最近错误），会话数据与「今日合计」均为远端值。远端机只需部署同一份 `zusage.py`（保持其 config 不含 remote 段即可，防递归）：

  ```json
  "remote": {
    "enabled": true,
    "ssh": "ssh 用户名@服务器地址",
    "script": "~/.zcode/zcode-token-usage-statusbar/zusage.py",
    "python": "python3",
    "timeout_s": 6,
    "poll_ms": 3000,
    "host_label": "我的服务器"
  }
  ```

  前提：本地到服务器的 `ssh` 免密可用（公钥认证）。**`ssh` 也可以写成数组（argv 形式）**——凡是能在远端执行命令的通道都能借道，不必有公网 sshd 与公钥免密：跑在本地 Docker 容器里的 zcode-server 用 `["docker","exec","-i","<容器名>"]`、WSL 用 `["wsl","-d","<发行版>"]`、k8s 用 `["kubectl","exec","-i","<pod>","--"]` 都可以（实测场景：容器 sshd 认证不通，`docker exec` 直读容器内 `db.sqlite`，端到端合并正常）。实现上，本地 `zusage.py` 对本地库查不到的会话 id（双重白名单校验）执行 `ssh <host> python3 <script> json <sids>`，把远端快照合并进 payload；远端返回 `known_sids` 供负缓存（确认无此会话 2 分钟内不重问，ssh 失败 60 秒退避），本地新会话（session 表已有行）不会触发远端查询。泵在远端会话聚焦期间按 `poll_ms` 轮询（本地会话仍是纯事件触发、零轮询）；`poll_ms` 别低于 1000，跨公网建议配合 ssh ControlMaster 连接复用。远端命中但各项为 0（远端库有该会话、却没有已完成的请求）与「本地库没有这个会话、又没配 remote」两种情形，条面徽标会写明原因，不再只显示一排 0。
- **MCP 对话内查询**：`token_usage(scope)` 工具，scope 支持 current / today / week / days:N / sessions:N / models:days / session:<id前缀>（含按模型与子代理明细）/ workspace:<目录关键词>（按工作区聚合主会话+子代理，含按会话与按模型明细；关键词未命中时会列出候选目录）。
- **CLI**：`python zusage.py [now|today|json|days N|sessions [N]|models [days]|workspace <目录关键词>|session <id前缀>|watch [秒]]`。
- **/usage 命令**：对话输入框触发 MCP 查询。

## 文件说明

| 文件 | 作用 |
|---|---|
| `install.py` | **一键安装**：探测 ZCode 安装位置 → 复制运行时到数据目录 → 生成 config → 注入 asar → 注册 MCP → 装 /usage 命令 → 弹监控窗口。`--remove` 卸载，`--dry-run` 预览，`--no-mcp` 只装状态条，`--dev` 开发模式（不复制，注入直指本仓库目录）。 |
| `bootstrap.py` | **给别人装用**：体检（Python 版本 / 文件齐备 / asar 定位 / ZCode 身份 / 注入状态 / 配置 / MCP）→ 按结论自动选动作。`--check` 只体检、`--repair` 只补注入、`--install` 完整安装、`--uninstall` 卸载、`--json` 机器可读。所有探测都打印试过哪些路径，不静默失败。 |
| `package.py` | **打包**：生成自包含 zip（含 `INSTALL-给AI看.md`），对方解压跑 `python bootstrap.py` 即可，无需 git/联网/pip。`--no-docs` 出精简包。 |
| `settings.html` + `settings-preload.cjs` | 独立设置窗（终末地风格）与其 preload 桥。字段编辑走主进程白名单 + 原子替换，`python_path` / `asar_path` 只读不下发。 |
| `autopatch.py` | ZCode 升级后补注入：`check` 只解析 asar 头（74~99ms）返回退出码，`apply` 仅在需要时重打包。适合放进登录计划任务。 |
| `overlay.js` | 状态条本体（渲染进程注入，自包含 IIFE）。fixed 悬浮在**窗口最底部**（条下只留 3px 缝，给输入卡片加 margin-bottom 上移让位），rAF 每帧跟随；输入框定位要求 textarea 在视口下半部（排除设置页等处的输入元素）。 |
| `inject-main.cjs` | 主进程 loader：向每个窗口注入 overlay.js + **触发式监听 db 写入**（fs.watch db 目录，有写入→去抖 300ms→查询推送；空闲零进程零轮询，仅 30s 兜底心跳）。查询走**常驻 python**（`zusage.py serve` 行协议；意外退出自动重启、30s 内 3 次判不稳定回退一次性 spawn、zusage.py mtime 变化自动重启；stdin EOF 随泵退出自清理）。 |
| `patch_install.py` | asar 注入/卸载工具（install.py 的底层，可单独用）：在入口 `out/main/index.js` 尾部追加/剥离本工具的 dynamic import 行 → 重打包 → 自检 → 原子替换。不制作也不依赖备份，卸载只动自己的注入行（其它工具后装的注入行不受影响）。幂等，自动替换旧注入行（迁移友好）。安装成功自动弹出监控窗口。 |
| `install_monitor.py` | 安装监控窗口（常驻命令行，每 10 秒检测一次）：持续提醒重启 ZCode；重启后通过 diag 更新确认注入加载，显示成功并自动退出。运行中原子替换失败（生成 .tmp）时，它还会在 ZCode 退出后自动完成替换——**收尾不依赖计划任务**。 |
| `zusage.py` | 查询库 + CLI + 机器可读快照（`json` 子命令，状态条数据源）。 |
| `usage_mcp.py` | 零依赖 stdio MCP server。 |
| `config.example.json` | 运行配置模板（安装时生成到数据目录的 `config.json`；含本机路径，不入库）。 |

**数据目录 `~/.zcode/zcode-token-usage-statusbar/`** 是标准安装的运行目录：运行时副本（`inject-main.cjs`/`overlay.js`/`zusage.py`/`usage_mcp.py`/`settings.html`/`settings-preload.cjs`）、`config.json`、诊断产物 `diag-<n>.json`（每窗口一份，主进程定期回写）都在这里。本仓库只是源码，**clone 目录可以随意搬走或删除，不影响已安装的实例**。

## 安装（桌面版）

> ⚠️ **这一节是桌面版（Windows / macOS 客户端）。** 如果你要装的是自托管网页版，
> 请看 [`web/README.zh-CN.md`](web/README.zh-CN.md) —— 在服务器上跑下面的 `install.py` **不会有效果**，
> 它找的是 Electron 客户端的 `app.asar`。

前提：Windows 或 macOS；Python 3.8+（零第三方依赖）；fuses `EmbeddedAsarIntegrityValidation=0`（ZCode 当前版本实测为 0）。

```bash
git clone https://github.com/xhwxt/zcode-token-usage-statusbar.git
cd zcode-token-usage-statusbar
python install.py            # 加 --lang en 可让安装器/CLI/MCP 输出英文
```

一条命令完成：探测 ZCode 安装位置（环境变量 `ZCODE_ASAR` → 常见目录 → 找不到时询问，或 `--root` 指定 ZCode 安装目录 / `--asar` 指定 app.asar 完整路径；首次成功后记住位置，之后一律免传）→ **复制运行时到数据目录 `~/.zcode/zcode-token-usage-statusbar/`** → 迁移/生成 config.json → 注入 asar（注入行指向数据目录副本）→ 注册 MCP（server 名 `zcode-token-usage-statusbar`，指向数据目录副本）→ 安装 /usage 命令 → 弹出「安装监控」窗口（每 10 秒检测一次，提醒重启 ZCode；重启后检测到注入加载即显示成功并自动关闭；万一运行中原子替换失败，监控窗口会在你退出 ZCode 后自动完成替换）。

**ZCode 升级会覆盖 app.asar，重跑一次 `python install.py` 即可**（监控窗口提示"未检测到注入加载"通常就是这个原因）。

### 让别人（或别的 AI）帮我装

不想自己动手的话，把仓库打包成一个自包含的 zip 传给对方，对方解压后一条命令就行：

```bash
python package.py            # 生成 dist/zcode-token-usage-statusbar-v64.zip（约 1.5MB）
```

包里有 `INSTALL-给AI看.md`，用大白话写清了「按顺序跑哪三条命令、退出码各是什么意思、
必须知道的四个前提」。整个过程不联网、不需要 git、不需要 pip。

拿到包的人（或 AI）这样跑：

```bash
python bootstrap.py --check     # 先体检：0=健康 1=需要修 2=环境缺失
python bootstrap.py             # 体检 + 按需自动修复（最省心）
python bootstrap.py --install   # 完整安装 / 重装
python bootstrap.py --repair    # 只补注入（ZCode 升级后最常见）
python bootstrap.py --uninstall # 卸载
```

`bootstrap.py` 只做三件事：确认这是 ZCode（同级有 `ZCode.exe` 或包名含 zcode，
避免误注入别的 Electron 应用）、校验运行时文件齐不齐、把结论翻译成可执行的下一步。
加 `--json` 给机器读，适合让 AI 直接判断该走哪条分支。

<details>
<summary>手动分步安装（等价于 install.py 做的事）</summary>

```bash
# 0) 数据目录：复制运行时 + 配置（改 python_path 为你的 python.exe 绝对路径）
mkdir -p ~/.zcode/zcode-token-usage-statusbar
cp inject-main.cjs overlay.js zusage.py usage_mcp.py ~/.zcode/zcode-token-usage-statusbar/
cp settings.html settings-preload.cjs ~/.zcode/zcode-token-usage-statusbar/   # 独立设置窗
cp config.example.json ~/.zcode/zcode-token-usage-statusbar/config.json   # 然后编辑它

# 1) 状态条：注入 asar（注入行须指向数据目录的 inject-main.cjs；ZCode 运行中也可执行；
#    ZCode 不在脚本默认位置时先改脚本顶部 ASAR，或直接用 install.py）
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
| `zusage.py` | 重跑 install.py 同步副本后，泵检测到副本 mtime 变化即自动重启常驻 python（~2 秒生效）；一次性回退模式下仍是每次轮询新进程、下次轮询生效 |
| `config.json`（数据目录） | 下次拉取时生效（拉取由 db 写入触发；完全空闲时最迟一个兜底心跳，默认 30s） |
| `inject-main.cjs` 本身 / 首次安装 / 标准↔dev 互切 | 重跑 `python install.py` 后**需重启 ZCode**（注入行含绝对路径，脚本自动替换旧行） |
| ZCode 官方升级覆盖 asar | 跑 `python autopatch.py apply` 补回注入（只解析头部即可判断，已注入时秒退） |

## 配置（config.json）

位置：标准安装在数据目录 `~/.zcode/zcode-token-usage-statusbar/config.json`（`--dev` 安装在仓库目录）。

**图形界面**：点状态条上的 ⚙ → 「打开完整设置…」，会开一个独立设置窗口，
上面这些字段都能直接改（保存即写入，约 1 秒生效）。
`python_path` 与 `asar_path` 属于安装信息，只显示不可改——改错了会把注入链搞断，
要换位置请用 `install.py --root` / `--asar`。

```json
{
  "python_path": "python.exe 绝对路径",
  "activity_min_ms": 1500,  // 有 db 写入时的最小拉取间隔（活动期限频，到点自动补拉最后一笔）
  "heartbeat_ms": 30000,    // 兜底心跳：防 fs.watch 文件事件丢失；空闲时拉取只由此触发
  "resident": true,         // 常驻查询 python（zusage.py serve，省每次启动费 ~17MB 常驻内存）；false=回退一次性短进程
  "hot_reload": true,       // overlay.js 热更新开关（调试用）；false=改完需重启 ZCode
  "diag": false,            // 诊断落盘（写 diag-*.json），排障时才开，开着会持续 IO
  "archive_days": 7,        // 超过 N 天未活动的会话视为旧会话；0 = 关闭
  "archive_mode": "skip",   // off=不处理 / count=只统计 / skip=旧会话不进候选池（省查询）
  "context_window": 1000000 // 上下文兜底值（原生读数/模型目录都失败时用）
}
```

> 归档是按「会话自己的最后活动时间」判断的，正在用的会话和最新会话永远不会被误标。
> 统计数字是全库口径：本机 76 个会话在 7 天阈值下会报出 51 个旧会话。
> **`skip` 是唯一能减少查询量的模式**（1 天阈值下候选池从 12 个降到 2 个）；
> `count` 只影响统计提示，不改变加载行为。
>
> 它不提升 ZCode 启动速度（客户端本来就不预加载会话正文，实测占比不到 2ms），
> 插件也无法改动 ZCode 自己的侧栏列表——这里说的「不加载」指的是不进插件的候选池。

## 数据口径、性能与排查

各数字的统计口径、性能实测数据、诊断手段（diag）与踩坑记录，见 **[docs/design-notes.md](docs/design-notes.md)**。

装好之后怎么用（三个入口、每项设置改什么、升级 ZCode 后怎么补注入、出问题怎么排查），见
**[docs/USAGE.md](docs/USAGE.md)**。启动开销的归因与实测见 [docs/PERF.md](docs/PERF.md)。

## 已知限制

- 口径说明：合计 = input + output（客户端 `computed_total_tokens` 原值），缓存读已含在 input 内、不要重复相加；reasoning（模型推理/思维链 token）仅部分提供方上报（DeepSeek 系上报且含在 output 内，部分 GLM 模型 id 恒不上报），推理拆分仅供参考。
- 会话的用量归属其当前所在工作区目录（model_usage 无工作区维度），会话迁移过工作区的话历史用量一并算进新目录；子代理会话的 directory 与父会话相同，`workspace:` scope 按 `parent_id` 归并去重，避免主/子重复计数。
- Windows 为全功能实测平台；macOS 支持自 v60 起（issue #2 反馈驱动），作者无 macOS 设备、未经实测，遇到问题欢迎开 issue 反馈。
- ZCode 安装位置自动探测常见目录；非标准位置用 `python install.py --root <ZCode 安装目录>` 指定（推荐，自动接上平台固定的 `resources/app.asar`），或 `python install.py --asar <app.asar 完整路径>`，首次成功后记住位置、之后免传；也可设环境变量 `ZCODE_ASAR`。
- 自动探测与兜底扫描只在确认目标是 ZCode（同级存在 `ZCode.exe`，或包内 `package.json` 的 name 含 zcode）时才会采用，同机的其它 Electron 应用（如 opencode）不会被误注入（该身份校验随 issue #6 的修复引入）。
- 依赖 fuses `EmbeddedAsarIntegrityValidation=0`。官方一旦收紧此 fuse 或改入口结构，注入路线即失效（届时 `python install.py --remove` 剥离注入行即恢复原样）。
- 修改客户端 asar 属非官方注入方式，ZCode 升级会覆盖，需重跑 install。
- 上下文超限亮红依赖 db 中 `context_exceeded` 标记行；触发条件是请求真被服务端拒绝，无法本地模拟测试。

## 升级 ZCode 之后（补注入）

官方升级会整包覆盖 `app.asar`，注入行随之消失。`autopatch.py` 专门处理这件事，
不必重跑整个安装：

```bash
python autopatch.py check   # 退出码 0=已注入 / 1=需要补 / 2=环境问题
python autopatch.py apply   # 只在需要时补注入（静默，不弹监控窗口）
```

`check` 只解析 asar 头部（实测 74~99ms），开销可忽略，适合每次登录或由启动包装脚本调用；
`apply` 只在检查失败时才重打包（307MB，十几秒）。

接法建议：Windows 计划任务在登录时触发一次 `apply`——已注入时它 100ms 就退出，
几乎无感，且完全不碰 ZCode 自己的启动路径，升级重建快捷方式也不受影响。

## 卸载

```bash
python install.py --remove    # 从 asar 剥离本工具注入行 + 移除 MCP 注册与 /usage 命令 + 删除数据目录
# 或只卸状态条：python patch_install.py remove
```

卸载不依赖备份：只在 asar 里增删本工具自己的注入行，运行中也可执行（重启后生效）；其它工具后装的注入行不受影响，也不会把官方升级后的新版 asar"还原"回旧版本。

## 目录迁移

标准安装下 clone 目录只是源码：**删掉或搬走都不影响已安装的实例**（注入行与 MCP 注册指向数据目录 `~/.zcode/zcode-token-usage-statusbar/`，运行时全套基于 `__dirname`/`__file__` 自定位）。换机器/换目录重新 clone 后重跑 `python install.py` 即可，会自动替换 asar 里的旧注入行（无需先 remove）。开发者改代码想即时生效用 `python install.py --dev`（注入直指仓库目录，改文件热更新即达；切回标准形态重跑不带 `--dev` 的 install 即可）。

## License

[MIT](LICENSE)
