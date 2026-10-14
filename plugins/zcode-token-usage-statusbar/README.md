# zcode-token-usage-statusbar（插件版）

把 [zcode-token-usage-statusbar](../../../README.zh-CN.md) 的查询能力打包成 ZCode 插件：
`token_usage` MCP 工具 + `/usage` 命令 + 自动触发技能；每次回答末尾自动追加**逐问速率行**；
并可选用**命令或 hook 自动**为桌面客户端安装悬浮用量状态条。用量查询只读本地数据库，不联网；
可选插件更新通过已配置的市场获取代码，不上传用量数据。

## 安装插件

### 方式一：从 GitHub 添加（推荐）

在 ZCode 中执行：

```text
/plugin marketplace add <owner>/zcode-token-usage-statusbar
/plugin install zcode-token-usage-statusbar@zusage-marketplace
```

### 方式二：本地目录

克隆本仓库后，在 ZCode 中打开 **设置 → 插件管理 → 发现 → +**，来源选择「本地目录」，指向**仓库根目录**（含 `marketplace.json` 的那层，不是 `plugins/` 子目录）即可；也可以用命令形式：

```text
/plugin marketplace add /绝对路径/到/zcode-token-usage-statusbar
```

### 更新

插件 **1.1.0** 的浮层 **⚙ 设置 → 插件更新** 包含：

- **检查更新**：按已注册来源比较当前/可用版本，不安装。
- **立即更新**：检测到新版后调用 ZCode 自带插件管理器安装，并同步运行时文件。
- **自动更新**：**1.2.4 起默认开启**，未填写配置时开启，已有显式 `false` 保持关闭；首次运行检查，之后客户端运行期间每 24 小时检测并安装新版；损坏配置或非布尔值不启动自动更新。
  开关存于运行时 `config.json` 的 `plugin_auto_update`，检测时间存于 `plugin-update.json`，跨重启限频。
- 状态显示正在检测/更新、已最新/有新版、失败原因、上次检测时间及需重启提示。
  不自动启用已禁用的插件，不上传用量数据；较新的手改热修保留并提示。

**1.4.0 起**，GitHub 注册来源（含托管在 GitHub 的 Git URL）检测正式 Release；更新时下载 manifest 与 ZIP，校验来源、版本、SHA256 和完整安全解包，再核对官方市场清单指向同一 ZIP/hash，交给官方用户级安装器。排除 draft、预发布与截图 tag，不回退开发分支。维护者须发布一致渠道清单，新远端用户推荐 `OWNER/REPO#plugin-releases`；已有注册不会自动切换。私有仓库暂不支持认证下载。**本地目录只刷新本地文件，不会自动 git pull**。
市场条目或版本不可用时明确报错，不显示“已最新”。仅支持 `zusage-marketplace` 的用户级安装；
不通过浮层更新其它插件或项目级安装。查询版没有浮层设置入口，仍用插件管理页手动更新。

从 1.0.0 初次升级：在 **设置 → 插件管理** 刷新 `zusage-marketplace`，更新本插件到最新版本（当前 **1.4.0**，自动更新要求 **1.1.0+**），
重开会话让 hook 同步运行时，再重启 ZCode 一次（新增主进程更新器）。
之后主进程文件更新仍需重启，overlay.js / zusage.py 在 `hot_reload` 开启时自动生效。
手动刷新也可执行 `/plugin marketplace update zusage-marketplace`；
仓库改动不会直接进入 `~/.zcode/cli/plugins/cache/…` 的已安装副本。

装完即有：MCP 工具 `token_usage`（插件下注册名带命名空间，命令/技能里用裸名即可）、
`/zcode-token-usage-statusbar:usage`、技能 `zcode-token-usage`。

### 客户端升级后的自动恢复（1.2.1）

- ZCode 官方升级覆盖 `app.asar` 后，浮层 loader 本次启动不再运行。SessionStart 的插件钩子不依赖该 loader。
- `plugin_auto_update: true`：SessionStart 后台独立启动检查，依旧按 24 小时限频；和 loader 共用跨进程锁。
  `pendingSync` 优先本地同步，不被市场网络错误或 24 小时间隔挡住。
- `plugin_auto_install_overlay: true`：确认当前注入路径，缺失/旧路径时修复；配置中的 `asar_path` 与 `ZCODE_ASAR` 均可定位非默认安装。
  生产路径通过包身份验证，避免误注入其它 Electron 应用。
- 自动更新默认开启，自动重注入仍默认关闭；两开关互不联动，卸载豁免标记阻止自动重注入。只开自动更新不会偷偷改 asar。
- 修复成功后需**完全退出再启动 ZCode 一次**；不会自动重启客户端。
  1.2.2 起，下一条助手回复顶部用引用块提醒重启；未重启时继续携带提醒，loader 实际加载后解除。
  这是通过 hook 上下文要求模型展示，不是原生即时弹窗，也不依赖浮层是否可见。
- Windows 文件占用导致 TMP 待替换时，由无窗口进程等退出后完成；等待期间官方再次升级会取消旧包替换。
- 插件被禁用/未注册 hook、没有触发新建或恢复会话、asar 权限不足或客户端结构不兼容时，无法自动恢复。
  当前 1.0.0 等旧安装须在插件管理页手动升级到 **1.2.1** 才含这项修复。

**1.2.3 起**，运行时 loader（`inject-main.cjs`）或 updater（`plugin-updater.cjs`）更新时，即使现有注入有效、无需再次注入，也会生成重启提醒。
SessionStart 最近一次诊断保存在运行时目录的 `plugin-session-start.json`；`source` 字段可区分手动验证（如 `manual-repair-check`）与自然 SessionStart 事件，手动执行成功不等于自然触发已验证。

### 更新可靠性修复（1.3.1）

更新与运行时同步分别采用操作系统锁，持续 hash 基线保护同版热修；安装失败按实际安装记录恢复，后台重启需求持久保存并由已加载文件指纹确认。检查期间可关闭自动更新，已开始的安装继续完成。待替换 asar 验证生成时来源与 TMP 身份，短暂占用重试，未知历史 TMP 不自动替换；相同源 mtime 的内容更新仍触发热更新。升级后须完全退出重启，停止不遵守新锁的旧更新器。Windows 分支尚未实机验收。

## GitHub 发布技能（1.4.0）

GitHub 发布技能是源码仓库 `.zcode/skills/zcode-github-release/` 下的项目专用维护工具，不随本插件分发，也不全局安装。它直接调用仓库根的发布脚本，默认只预演；真实提交、tag 推送、Release 和渠道发布须明确授权。插件用户只使用 Release 检测与下载，无需发布技能。

## 逐问速率行（1.2.0）

每次回答的末尾自动附一行本问真实速率（数据读 ZCode usage 数据库，非模型自述）：

```text
⚡ 333.3 tok/s(本轮) · 首字 3.0s · 输出 2,000(+500 思考) tok / 生成 7.5s · 2 段 / 峰 400 · 累计 4.1k tok · ⏱ 17:47:18
```

- **机制**：UserPromptSubmit hook 在每次提问时把提问时刻写入状态文件（`~/.zcode/zusage-rate.last-session.json`），
  并注入「本问统计指令」；模型在回答收尾时运行 `token_rate.py --turn --current`，
  把输出的一行放进 Markdown 引用块贴在回复最末尾（与正文同一条消息，防客户端折叠）。
- **口径**（2026-10-14 据真库 `raw_usage_json` 与思考段时间分布裁决，与 Changlan4/zcode-tps-widget
  等社区实现核对一致）：分子 = `output_tokens`——**ZCode 该列已包含思考 token**（provider 原生
  totalTokens = input + output，不另加 reasoning，再相加会重复计数）；分母 = 纯生成时长，无思考段
  `completed_at − first_token_at`，**含思考段 `completed_at − started_at`**（思考 token 在
  first_token_at 之前流出，窗口须前移，否则分母漏掉思考时长），按段累加、段间工具等待不计入；
  首字延迟取本轮第一段；会话累计 = Σoutput（思考已含）。与状态条「生成速度」同分子同分母口径，
  差异只在统计对象：状态条是**最近一段的瞬时速度**，统计行是**本问全部段的加权均值**（且在收尾
  正文段落库前采样）——悬停状态条速率项可见完整解释。
- **--current 守卫**：最新轮次的所有入库行都早于**本会话**的提问时刻 = 本问还没有任何模型请求
  （纯问答轮收尾自测时必然如此），此时输出为空——**绝不把上一轮数据当作本问显示**。
  提问时刻按会话记录（多窗口交替提问互不干扰，1.3.0 修复）。
  因此纯问答回复有意不带统计行；带工具调用的轮次统计到「最后一次工具调用后的收尾正文」之前。
- **悬停解释（1.3.0 浮层 v67）**：装了悬浮状态条的客户端里，鼠标悬停回复末尾的统计行
  （指针变 ? 形）会弹出与状态条同款的 tooltip，逐项详解每个数值的口径——即时速率的
  分子分母与加权方式、首字延迟、思考 token 含义、段/峰、会话累计、采样时刻，以及与
  状态条「生成速度」为什么不同。统计行本身是 Markdown，hover 能力由浮层扫描消息流注入。
- 手动排查：`python3 <插件目录>/token_rate.py`（上轮 + 会话累计）、`--turn`（最新一问）、
  `--json`（机器可读）。
- **⚙ 设置面板开关（1.3.0，默认开启）**：「逐问速率行 → 回答末尾追加统计行」。关闭后由主进程
  原子写入运行时 `config.json` 的 `answer_rate_line: false`，prompt-submit hook 每次提问时读同一
  文件——**下一问即停用，无需重启**；提问时刻照常记录，`--current` 守卫与 CLI 手动排查不受影响。
  面板开关需要 1.3.0 的主进程 loader（inject-main.cjs 更新后需重启 ZCode 一次）；查询版没装浮层
  时直接改 `answer_rate_line` 键即可，缺省/文件损坏一律视为开启。

## 注意事项

- **装插件 ≠ 装浮层**：插件只提供查询能力（MCP / 命令 / 技能）。悬浮条需要
  `/zcode-token-usage-statusbar:install-overlay` 显式安装，或把运行时目录 `config.json` 的
  `"plugin_auto_install_overlay": true` 交给 SessionStart hook 自动装（默认关，未开启时 hook 只提示一次）。
- **浮层改动要重启客户端生效**（写的是 app.asar，客户端启动时加载）。此后 overlay.js / zusage.py
  更新走热更新免重启，inject-main.cjs 更新才需要再重启一次。
- **卸载插件 ≠ 彻底卸载**：GUI 路径是 设置 → 插件 → 已安装 → ZCode Token Usage Statusbar
  → 三个点 → 卸载，它只移除插件组件（MCP/命令/技能/hook）与 `~/.zcode/cli/plugins/cache/` 下的
  安装副本；浮层注入行、运行时目录、市场注册都**不会**被动。彻底卸载按序执行：
  ① `/zcode-token-usage-statusbar:uninstall-overlay` 并重启（命令由本插件提供，**务必在卸载插件之前**）
  ② GUI 卸载插件 ③ 设置里移除 `zusage-marketplace` 市场并删
  `~/.zcode/cli/plugins/marketplaces/zusage-marketplace/` 克隆 ④ 视需要删运行时目录
  `~/.zcode/zcode-token-usage-statusbar/`（先备份 `config.json`）。完整清单与卸载后自查见仓库
  README 的「卸载 → 彻底卸载干净（全清单）」。
- **钩子只对插件启用之后开始的会话生效**（含恢复的会话）；刚装完插件时当前这个会话不会补触发。
- **Windows**：`.mcp.json` 与 `hooks/hooks.json` 里的 `python3` 若不存在，改成 `python`。
- 诊断：`python3 <插件目录>/overlay_install.py check`（注入状态 / 开关 / 标记一目了然）。

## 悬浮状态条（可选）

安装与卸载入口见上方「注意事项」，这里补充行为细节：

- hook 自动模式下，每次会话启动会静默同步运行时文件：overlay.js / zusage.py 热更新免重启，
  inject-main.cjs 更新只提示需重启；**ZCode 官方升级覆盖 app.asar 后，下一次会话自动重新注入**
  （需 `plugin_auto_install_overlay: true`）。
- 安装器副本比运行时目录旧时不会覆盖（保留手改热修）。
- 豁免标记是运行时目录下的 `.plugin-overlay-optout`，删除该文件即可恢复提示/自动安装。
- Linux 系统级安装位置需要 root，hook 里拿不到 sudo——请改用仓库根 `sudo python install.py`。

## 维护者须知

- 根运行时文件通过 `sync_plugin.py` 同步为**打包副本**；发布工具留在仓库根，不随插件分发；根文件变更后跑 `python3 sync_plugin.py`
  （`--check` 供 CI 查漂移）。
- hook 命令写的是 `python3`，Windows 若无该命令请把 `hooks/hooks.json` 与 `.mcp.json`
  里的 `python3` 改成 `python`。
- 测试接缝：环境变量 `ZUSAGE_PLUGIN_RUNTIME` / `ZUSAGE_PLUGIN_ASAR` 可把安装器指到临时目录，
  不碰真机；`token_rate.py` 用 `ZCODE_USAGE_DB` / `ZUSAGE_RATE_STATE_FILE` 指到 fixture 库与状态文件，
  测试在 `tests/token_rate.test.py`（`python3 tests/token_rate.test.py`）。
