# ZCode Token Usage Status Bar

[中文](README.zh-CN.md) | English

A floating status bar for the [ZCode](https://zcode.ai) desktop client (Electron app) that shows real-time token usage of the current conversation window. It only reads the local SQLite database — never touches the network.

> This page is a condensed English overview. The in-depth docs in this repo are in Chinese.

> **Web edition for self-hosted deployments**: same data, same `zusage.py`, but delivered via
> nginx injection plus a read-only sidecar — works on both the direct web UI and the
> remote-control page. See [`web/`](web/README.md). The desktop and web editions are
> independent; install either or both.

![Status bar overview](docs/tour/shots/en/hero.png)

## Features

The status bar floats at the bottom of the ZCode window and shows real-time token usage and runtime state of the current window. All items below can be toggled in the ⚙ panel.

### ① Generation Speed

![Generation speed tooltip](docs/tour/shots/en/item-1.png)

Tokens per second of the most recent completed request (output tokens ÷ generation time, from first token to completion), color-coded in three tiers: ≥70 green, 40–70 yellow, <40 red — how fast the model is right now, at a glance.

### ② Context Capacity

![Context capacity tooltip](docs/tour/shots/en/item-2.png)

A mini progress bar plus percentage showing how much of the context window the current session uses (total input of the latest request ÷ window capacity). The color shifts green → yellow → red as usage grows; large (≥1M tokens) windows warn earlier at 40%/60%. When a request is rejected for exceeding the window, the bar flashes red and a popup bubble offers three suggestions (roll back the previous turn / continue with a larger-window model or compress the session / start a new conversation). The window size is detected automatically: native ZCode UI (server-driven, follows the model) → built-in model catalog → `config.json` fallback — or override it manually in the ⚙ panel.

### ③ Current Turn

![Current turn tooltip](docs/tour/shots/en/item-3.png)

Token consumption of the latest turn, plus cache hit rate, model request count, single-request duration and first-token latency. Hover for the full breakdown: input / output / cache read / cache write / thinking, along with total turn duration and tool call count.

### ④ Session Total

![Session total tooltip](docs/tour/shots/en/item-4.png)

Total consumption of all requests in the current session, with turn/request counts; code changes (+added / −removed lines and file count) are part of this item's details. Each conversation window shows only its own data — multiple windows never mix. Hover for the five-way breakdown.

### ⑤ Tool Calls

![Tool calls tooltip](docs/tour/shots/en/item-5.png)

Total tool invocations in the session; a red badge lights up when errors occur. Hover for per-tool call counts, durations and error details.

### ⑥ Today's Total

![Today total tooltip](docs/tour/shots/en/item-6.png)

Token consumption across all of today's sessions, aggregated across conversations and recalculated as each request completes.

### ⑦ Sub-agents

![Sub-agent detail panel](docs/tour/shots/en/item-7.png)

Token usage of background sub-agents in the current session, tracked separately from the session total; a blue ● means one is running right now. Hover shows a summary; clicking opens a fixed detail panel with tabs for the summary and each sub-agent's dispatch task name plus its input/output/cache/thinking breakdown.

### ⑧ Settings Panel

![Settings panel](docs/tour/shots/en/item-8.png)

Click ⚙ to toggle any bar item, override the context window size, or switch the UI language (中文 / English) — the change applies immediately and persists. Hovering any bar item also shows a detail tooltip (view-only); interactive content — like sub-agent details — opens in click-triggered fixed panels instead.

### More

- **Automatic context-window detection**: native ZCode UI → model catalog → `config.json` fallback.
- **Session tracking**: each window shows only the data of its own focused session — zero cross-window bleed.
- **SSH remote sessions (v9)**: when the desktop client is connected to a remote zcode-server, session data lives in the remote machine's `~/.zcode/cli/db/db.sqlite`. Add a `remote` section to `config.json` and the bar **automatically switches to querying the remote host over SSH** when a remote conversation is focused — a ☁ "remote" badge appears (hover for host name / last error), and both session stats and the "today" total come from the remote database. The remote host only needs the same `zusage.py` deployed (its own config must NOT enable `remote`, to avoid recursion). Requires passwordless SSH (public-key auth) to the server; the pump polls at `remote.poll_ms` (default 3000) while a remote session is focused — local sessions remain purely event-driven.

  ```json
  "remote": {
    "enabled": true,
    "ssh": "ssh user@server",
    "script": "~/.zcode/zcode-token-usage-statusbar/zusage.py",
    "python": "python3",
    "timeout_s": 6,
    "poll_ms": 3000,
    "host_label": "my-server"
  }
  ```
- **MCP in-chat query**: `token_usage(scope)` supporting current / today / week / days:N / sessions:N / models:days / session:<id prefix> (with per-model and subagent details) / workspace:<dir keyword> (aggregates a workspace's main sessions + subagents, with per-session and per-model details; unmatched keywords return candidate directories).
- **CLI**: `python zusage.py [now|today|json|days N|sessions [N]|models [days]|workspace <dir keyword>|session <id prefix>|watch]`.
- **/usage command** in the chat input.

## Which edition do you want?

This repository ships **two independent editions**. Installing the wrong one does not error —
it simply does nothing — so check first:

| Your setup | Edition | Entry point | How to install |
|---|---|---|---|
| **ZCode desktop client** (the Electron app on Windows / macOS) | **Desktop** | `install.py` | `python install.py` at the repo root |
| **Self-hosted ZCode web** (your own server + nginx, opened in a browser or on a phone) | **Web** | [`web/`](web/README.md) | see `web/README.md` — do **not** run `install.py` |
| Both | Both | — | they are independent and can coexist |

**One-line test**: do you open ZCode as an **installed application**, or by typing a **URL in a browser**?

- The desktop edition injects a loader line into `app.asar`; it only affects the Electron client.
  Running it on a server has no effect at all.
- The web edition uses nginx injection plus a read-only sidecar; it only affects a web UI **you
  serve yourself**. There is no injection point on the official cloud pages.

## Installation (desktop edition)

> ⚠️ **This section is for the desktop edition (Windows / macOS client).** If you want the
> self-hosted **web** edition, see [`web/README.md`](web/README.md) — running `install.py`
> on a server does nothing, because it looks for an Electron client's `app.asar`.

Requirements: Windows or macOS; Python 3.8+ (zero third-party dependencies).

```bash
git clone https://github.com/xhwxt/zcode-token-usage-statusbar.git
cd zcode-token-usage-statusbar
python install.py            # add --lang en for English installer/CLI/MCP output
```

One command does it all: locate the ZCode installation (`ZCODE_ASAR` environment variable → common install locations → otherwise `--root` for the ZCode directory, e.g. `--root D:\Apps\ZCode`, or `--asar` for the full app.asar path; remembered after the first run) → copy the runtime into the data directory `~/.zcode/zcode-token-usage-statusbar/` → migrate/generate config.json → patch app.asar (a single loader line) → register the MCP server → install the /usage command → open an install-monitor window that reminds you to restart ZCode and confirms the injection took effect.

**ZCode upgrades overwrite app.asar — just re-run `python install.py`.**

## Data Source

Reads `~/.zcode/cli/db/db.sqlite` (`model_usage` / `turn_usage` / `tool_usage` — one row per model request), strictly read-only, never online. Numbers refresh event-driven when a request completes; when idle there are zero extra processes and zero polling.

## Uninstall

```bash
python install.py --remove
```

Uninstall does not rely on a backup: it only adds/removes this tool's own injection line inside app.asar, works while ZCode is running (effective after restart), leaves injections added later by other tools untouched, and never "restores" an officially upgraded asar back to an older snapshot.

## Notes

- Accounting semantics: totals (`total` / 合计) = input + output as recorded by the client (`computed_total_tokens`); cache-read tokens are already **inside** input — don't add them again. Reasoning tokens are provider-reported only (e.g. DeepSeek reports them inside output; some GLM model ids never report them), so treat the reasoning split as informational.
- A session's usage is attributed to the workspace directory it currently belongs to; `model_usage` has no workspace dimension, so usage from before a session was moved to another workspace counts toward the new one. Subagent sessions carry the same directory as their parent — the `workspace:` scope groups them via `parent_id` to avoid double counting.
- Data semantics, performance measurements, diagnostics and pitfall notes (Chinese) live in [docs/design-notes.md](docs/design-notes.md).
- Patching app.asar is an unofficial injection route; ZCode updates overwrite it — re-run install after upgrading.
- Non-default install locations: pass `--root <ZCode directory>` (recommended — the platform's fixed `resources/app.asar` is appended) or `--asar <full app.asar path>`, or set the `ZCODE_ASAR` environment variable; the location is remembered after the first successful run.
- Auto-detection and the fallback scan only accept a target confirmed to be ZCode (a sibling `ZCode.exe`, or a package `name` containing zcode), so other Electron apps on the same machine (e.g. opencode) are never injected (added with the issue #6 fix).
- macOS support was added in v60 (issue #2). Windows is the fully tested platform; the author has no macOS device, so macOS is untested — issues and feedback are welcome.
- License: [MIT](LICENSE).
