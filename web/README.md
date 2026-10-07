# ZCode Token Usage Status Bar · Web Edition

The same token usage bar, for the **web pages of a self-hosted ZCode** — both the
direct web UI shipped by the ZCode server and the official remote-control page.

English | [简体中文](README.zh-CN.md)

> ⚠️ **This is the WEB edition, not the desktop one.**
> **Do not run `install.py` from the repository root** — that script looks for an Electron
> client's `app.asar`; on a server it does nothing at all (and does not error either).
> For the desktop edition, go back to the [root README](../README.md).

> The Electron desktop edition lives at the repository root (`install.py`).
> This is the **web edition**: same data pipeline (the same `zusage.py`),
> completely different injection route.

## Why the desktop route does not work here

The desktop edition injects a loader line into `app.asar`. The web edition has no
asar to patch, and ZCode's plugin mechanism (`plugin.json`) can only contribute
`agent / command / skill / hook / mcp` components — it has no access to the render
layer. That is stated in ZCode's own `CONTEXT.md`, and the protocol enum is closed
(`zcodePluginComponentKindSchema = z.enum(["agent","command","skill","hook","mcp"])`).

So the web edition takes a different route: **one injected script line at the nginx
layer plus a read-only sidecar service**.

```
browser ──443──▶ nginx ──┬── /            → ZCode server (direct web UI)
                         ├── /remote/…    → relay (remote-control page)
                         └── /zusage/*    → sidecar (3033)
                                 │
                                 ▼
                    read-only SQLite (mode=ro + query_only)
```

No ZCode source changes, no rebuild, no restart of any ZCode service — and it
survives ZCode upgrades, because the injection lives in nginx, not in build output.

## Appearance

**Narrow (<768px)** — one line under the composer; tap for a detail sheet:

```
⚡ 127 t/s   ▬ 0.0%   🗄 94%   💬 39.1K 1t   📅 48.32M
```

**Wide (≥768px)** — a capsule bar; hover any group for details:

```
⚡127 t/s │ ▬0.0% │ 🗄94% │ 💬39.1K 1t │ 🔧1 ✕2 │ 📅48.32M │ ⑂236.4K ● │ ⚙
```

Both widths share one item builder but keep **separate visible-item settings**
(five items already fill a phone row; desktop has 800px+). Toggle them in the
sheet / popover; changes apply immediately and are remembered.

### When it shows

| Situation | Behaviour |
|---|---|
| Session page / home | shown |
| Sidebar or side-panel drawer open | **still shown**, but layered *below* the drawer |
| The bar's own detail sheet open | still shown (layering decides) |
| Settings page | **hidden** (`[data-testid="settings-page"]`) |
| Pages without a composer (task list, etc.) | hidden automatically |

## Files

| File | Role |
|---|---|
| `boot.js` | The single injected entry: token, current-session tracking, polling, push to overlay |
| `overlay.js` | Rendering (narrow line / wide capsule + sheet / popover). **Not the same file as the desktop `overlay.js` at the repository root** |
| `zusage-sidecar.mjs` | Sidecar service: resident python pump + snapshot API + static scripts + token check |
| `readonly-guard.py` | Forced read-only wrapper, see below |
| `zusage-sidecar.service` | systemd unit template |
| `nginx.conf.example` | nginx snippet (three additions) |

## Install

```bash
# 1) place the files
sudo mkdir -p /opt/zcode-token-usage-statusbar
sudo cp -r . /opt/zcode-token-usage-statusbar/     # whole repo; the sidecar uses ../zusage.py

# 2) install the service and replace the /path/to/... placeholders
sudo cp web/zusage-sidecar.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now zusage-sidecar

# 3) self-check
curl -s localhost:3033/zusage/health               # {"ok":true}

# 4) apply the three additions from web/nginx.conf.example, then
sudo nginx -t && sudo systemctl reload nginx
```

Requirements: Python 3.8+ (no third-party packages), Node 22.19+ / 24+ (uses `node:sqlite`).

## Why `readonly-guard.py` exists

Upstream `zusage.py` falls back to a **writable handle** when `mode=ro` fails
(`sqlite3.connect(str(DB_PATH))`, see `zusage.py:31-42`). That is fine for the desktop
edition, but here the sidecar lives alongside the ZCode service, which is writing the
same database — a second writer must not exist even in theory.

`readonly-guard.py` replaces `sqlite3.connect` before `zusage.py` gets control: any path
without `mode=ro` is rewritten to `file:<path>?mode=ro` with `uri=True`, and every
connection gets `PRAGMA query_only=1`. The systemd unit adds `ProtectHome=read-only` as a
second layer.

## Context window source (important)

The built-in model-catalog lookup points at a **hard-coded Windows path**
(`zusage.py:417`: `D:\ZCode\resources\model-providers\...`). On Linux it always misses and
falls back to the config default `128000`. For a 1M-window model that reports usage as
**8×** too high (248K shown as 194%).

Resolution order:

| Priority | Source | Note |
|---|---|---|
| 1 | page attribute `[data-usage-max]` | provided by ZCode itself, follows the model — most accurate |
| 2 | page aria-label `总量 N` / `of N` | the native context ring's label |
| 3 | server-side `provider_config.json` | sidecar reads `providerModelRules[].config.properties.contextWindow` |
| 4 | show `–` | never invent a number |

## Coexistence with the page

The bar is an overlay: it changes no ZCode source and no build output. It performs exactly
**one** write to the page DOM — setting `marginBottom` on the composer card so the bar has
room — and that write is adaptive: zero intrusion when there is already enough space, and
only the missing difference otherwise. On both real pages it never triggered, because
ZCode already reserves 28–85px below the composer while the bar is only 22–28px tall.

Everything else is read-only: no classes added, no `<html>` changes, no global CSS variables,
no React hooks. The breakpoint is `(max-width: 767.5px)`, identical to the in-tree
`useIsNarrowViewport.ts`.

The remote-control page carries no token in its URL (it arrives as a query parameter of the
`wsUrl` that the relay delivers through `workspace-bridge`), so `boot.js` hooks the
`WebSocket` constructor **only when no URL token is present**; the direct page supplies
`?token=` and is not hooked. The hook is a pure pass-through: same `prototype`, copied
static constants.

## Known limitations

- Self-hosted deployments only. The official cloud serves its own pages; there is no injection point.
- The snapshot endpoint reuses ZCode's own token. Multi-tenant setups need their own design.
- Resizing the remote-control page from wide to phone width returns to the task list — that is
  the official page's own layout-switch behaviour, unrelated to this plugin (verified with an A/B control).

## License

MIT, same as the repository root.
