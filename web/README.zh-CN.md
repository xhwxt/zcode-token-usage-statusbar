# ZCode Token 用量状态栏 · Web 版

给**自托管 ZCode 的网页**加同一条 token 用量状态条 —— 直连页（ZCode server 自带的 web）
和远控页（手机远控那套官方页面）都能显示。

[English](README.md) | 简体中文

> ⚠️ **这是网页版，不是桌面版。**
> **不要运行仓库根目录的 `install.py`** —— 那个脚本找的是 Electron 客户端的 `app.asar`，
> 在服务器上跑它不会有任何效果（也不报错，只是什么都没发生）。
> 桌面版请回[仓库根目录的 README](../README.zh-CN.md)。

> 桌面客户端（Electron）的版本在仓库根目录（`install.py`）。这一份是**网页版**，
> 与桌面版共用同一套数据口径（同一个 `zusage.py`），但走的是完全不同的注入路线。

## 为什么网页版不能用桌面版那套

桌面版靠往 `app.asar` 的主入口注入一行 loader。网页版没有 asar 可注入，
而 ZCode 的插件机制（`plugin.json`）只能提供 agent/command/skill/hook/mcp **五种组件**，
碰不到渲染层 —— 官方在 `CONTEXT.md` 里写明，协议层也是封闭枚举
（`zcodePluginComponentKindSchema = z.enum(["agent","command","skill","hook","mcp"])`）。

所以网页版走的是另一条路：**在 nginx 上注入一行脚本 + 一个只读旁挂服务**。

```
浏览器 ──443──▶ nginx ──┬── /            → ZCode server（直连页）
                        ├── /remote/…    → relay（远控页）
                        └── /zusage/*    → 旁挂服务(3033)
                                │
                                ▼
                    只读 SQLite（mode=ro + query_only）
```

这套做法不修改 ZCode 源码、不需要重新构建、不需要重启 ZCode 服务，
而且 ZCode 升级重建后依然有效（注入在 nginx 层，不在构建产物里）。

## 形态

**窄屏（<768px）** —— 输入框正下方一行，点击出明细抽屉：

```
⚡ 127 t/s   ▬ 0.0%   🗄 94%   💬 39.1K 1轮   📅 48.32M
```

**宽屏（≥768px）** —— 胶囊条，悬停任一组出明细：

```
⚡127 t/s │ ▬0.0% │ 🗄94% │ 💬39.1K 1轮 │ 🔧1 ✕2 │ 📅48.32M │ ⑂236.4K ● │ ⚙
```

两端共用一份条目构建，但**显示项各存一套配置**（手机一行放 5 项就满，桌面有 800px+）。
抽屉/弹层里有「显示项」开关，勾完即时生效并记住。

### 该出现和不该出现

| 场景 | 行为 |
|---|---|
| 会话页 / 首屏 | 显示 |
| 打开侧栏或侧面板抽屉 | **照常显示**，但层级低于抽屉，被它盖住 |
| 打开状态条自己的明细抽屉 | 照常显示（由层级决定谁在上面） |
| 设置界面 | **隐藏**（`[data-testid="settings-page"]`） |
| 没有输入框的页面（任务列表等） | 自动隐藏 |

## 文件

| 文件 | 作用 |
|---|---|
| `boot.js` | 注入进页面的唯一入口：取 token、跟踪当前会话、轮询快照、推给 overlay |
| `overlay.js` | 渲染（窄屏一行 / 宽屏胶囊条 + 抽屉 / 弹层）。**与仓库根目录那份桌面版 `overlay.js` 不是同一个文件** |
| `zusage-sidecar.mjs` | 旁挂服务：常驻 python 泵 + 快照 API + 静态脚本 + token 校验 |
| `readonly-guard.py` | 强制只读包装器，见下 |
| `zusage-sidecar.service` | systemd 单元模板 |
| `nginx.conf.example` | nginx 接入片段（三处新增） |

## 安装

```bash
# 1) 放好文件（示例路径）
sudo mkdir -p /opt/zcode-token-usage-statusbar
sudo cp -r . /opt/zcode-token-usage-statusbar/          # 整个仓库，sidecar 要用到 ../zusage.py

# 2) 装服务，改掉里面的 /path/to/... 占位
sudo cp web/zusage-sidecar.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now zusage-sidecar

# 3) 自检
curl -s localhost:3033/zusage/health                    # {"ok":true}

# 4) 按 web/nginx.conf.example 加三处，然后
sudo nginx -t && sudo systemctl reload nginx
```

前提：Python 3.8+（零第三方依赖）、Node 22.19+ / 24+（用到 `node:sqlite`）。

## 为什么需要 readonly-guard.py

上游 `zusage.py` 的 `connect()` 在 `mode=ro` 打开失败时会**回退成可写句柄**
（`sqlite3.connect(str(DB_PATH))`，见 `zusage.py:31-42`）。桌面版场景下这没问题，
但网页版的旁挂服务与 ZCode 服务**同时**活着，ZCode 正在写同一个库 ——
绝不能出现第二个写者（哪怕只是理论上）。

`readonly-guard.py` 在 `zusage.py` 拿到控制权之前就把 `sqlite3.connect` 换掉：
未带 `mode=ro` 的路径一律改写成 `file:<path>?mode=ro` 且 `uri=True`，
连接建立后统一 `PRAGMA query_only=1`。systemd 单元里再加 `ProtectHome=read-only` 兜底。

## 上下文窗口的来源（重要）

插件内置的模型目录查表指向一个**硬编码的 Windows 路径**
（`zusage.py:417`：`D:\ZCode\resources\model-providers\...`），在 Linux 上必然落空，
于是退到 config 默认的 `128000`。对本机 1M 窗口的模型，这会把用量显示成 **8 倍**
（248K 显示为 194%）。

取值优先级：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | 页面 `[data-usage-max]` | ZCode 原生下发、跟随模型，最准 |
| 2 | 页面 aria-label 的「总量 N」/「of N」 | 原生上下文环的标签 |
| 3 | 服务端 `provider_config.json` | 由旁挂服务读 `providerModelRules[].config.properties.contextWindow` |
| 4 | 取不到就显示 `–` | 不编造 |

## 与页面共存的约定

状态条是**叠加层**，不改 ZCode 的任何源码与构建产物。对页面 DOM 的写操作**有且仅有一个**：
给输入框视觉卡片设 `marginBottom` 让位，且是自适应的 ——
够放就完全不占位，不够才补差额（实测在两个页面上从未触发，因为 ZCode 布局本身
在输入框下方预留了 28–85px，而条高只有 22–28px）。

其余一律只读：不加 class、不改 `<html>`、不注入全局 CSS 变量、不监听 React。
断点用 `(max-width: 767.5px)`，与树内 `useIsNarrowViewport.ts` 同值。

远控页的 URL 上没有 token（它是 relay 经 workspace-bridge 下发的 `wsUrl` 查询参数），
所以 `boot.js` 在**拿不到 URL token 时**才钩一次 `WebSocket` 构造把它取出来；
直连页 URL 自带 `?token=`，不挂钩。钩子只做透传：保留 `prototype`、复制静态常量。

## 已知限制

- 只服务**自托管**部署。官方云端的页面由官方伺服，没有注入点。
- 快照接口的 token 复用 ZCode 自己的那份。多用户/多租户场景需要另行设计。
- 远控页从宽屏缩到手机宽度会回退到任务列表 —— 这是官方远控页自身的布局切换行为，
  与本插件无关（已用 A/B 对照验证）。

## License

MIT，同仓库根目录。
