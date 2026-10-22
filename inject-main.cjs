/* ZCode token 用量注入 loader v2（主进程）。
 * 由 patch_install.py 在 app.asar 入口尾部追加一行 import 引入。
 * 职责：1) 向每个窗口注入 overlay.js（mtime 变化热更新，免重启）；
 *       2) 触发式拉取：fs.watch 监听 db 目录，db.sqlite/-wal 一有写入（去抖 300ms）就
 *          查询并推送。限频 config.activity_min_ms（默认 1500ms），限频到点
 *          自动补拉，最后一笔写入不漏；完全空闲时零进程零轮询，仅兜底心跳
 *          （config.heartbeat_ms，默认 30s）防文件事件丢失（Node 官方文档：fs.watch
 *          事件不保证送达）。任何失败只打日志，不影响客户端。
 *       3) 每窗口当前会话（v6）：渲染端 workspace shell 把焦点会话 id 经 IPC
 *          "zcode:sync-active-task-session" 推给主进程（客户端自带通道，实测见 app bundle），
 *          这里追加监听记录 webContents.id → 会话 id：查询时把全部窗口的会话强制纳入快照，
 *          推送时给每个窗口注入 mine 字段 —— 多窗口各自显示自己的会话，不再猜"全局最近活跃"。 *
 *  v8：查询改常驻 python（zusage.py serve，stdin/stdout 行协议）——省每次 ~60-100ms 的
 *      解释器启动费；意外退出自动重启（30s 内连续 3 次判不稳定则回退一次性 spawn）；
 *      zusage.py mtime 变化自动重启（升级生效）；stdin EOF = 泵退出，常驻进程自清理。 */
"use strict";
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const { app, BrowserWindow, webContents } = require("electron");

const HERE = __dirname;   // 本 loader 被 asar 注入行按绝对 file:// URL import，__dirname 即真实目录（克隆到任意路径无需改代码）
const CONFIG = path.join(HERE, "config.json");
const OVERLAY = path.join(HERE, "overlay.js");
const ZUSAGE = path.join(HERE, "zusage.py");
const LOG = (...a) => console.error("[zusage]", ...a);

function readCfg() {
  const c = { python_path: "python", resident: true };
  try { Object.assign(c, JSON.parse(fs.readFileSync(CONFIG, "utf8"))); } catch (e) { }
  return c;
}

/* config.json 内容缓存：热更新定时器每 2s 触发，改为 stat mtime 判变化后才读文件 */
let cfgMtime = 0;
let cfgCache = null;
function readCfgCached() {
  let m = 0;
  try { m = fs.statSync(CONFIG).mtimeMs; } catch (e) { }
  if (!cfgCache || m !== cfgMtime) {
    cfgMtime = m;
    cfgCache = readCfg();
  }
  return cfgCache;
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

function pushOnce(wc, payload) {
  if (!isMainWindowState(wc) || wc.isDestroyed() || !payload || typeof payload !== "object") return;
  /* 按窗口注入 mine（本窗口焦点会话 id，可为空串 = 该窗口当前无会话）：
   * overlay 收到 mine 字段就优先显示它，不再多窗口共享同一个猜测。
   * 必须对象合并后序列化，禁止对 JSON 字符串做括号手术——v34 首版 slice 掐尾少补一个
   * 右括号，生成语法错误代码且失败只进静默 catch，全部窗口停在初始零值（实测翻车）。 */
  const obj = activeSid.has(wc.id) ? Object.assign({}, payload, { mine: activeSid.get(wc.id) || "" }) : payload;
  /* U+2028/2029 转义：JSON.stringify 会输出裸字符，放进 executeJavaScript 字符串字面量
   * 有语法风险（zusage.py 侧 ensure_ascii 的同款考量；会话标题可能含任意文本） */
  const js = `window.__zusageUpdate && window.__zusageUpdate(${JSON.stringify(obj).replace(/[\u2028\u2029]/g, (c) => c === "\u2028" ? "\\u2028" : "\\u2029")})`;
  wc.executeJavaScript(js, true).catch(() => { });
}

/* 每窗口焦点会话：客户端渲染端把 activeTaskId 经此通道同步给主进程（CUA PiP 所用通道），
 * 追加监听器不影响原有处理。值为 "" = 窗口已同步但当前无会话（新空会话/非任务页）；
 * 不在表里 = 该窗口从未同步（辅助窗口），推送时不带 mine，overlay 走本地键启发式。 */
const IPC_ACTIVE_SID = "zcode:sync-active-task-session";
const activeSid = new Map();   // webContents.id → 会话 id 或 ""
try {
  const { ipcMain } = require("electron");
  ipcMain.on(IPC_ACTIVE_SID, (e, sid) => {
    if (!e || !e.sender || e.sender.isDestroyed()) return;
    const s = typeof sid === "string" ? sid.trim() : "";
    const val = s && s.length <= 255 && /^[A-Za-z0-9_-]+$/.test(s) ? s : "";
    const prev = activeSid.get(e.sender.id);
    activeSid.set(e.sender.id, val);
    if (prev !== val) scheduleRetry(800);   // 会话切换/窗口就绪：尽快按新会话拉一次（限频器仍生效）
  });
} catch (e) { LOG("ipc hook failed:", e.message); }

/* ---------- 设置浮窗 IPC（v64） ----------
 * 渲染端要能读写 config.json 才谈得上「独立设置窗口」。只暴露白名单字段，
 * 其余键（含 asar_path 等安装位置）原样保留不回显、不可改——安装路径由 autopatch
 * 与 install.py 负责，让用户在悬浮条里改它只会把注入链搞断。
 * 写入走「读-合并白名单-原子替换」，避免半截文件让 readCfg 回退到默认值。 */
const CFG_EDITABLE = {
  activity_min_ms: "int", heartbeat_ms: "int", hot_reload: "bool", diag: "bool",
  resident: "bool", lang: "enum", context_window: "int",
  archive_days: "int", archive_hide: "bool", archive_mode: "enum",
};
const CFG_LANGS = ["zh", "en"];
const CFG_ARCHIVE_MODES = ["off", "hide", "fold"];

function coerce(key, val) {
  const kind = CFG_EDITABLE[key];
  if (!kind) return undefined;
  if (kind === "int") {
    const n = typeof val === "number" ? val : parseInt(String(val), 10);
    if (!Number.isFinite(n) || n < 0) return undefined;
    return key === "activity_min_ms" ? Math.max(300, n)
      : key === "heartbeat_ms" ? Math.max(10000, n)
        : key === "context_window" ? Math.max(1000, n)
          : Math.min(Math.max(n, 0), 3650);
  }
  if (kind === "bool") return val === true || val === "true" || val === 1;
  if (kind === "enum") {
    const s = String(val);
    if (key === "lang") return CFG_LANGS.includes(s) ? s : undefined;
    if (key === "archive_mode") return CFG_ARCHIVE_MODES.includes(s) ? s : undefined;
  }
  return undefined;
}

function cfgPublic() {
  const c = readCfg();
  const out = {};
  for (const k of Object.keys(CFG_EDITABLE)) if (c[k] !== undefined) out[k] = c[k];
  /* 未显式设置时给出运行时实际生效值，面板才不会显示空白 */
  if (out.activity_min_ms === undefined) out.activity_min_ms = 1500;
  if (out.heartbeat_ms === undefined) out.heartbeat_ms = 30000;
  if (out.context_window === undefined) out.context_window = 128000;
  if (out.lang === undefined) out.lang = "zh";
  if (out.archive_days === undefined) out.archive_days = 7;
  if (out.archive_mode === undefined) out.archive_mode = "fold";
  out.hot_reload = c.hot_reload !== false;
  out.diag = c.diag === true;
  out.resident = c.resident !== false;
  out.archive_hide = c.archive_hide === true;
  out.__python_path = c.python_path || "";
  return out;
}

function cfgWrite(patch) {
  let cur = {};
  try { cur = JSON.parse(fs.readFileSync(CONFIG, "utf8")); } catch (e) { cur = {}; }
  if (!cur || typeof cur !== "object") cur = {};
  let changed = 0;
  for (const k of Object.keys(patch || {})) {
    const v = coerce(k, patch[k]);
    if (v === undefined) continue;
    if (cur[k] === v) continue;
    cur[k] = v;
    changed++;
  }
  if (!changed) return { ok: true, changed: 0, cfg: cfgPublic() };
  const tmp = CONFIG + ".tmp";
  try {
    fs.writeFileSync(tmp, JSON.stringify(cur, null, 2), "utf8");
    fs.renameSync(tmp, CONFIG);   // 原子替换，读侧永不会看到半截文件
  } catch (e) {
    try { fs.unlinkSync(tmp); } catch (e2) { }
    LOG("config write failed:", e.message);
    return { ok: false, error: e.message };
  }
  cfgMtime = 0;          // 让 readCfgCached 立刻重读
  cfgCache = null;
  scheduleRetry(200);    // 立即按新配置拉一次
  return { ok: true, changed, cfg: cfgPublic() };
}

try {
  const { ipcMain } = require("electron");
  /* 同步通道：设置窗口首屏要立刻拿到值，异步会让表单先闪空值。
   * 只在设置窗口的 preload 里调用，调用频率极低（打开/保存各一次）。 */
  ipcMain.on("zusage:cfg:get-sync", (e) => {
    try { e.returnValue = { ok: true, ...settingsBridgePayload() }; }
    catch (err) { e.returnValue = { ok: false, error: String(err && err.message || err) }; }
  });
  ipcMain.on("zusage:cfg:set-sync", (e, patch) => {
    try { e.returnValue = cfgWrite(patch || {}); }
    catch (err) { e.returnValue = { ok: false, error: String(err && err.message || err) }; }
  });
  ipcMain.handle("zusage:cfg:get", () => {
    try { return { ok: true, ...settingsBridgePayload() }; }
    catch (e2) { return { ok: false, error: String(e2 && e2.message || e2) }; }
  });
  ipcMain.handle("zusage:cfg:set", (e, patch) => {
    try { return cfgWrite(patch || {}); }
    catch (e2) { return { ok: false, error: String(e2 && e2.message || e2) }; }
  });
} catch (e) { LOG("settings ipc failed:", e.message); }

/* 设置浮窗开关（渲染端发来 → 主进程开一个独立 BrowserWindow）。
 * 页面从 settings.html 读入后以 data: URL 加载，不额外分发静态文件。
 * 配置读写通过 preload 注入的 __zusageApi 直接走主进程函数（同一进程内的闭包），
 * 不需要再绕 IPC 通道。 */
let settingsWin = null;
function openSettingsWindow() {
  try {
    if (settingsWin && !settingsWin.isDestroyed()) {
      settingsWin.focus();
      /* 重开时把最新配置塞回去，避免看到陈旧值 */
      try { settingsWin.webContents.reload(); } catch (e) { }
      return { ok: true, reused: true };
    }
    const { BrowserWindow } = require("electron");
    const pagePath = path.join(HERE, "settings.html");
    if (!fs.existsSync(pagePath)) return { ok: false, error: "settings.html missing" };
    const html = fs.readFileSync(pagePath, "utf8");

    settingsWin = new BrowserWindow({
      width: 730, height: 780, minWidth: 560, minHeight: 460,
      title: "ZCode Token 用量 · 设置",
      backgroundColor: "#16181d",
      autoHideMenuBar: true,
      webPreferences: {
        preload: path.join(HERE, "settings-preload.cjs"),
        contextIsolation: false,
        nodeIntegration: false,
        sandbox: false,
      },
    });
    settingsWin.setMenuBarVisibility(false);
    settingsWin.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
    settingsWin.on("closed", () => { settingsWin = null; });
    return { ok: true };
  } catch (e) {
    LOG("open settings failed:", e.message);
    return { ok: false, error: e.message };
  }
}

/* preload 侧只做一件事：把主进程的实现挂到 window.__zusageApi。
 * 配置对象用结构化克隆传过去（preload 与主进程同进程，直接引用函数即可）。 */
function settingsBridgePayload() {
  return { cfg: cfgPublic(), runtime: HERE, path: CONFIG };
}
try {
  const { ipcMain } = require("electron");
  ipcMain.handle("zusage:settings:open", () => openSettingsWindow());
} catch (e) { LOG("settings open ipc failed:", e.message); }

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

/* v9 远程数据源：payload.recent 里带 remote 标记的会话 = zusage.py 经 SSH 从远端机取得。
 * 本地 fs.watch 对远端库写入无感，聚焦远端会话期间由泵按 remote.poll_ms 主动轮询
 * （默认 3s），切回本地会话即恢复纯事件触发、零进程零轮询。 */
const remoteSids = new Set();
let lastHadRemote = false;
function remotePollMs() {
  const r = readCfgCached().remote;
  return Math.max(1000, (r && r.poll_ms | 0) || 3000);
}

let busy = false;
let pushCount = 0;
let lastSpawn = 0;    // 上次发起查询时刻（activity_min_ms 限频用）
let lastStamp = 0;    // 上次拉取时的 db mtime（去重：无新写入不拉）
let lastWants = "";   // 上次查询用的会话 id 集合（变化即拉，防"会话切了但 db 没写入"滞留旧数据）
let lastPayload = null;   // v63：最近一次快照缓存，新窗口/重载后立即补推，免等下一次写入

/* 诊断写盘：定位问题用，稳定运行时应关闭。
 * 老实现无条件每 ~15 次推送执行一次 executeJavaScript 取 diag 再同步写盘（每窗口一个文件），
 * 长会话里是持续的额外 IPC + JSON 序列化 + 同步磁盘 IO。默认关闭，需要时 config.diag=true 打开。 */
function diagEnabled() {
  return readCfgCached().diag === true;
}

function handlePayload(payload) {
  if (!payload || typeof payload !== "object") return;
  lastPayload = payload;   // 缓存供新窗口/重载即时补推（v63）
  remoteSids.clear();
  for (const r of (payload.recent || [])) if (r && r.remote && r.sid) remoteSids.add(r.sid);
  for (const wc of webContents.getAllWebContents()) pushOnce(wc, payload);
  // 定位诊断（默认关闭）：每 ~15 次拉取收集每个窗口各自的 __zusageDiag 写 diag-<n>.json
  if (diagEnabled() && ++pushCount % 15 === 1) {
    let idx = 0;
    for (const wc of webContents.getAllWebContents()) {
      if (!isMainWindowState(wc) || wc.isDestroyed()) continue;
      const file = path.join(HERE, "diag-" + idx + ".json");
      idx++;
      wc.executeJavaScript("JSON.stringify(window.__zusageDiag||null)", true)
        .then((s) => {
          if (s && s !== "null") {
            try {
              const obj = JSON.parse(s);
              obj.pump = {
                version: 11,
                resident: !!pyRes,
                activeMap: Array.from(activeSid.entries()).slice(0, 12),
                wants: lastWants,
              };
              fs.writeFileSync(file, JSON.stringify(obj));
            } catch (e2) {
              try { fs.writeFileSync(file, s); } catch (e) { }
            }
          }
        })
        .catch(() => { });
    }
  }
}

/* ---------- 常驻查询进程（v8）：zusage.py serve，stdin 请求行 / stdout 响应行 ---------- */
let pyRes = null;        // { proc, buf, waiters: [fn], born }
let pyFail = 0;          // 启动 30s 内退出次数（>=3 判不稳定，回退一次性 spawn）
let pyBackoff = 1000;
let pyRestarting = false;   // 主动 kill（升级/超时）标记，close 时不计失败

function startResident() {
  if (pyRes) return;
  const proc = spawn(readCfg().python_path, [path.join(HERE, "zusage.py"), "serve"], { windowsHide: true });
  const state = { proc, buf: "", waiters: [], born: Date.now() };
  pyRes = state;
  LOG("resident python starting");
  proc.stdout.on("data", (d) => {
    state.buf += d.toString("utf8");
    let i;
    while ((i = state.buf.indexOf("\n")) >= 0) {
      const line = state.buf.slice(0, i);
      state.buf = state.buf.slice(i + 1);
      const w = state.waiters.shift();
      if (!w) continue;
      let payload = null;
      try { payload = JSON.parse(line); } catch (e) { }
      w(payload);
    }
  });
  proc.stderr.on("data", () => { });
  proc.on("close", () => {
    if (pyRes === state) pyRes = null;
    const pending = state.waiters;
    state.waiters = [];
    pending.forEach((w) => w(null));   // 挂起请求失败收场，泵侧等下一笔写入/心跳补拉
    if (pyRestarting) { pyRestarting = false; startResident(); return; }
    if (Date.now() - state.born < 30000) {   // 早期崩溃：退避重启，3 次判不稳定
      pyFail++;
      if (pyFail >= 3) { LOG("resident unstable (3 early exits), fallback to one-shot spawn"); return; }
      setTimeout(startResident, pyBackoff);
      pyBackoff = Math.min(pyBackoff * 2, 30000);
    } else {   // 长跑后退出（升级/外部误杀）：立即重启，不计失败
      pyFail = 0;
      pyBackoff = 1000;
      startResident();
    }
  });
}

function killResidentForUpgrade() {
  if (!pyRes) return;
  pyRestarting = true;
  try { pyRes.proc.kill(); } catch (e) { }
}

/* 统一查询入口：resident 可用走行协议，否则一次性 spawn（config.resident=false 或判不稳定） */
function runQuery(wants) {
  busy = true;
  const done = (payload) => {
    busy = false;
    if (payload) handlePayload(payload);
    if (lastHadRemote) scheduleRetry(remotePollMs());   // 远端会话聚焦期间维持轮询链
    // 失败不原地重试：下一笔 db 写入/心跳会再拉，最多滞后一个心跳
  };
  if (readCfgCached().resident !== false && pyFail < 3) {
    if (!pyRes) startResident();
    if (pyRes && pyRes.proc.stdin.writable) {
      const state = pyRes;
      const timer = setTimeout(() => {
        LOG("resident query timeout, restarting it");
        pyRestarting = true;
        try { state.proc.kill(); } catch (e) { }
        done(null);
      }, 15000);
      state.waiters.push((payload) => { clearTimeout(timer); done(payload); });
      try { state.proc.stdin.write((wants || "") + "\n"); } catch (e) { }
      return;
    }
  }
  legacySpawn(wants);
}

/* 回退路径（一次性 spawn）：常驻不可用时走 */
function legacySpawn(wants) {
  /* wants：各窗口当前会话 id（逗号分隔）。全部强制纳入快照，共享池里包含每个窗口
   * 自己的会话；zusage.py 逐个白名单校验。泵侧整体格式白名单防注入。 */
  const args = [path.join(HERE, "zusage.py"), "json"];
  if (wants && /^[A-Za-z0-9_,-]{1,600}$/.test(wants)) args.push(wants);
  const py = spawn(readCfg().python_path, args, { windowsHide: true });
  let out = "";
  /* 兜底：查询进程 15 秒不退出按挂死杀掉，close 事件照常触发，busy 不会永久卡住 */
  const killTimer = setTimeout(() => { LOG("query timeout, killing"); try { py.kill(); } catch (e) { } }, 15000);
  py.stdout.on("data", (d) => { out += d; });
  py.on("error", (e) => { busy = false; LOG("spawn failed:", e.message); });
  py.on("close", () => {
    clearTimeout(killTimer);
    busy = false;
    if (lastHadRemote) scheduleRetry(remotePollMs());
    if (out.trim()) {
      let payload;
      try { payload = JSON.parse(out); } catch (e) { payload = null; }
      if (payload) handlePayload(payload);
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
function maybeSpawn(opts) {
  if (busy) { scheduleRetry(300); return; }
  const st = dbStamp();
  const cfg = readCfgCached();
  const wait = Math.max(300, cfg.activity_min_ms | 0 || 1500);
  const firstRun = !!(opts && opts.firstRun);
  /* 先取各窗口的"当前会话 id"：主进程 IPC 映射（应用自己上报的焦点会话，v6）优先，
   * overlay 从 localStorage 键读出的 __zusageWantSid 兜底；无窗口时直接短路（空闲零进程的
   * 设计意图）；executeJavaScript 挂 1s 超时，防渲染进程卡死导致泵永不 settle。
   * v8：全部窗口都已在 IPC 映射表里（含"已同步但无会话"的空串）时跳过整轮 eval ——
   * mine 模式下 WantSid 已非主数据源，省掉活跃期每 1.5s × N 窗口的页面求值。 */
  const wcs = webContents.getAllWebContents().filter((wc) => isMainWindowState(wc) && !wc.isDestroyed());
  if (!wcs.length) return;
  /* v63 启动快速路径：首轮若每个窗口都已有 IPC 映射，说明 focus 会话已知，
   * 不必等页面 eval（最长 1s 超时）——直接按映射发起查询，条更早出数。 */
  const allKnown = wcs.every((wc) => activeSid.has(wc.id));
  if (firstRun && allKnown) {
    const wants0 = [];
    wcs.forEach((wc) => {
      const s = activeSid.get(wc.id) || "";
      if (s && /^[A-Za-z0-9_-]{1,80}$/.test(s) && !wants0.includes(s)) wants0.push(s);
    });
    lastWants = wants0.join(",");
    lastSpawn = Date.now();
    if (st) lastStamp = st;
    runQuery(lastWants);
    return;
  }
  const withTimeout = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(() => r(""), ms))]);
  /* 各窗口的 pane 会话 id（v10，__zusageWantSids 数组，分屏多实例状态条用）+ 旧单值兜底。
   * v8 曾在 mine 模式跳过页面求值；分屏后每 pane 有自己的会话，必须读回数组才能全部纳入快照。 */
  const readWantsJs = "(function(){var a=window.__zusageWantSids;if(!a||!a.length){a=window.__zusageWantSid?[window.__zusageWantSid]:[]}return JSON.stringify(a)})()";
  Promise.all(wcs.map((wc) => withTimeout(wc.executeJavaScript(readWantsJs, true).catch(() => "[]"), 1000)))
    .then((vals) => {
      /* 汇总全部窗口的全部 pane 会话（去重）：mine（窗口焦点会话）优先，pane 会话随后 */
      const wants = [];
      const push = (sid) => { if (sid && /^[A-Za-z0-9_-]{1,80}$/.test(sid) && !wants.includes(sid)) wants.push(sid); };
      wcs.forEach((wc, i) => {
        if (activeSid.has(wc.id)) push(activeSid.get(wc.id) || "");
        let arr = [];
        try { arr = JSON.parse(vals[i] || "[]"); } catch (e) { }
        if (Array.isArray(arr)) arr.forEach(push);
      });
      const key = wants.join(",");
      /* v9：聚焦 SSH 远程会话时本地 db 不写入（st 恒定），短路径放行、改按 remote 轮询间隔 */
      const remoteActive = wants.some((s) => remoteSids.has(s));
      if (key === lastWants && st && st === lastStamp && !remoteActive) return;   // 会话与数据都没变：零进程
      const now = Date.now();
      const effWait = remoteActive ? remotePollMs() : wait;
      if (now - lastSpawn < effWait) { scheduleRetry(effWait - (now - lastSpawn) + 50); return; }
      lastWants = key;
      lastHadRemote = remoteActive;
      if (st) lastStamp = st;
      lastSpawn = now;
      runQuery(key);
    })
    .catch(() => { });
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

/* overlay / zusage.py 热更新检查（stat mtime，开销可忽略）；config.hot_reload=false 可关闭
 * （发布/稳定形态：改文件需重启 ZCode 才生效），开关改动随下次心跳生效。
 * zusage.py 变化时重启常驻进程（serve 进程驻内存的是旧代码）；顺带 stat config mtime。
 * v63：默认 5s 一轮（原 2s），热更新只服务于开发调试；config.hot_reload=true 时维持 2s 快检。 */
let overlayMtimeKnown = false;
let zusageMtime = 0;
function hotCheck() {
  const cfg = readCfgCached();
  if (cfg.hot_reload === false) return;
  hotReloadIfChanged();
  try {
    const m = fs.statSync(ZUSAGE).mtimeMs;
    if (overlayMtimeKnown && m !== zusageMtime && pyRes) {
      LOG("zusage.py changed, restarting resident python");
      killResidentForUpgrade();
    }
    zusageMtime = m;
    overlayMtimeKnown = true;
  } catch (e) { }
}
setInterval(hotCheck, readCfg().hot_reload === true ? 2000 : 5000);
setInterval(maybeSpawn, Math.max(10000, readCfg().heartbeat_ms | 0 || 30000));   // 兜底心跳：防文件事件丢失
watchDb();
/* v63：常驻解析器提前预热（省首帧 ~60-100ms 解释器启动），再走启动快速路径查询。
 * 预热失败无副作用：runQuery 内部会按需启动/回退一次性 spawn。 */
startResident();
maybeSpawn({ firstRun: true });   // 启动先拉一次

/* ---------- 渲染端 → 主进程 的请求泵（v64 设置窗口用） ----------
 * overlay 没有 ipcRenderer（contextIsolation），又不该为此常开一个定时器。
 * 做法：渲染端把请求压进 window.__zusageReq 数组，主进程在一次 executeJavaScript
 * 里原子取走（数组清空）并回填 window.__zusageRes[id]。只在有请求时才有一次往返，
 * 空闲时由下面的 400ms 轻量轮询承担（读取一个可能不存在的数组，成本近乎为零）。 */
let reqTimer = 0;
async function pumpRequests() {
  const wcs = webContents.getAllWebContents().filter((wc) => isMainWindowState(wc) && !wc.isDestroyed());
  for (const wc of wcs) {
    let reqs = null;
    try {
      /* 原子取走队列：读 + 清空在同一个脚本里完成，避免重复处理 */
      reqs = await wc.executeJavaScript(
        "(function(){var a=window.__zusageReq;if(!a||!a.length)return [];window.__zusageReq=[];return a})()",
        true
      );
    } catch (e) { continue; }
    if (!Array.isArray(reqs) || !reqs.length) continue;
    const results = {};
    for (const r of reqs) {
      if (!r || typeof r !== "object") continue;
      const id = String(r.id || "");
      if (!id) continue;
      try {
        if (r.type === "cfg:get") results[id] = { ok: true, cfg: cfgPublic(), runtime: HERE };
        else if (r.type === "cfg:set") results[id] = cfgWrite(r.patch || {});
        else if (r.type === "settings:open") results[id] = openSettingsWindow();
        else results[id] = { ok: false, error: "unknown request " + String(r.type) };
      } catch (e) {
        results[id] = { ok: false, error: String(e && e.message || e) };
      }
    }
    if (Object.keys(results).length) {
      const js = "window.__zusageRes=Object.assign(window.__zusageRes||{},"
        + JSON.stringify(results) + ");";
      wc.executeJavaScript(js, true).catch(() => { });
    }
  }
}
function startReqPump() {
  if (reqTimer) return;
  reqTimer = setInterval(() => { pumpRequests().catch(() => { }); }, 400);
}
startReqPump();

function injectOverlay(wc) {
  if (!overlaySrc || !isMainWindowState(wc)) return;
  /* v64：overlay 跑在 contextIsolation 的渲染进程里，拿不到 ipcRenderer，
   * 也无法自建窗口。设置面板的数据读写走上面的请求泵（window.__zusageReq/Res）。
   * v63 首屏快速路径保留：overlay 装好后立刻补推最近一次快照。 */
  wc.executeJavaScript(overlaySrc, true)
    .then(() => {
      if (lastPayload) pushOnce(wc, lastPayload);
    })
    .catch(() => { });
}

const hook = (wc) => {
  if (!isMainWindowState(wc)) return;
  /* v63：did-finish-load 要等页面所有资源（图片/字体/懒加载 chunk）结束才触发，
   * 是"重启后条要等好几秒才出现"的主因。dom-ready 在 DOM 可用时就到，
   * overlay 自己会轮询等输入框挂载，早注入没有副作用；did-finish-load 保留兜底
   * （SPA 内部导航后重新装载的情况）。 */
  wc.on("dom-ready", () => injectOverlay(wc));
  wc.on("did-finish-load", () => injectOverlay(wc));
  if (wc.isLoading()) return;
  injectOverlay(wc);
};
app.on("web-contents-created", (e, wc) => hook(wc));
for (const wc of webContents.getAllWebContents()) hook(wc);

LOG("injected v11 (v63 perf: dom-ready inject, snapshot warm-push, resident prewarm, diag off by default, 5s hot-check) resident query mode + remote SSH datasource (per-window active session via IPC, fs.watch db dir, remote poll_ms while remote session focused, zusage serve + one-shot fallback, activity_min_ms/heartbeat_ms, query timeout 15s)");
