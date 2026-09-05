/* ZCode token 用量注入 loader v2（主进程）。
 * 由 patch_install.py 在 app.asar 入口尾部追加一行 import 引入。
 * 职责：1) 向每个窗口注入 overlay.js（mtime 变化热更新，免重启）；
 *       2) 触发式拉取：fs.watch 监听 db 目录，db.sqlite/-wal 一有写入（去抖 300ms）就
 *          spawn python 查询并推送。限频 config.activity_min_ms（默认 1500ms），限频到点
 *          自动补拉，最后一笔写入不漏；完全空闲时零进程零轮询，仅兜底心跳
 *          （config.heartbeat_ms，默认 30s）防文件事件丢失。任何失败只打日志，不影响客户端。 */
"use strict";
const path = require("path");
const fs = require("fs");
const { app, BrowserWindow, webContents } = require("electron");

const HERE = __dirname;   // 本 loader 被 asar 注入行按绝对 file:// URL import，__dirname 即真实目录（克隆到任意路径无需改代码）
const CONFIG = path.join(HERE, "config.json");
const OVERLAY = path.join(HERE, "overlay.js");
const LOG = (...a) => console.error("[zusage]", ...a);

function readCfg() {
  const c = { python_path: "python" };
  try { Object.assign(c, JSON.parse(fs.readFileSync(CONFIG, "utf8"))); } catch (e) { }
  return c;
}

/* overlay 注入源 + 热更新：mtime 变了就重注入（移除旧条+复位守卫）。
 * 载入前用 new Function 校验语法，避免把写到一半的文件载入页面。 */
let overlaySrc = "";
let overlayMtime = 0;
try {
  overlaySrc = fs.readFileSync(OVERLAY, "utf8");
  overlayMtime = fs.statSync(OVERLAY).mtimeMs;
} catch (e) { LOG("overlay.js missing:", e.message); }

function hotReloadIfChanged() {
  let m = 0, src = "";
  try {
    m = fs.statSync(OVERLAY).mtimeMs;
    src = fs.readFileSync(OVERLAY, "utf8");
  } catch (e) { return; }
  if (m === overlayMtime) return;
  try { new Function(src); } catch (e) { LOG("overlay.js syntax not ready, skip:", e.message); return; }
  overlayMtime = m;
  overlaySrc = src;
  LOG("overlay.js changed, hot-reloading");
  for (const wc of webContents.getAllWebContents()) {
    if (!isMainWindowState(wc) || wc.isDestroyed()) continue;
    wc.executeJavaScript(
      '(function(){var b=document.getElementById("zusage-bar");if(b)b.remove();var t=document.getElementById("zusage-tip");if(t)t.remove();window.__zusageOverlay=false;})()', true
    ).catch(() => { });
    injectOverlay(wc);
  }
}

function isMainWindowState(wc) {
  try { return wc.getType() === "window"; } catch (e) { return false; }
}

function pushOnce(wc, json) {
  if (!isMainWindowState(wc) || wc.isDestroyed()) return;
  wc.executeJavaScript(`window.__zusageUpdate && window.__zusageUpdate(${json})`, true)
    .catch(() => { });
}

/* db 活动戳：db.sqlite / -wal / -shm 的最新 mtime。WAL 模式下写入主要落在 -wal。 */
let DB_DIR = null;
function dbDir() {
  if (!DB_DIR) DB_DIR = path.join(app.getPath("home"), ".zcode", "cli", "db");
  return DB_DIR;
}
function dbStamp() {
  let m = 0;
  const dir = dbDir();
  for (const f of ["db.sqlite", "db.sqlite-wal", "db.sqlite-shm"]) {
    try { m = Math.max(m, fs.statSync(path.join(dir, f)).mtimeMs); } catch (e) { }
  }
  return m;
}

let busy = false;
let pushCount = 0;
let lastSpawn = 0;    // 上次 spawn 时刻（activity_min_ms 限频用）
let lastStamp = 0;    // 上次拉取时的 db mtime（去重：无新写入不拉）

function spawnQuery(wantSid) {
  busy = true;
  const { spawn } = require("child_process");
  /* wantSid：overlay 上报的当前会话 id（localStorage 键值），强制纳入快照——
   * 不在 recent 池里的会话也能显示自己的数据（v32）。格式白名单防注入。 */
  const args = [path.join(HERE, "zusage.py"), "json"];
  if (wantSid && /^[A-Za-z0-9_-]+$/.test(wantSid)) args.push(wantSid);
  const py = spawn(readCfg().python_path, args, { windowsHide: true });
  let out = "";
  /* 兜底：查询进程 15 秒不退出按挂死杀掉，close 事件照常触发，busy 不会永久卡住 */
  const killTimer = setTimeout(() => { LOG("query timeout, killing"); try { py.kill(); } catch (e) { } }, 15000);
  py.stdout.on("data", (d) => { out += d; });
  py.on("error", (e) => { busy = false; LOG("spawn failed:", e.message); });
  py.on("close", () => {
    clearTimeout(killTimer);
    busy = false;
    if (out.trim()) {
      let payload;
      try { payload = JSON.parse(out); } catch (e) { payload = null; }
      if (payload) {
        const json = JSON.stringify(payload);
        for (const wc of webContents.getAllWebContents()) pushOnce(wc, json);
      }
    }
    // 定位诊断：每 ~15 次拉取收集每个窗口各自的 __zusageDiag，写 diag-<n>.json（多窗口互不覆盖）
    if (++pushCount % 15 === 1) {
      let idx = 0;
      for (const wc of webContents.getAllWebContents()) {
        if (!isMainWindowState(wc) || wc.isDestroyed()) continue;
        const file = path.join(HERE, "diag-" + idx + ".json");
        idx++;
        wc.executeJavaScript("JSON.stringify(window.__zusageDiag||null)", true)
          .then((s) => {
            if (s && s !== "null") {
              try { fs.writeFileSync(file, s); } catch (e) { }
            }
          })
          .catch(() => { });
      }
    }
  });
}

/* 触发式拉取：db 有新写入（mtime 变化）才 spawn 查询；限频不满足时安排补拉，
 * 保证限频窗口结束后最后一笔写入的数据一定被取到。 */
let retryTimer = 0;
function scheduleRetry(ms) {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(maybeSpawn, ms);
}
function maybeSpawn() {
  if (busy) { scheduleRetry(300); return; }
  const st = dbStamp();
  if (st && st === lastStamp) return;   // 没有新写入，什么都不用做
  const cfg = readCfg();
  const wait = Math.max(300, cfg.activity_min_ms | 0 || 1500);
  const now = Date.now();
  if (now - lastSpawn < wait) { scheduleRetry(wait - (now - lastSpawn) + 50); return; }
  if (st) lastStamp = st;
  lastSpawn = now;
  /* 先向各窗口要"当前会话 id"（overlay 从 localStorage 键读出并挂在 __zusageWantSid），
   * 再带参查询；无窗口时直接短路（空闲零进程的设计意图）；executeJavaScript 挂 1s 超时，
   * 防渲染进程卡死导致泵永不 settle */
  const wcs = webContents.getAllWebContents().filter((wc) => isMainWindowState(wc) && !wc.isDestroyed());
  if (!wcs.length) return;
  const withTimeout = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(() => r(""), ms))]);
  Promise.all(wcs.map((wc) => withTimeout(wc.executeJavaScript("(window.__zusageWantSid||'')", true).catch(() => ""), 1000)))
    .then((vals) => spawnQuery(vals.find((v) => v) || ""))
    .catch(() => spawnQuery(""));
}

/* 监听 db 目录：WAL/主库一有写入立刻（去抖 300ms）触发拉取；完全空闲时零进程零轮询 */
let debounceTimer = 0;
let dbWatcher = null;
function watchDb() {
  if (dbWatcher) return;
  try {
    dbWatcher = fs.watch(dbDir(), (ev, f) => {
      if (f && !/db\.sqlite/.test(f)) return;
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(maybeSpawn, 300);
    });
    dbWatcher.on("error", () => {
      LOG("db watcher error, re-arm in 5s");
      try { dbWatcher.close(); } catch (e) { }
      dbWatcher = null;
      setTimeout(watchDb, 5000);
    });
    LOG("watching", dbDir());
  } catch (e) {
    LOG("fs.watch failed, retry in 5s:", e.message);
    setTimeout(watchDb, 5000);
  }
}

/* overlay 热更新检查（一次 stat，开销可忽略）；config.hot_reload=false 可关闭
 * （发布/稳定形态：改 overlay.js 需重启 ZCode 才生效），开关改动随下次心跳生效。 */
setInterval(() => { if (readCfg().hot_reload !== false) hotReloadIfChanged(); }, 2000);
setInterval(maybeSpawn, Math.max(10000, readCfg().heartbeat_ms | 0 || 30000));   // 兜底心跳：防文件事件丢失
watchDb();
maybeSpawn();   // 启动先拉一次

function injectOverlay(wc) {
  if (!overlaySrc || !isMainWindowState(wc)) return;
  wc.executeJavaScript(overlaySrc, true).catch(() => { });
}

const hook = (wc) => {
  if (!isMainWindowState(wc)) return;
  wc.on("did-finish-load", () => injectOverlay(wc));
  if (wc.isLoading()) return;
  injectOverlay(wc);
};
app.on("web-contents-created", (e, wc) => hook(wc));
for (const wc of webContents.getAllWebContents()) hook(wc);

LOG("injected v5, trigger mode (fs.watch db dir, activity_min_ms/heartbeat_ms, query timeout 15s)");
