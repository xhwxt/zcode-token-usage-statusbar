#!/usr/bin/env node
/**
 * zusage 旁挂服务 —— 把 ZCode Token 用量状态栏接进自托管 web（直连页 + 远控页）。
 *
 * 为什么是旁挂而不是改 ZCode：
 *   直连页 HTML 由 server 每次请求从磁盘读（packages/server/src/http.ts:464），远控页由
 *   relay 伺服；两者都可以在 nginx 层注入一行 <script>。于是整套功能只需要
 *   「一个只读旁挂进程 + nginx 三行」，不碰 ZCode 源码、不重建、不重启 zcode-web / relay。
 *
 * 数据链：
 *   常驻 python（zusage.py serve，经 readonly-guard.py 强制只读）
 *     → 行协议（stdin 一行 sid 列表，stdout 一行 JSON 快照）
 *     → 本服务补 mine / 修正 context_window / 抹掉敏感字段
 *     → 浏览器 overlay
 *
 * 只读保证（三重）：
 *   1. readonly-guard.py 把 sqlite3.connect 强制成 mode=ro + PRAGMA query_only=1
 *   2. 本进程自己不开任何数据库连接
 *   3. 全程只 SELECT（由 zusage.py 决定）
 *
 * 端口 3033，只监听 127.0.0.1，由 nginx 反代到 /zusage/。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

/* ---------- 配置（全部可用环境变量覆盖，不落盘任何密钥） ---------- */
const PORT = Number(process.env.ZUSAGE_PORT || 3033);
const HOST = process.env.ZUSAGE_HOST || '127.0.0.1';
/** 与 ZCode server 同一个 token（zcode-web.service 的 ZCODE_SERVER_TOKEN）。
 *  优先取进程环境；没有则读数据目录下的 .zcode-token（ZCode 自己已有的那份，权限 600）
 *  ——这样 systemd unit 里不必再抄一份密钥。 */
/** ZCode 数据目录：优先用 ZCode 自己的环境变量派生，避免把部署路径写死。
 *  ZCODE_DATA_BASE_DIR 是 ZCode server 自己就在用的变量（zcode-web.service 里设的那个）。 */
const DATA_DIR =
  (process.env.ZCODE_DATA_BASE_DIR || process.env.ZUSAGE_DATA_DIR || '/opt/zcode-data').trim();

const TOKEN = resolveToken();
function resolveToken() {
  const env = (process.env.ZCODE_SERVER_TOKEN || process.env.ZUSAGE_TOKEN || '').trim();
  if (env) return env;
  const file = process.env.ZUSAGE_TOKEN_FILE || join(DATA_DIR, '.zcode-token');
  try {
    return readFileSync(file, 'utf8').trim();
  } catch {
    return '';
  }
}
/** 插件目录（只读引用它的 zusage.py，不改一行）。
 *  两种布局都自定位：仓库内 web/ 的上一级就是仓库根；部署时 web/ 常与仓库目录并列。 */
const PLUGIN_DIR = process.env.ZUSAGE_PLUGIN_DIR || resolvePluginDir();
function resolvePluginDir() {
  const candidates = [resolve(HERE, '..'), resolve(HERE, '../zcode-token-usage-statusbar')];
  for (const dir of candidates) {
    if (existsSync(join(dir, 'zusage.py'))) return dir;
  }
  return candidates[0];
}
const PY = process.env.ZUSAGE_PYTHON || 'python3';
/** ZCode 的模型目录：contextWindow 的唯一正确来源（不是插件兜底的 128000） */

const PROVIDER_CONFIGS = (
  process.env.ZUSAGE_PROVIDER_CONFIGS ||
  [join(DATA_DIR, '.zcode/provider_config.json'), join(DATA_DIR, '.zcode/v2/provider_config.json')].join(',')
)
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const SNAPSHOT_TTL_MS = Number(process.env.ZUSAGE_SNAPSHOT_TTL_MS || 700);
const PY_TIMEOUT_MS = Number(process.env.ZUSAGE_PY_TIMEOUT_MS || 8000);

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

/* ---------- 模型目录 → contextWindow 查表 ---------- */
let ctxCatalog = new Map(); // modelId -> contextWindow
let ctxCatalogAt = 0;

function loadContextCatalog() {
  const now = Date.now();
  if (now - ctxCatalogAt < 60_000 && ctxCatalog.size) return ctxCatalog;
  const map = new Map();
  for (const file of PROVIDER_CONFIGS) {
    try {
      if (!existsSync(file)) continue;
      const doc = JSON.parse(readFileSync(file, 'utf8'));
      const rules = doc?.config?.modelConfigRules?.providerModelRules;
      if (!Array.isArray(rules)) continue;
      for (const rule of rules) {
        const modelId = rule?.modelId;
        const ctx = rule?.config?.properties?.contextWindow;
        if (typeof modelId === 'string' && Number.isFinite(ctx) && ctx > 0) {
          map.set(modelId, ctx);
        }
      }
    } catch (e) {
      log('provider config 读取失败', file, e.message);
    }
  }
  if (map.size) {
    ctxCatalog = map;
    ctxCatalogAt = now;
  }
  return ctxCatalog;
}

/* ---------- 常驻 python（行协议），失败自动重启 ---------- */
let child = null;
let childGen = 0;
let unstable = false;
const crashStamps = [];
let queue = Promise.resolve(); // 串行化：行协议不能并发写

function startChild() {
  const gen = ++childGen;
  const script = join(PLUGIN_DIR, 'zusage.py');
  if (!existsSync(script)) {
    log('zusage.py 不存在:', script);
    return null;
  }
  const proc = spawn(PY, [join(HERE, 'readonly-guard.py'), script, 'serve'], {
    cwd: PLUGIN_DIR,
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env },
  });
  let buf = '';
  const waiters = [];
  proc.stdout.setEncoding('utf8');
  proc.stdout.on('data', (chunk) => {
    buf += chunk;
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl);
      buf = buf.slice(nl + 1);
      const w = waiters.shift();
      if (w) w.resolve(line);
    }
  });
  proc.stderr.setEncoding('utf8');
  proc.stderr.on('data', (d) => log('[python]', String(d).trim().slice(0, 300)));
  proc.on('exit', (code) => {
    for (const w of waiters.splice(0)) w.reject(new Error('python exited ' + code));
    if (gen !== childGen) return;
    child = null;
    const now = Date.now();
    crashStamps.push(now);
    while (crashStamps.length && now - crashStamps[0] > 30_000) crashStamps.shift();
    if (crashStamps.length >= 3) {
      unstable = true;
      log('常驻 python 30s 内崩溃 3 次 → 回退一次性模式');
      return;
    }
    log('常驻 python 退出 code=', code, '→ 2s 后重启');
    setTimeout(() => {
      if (!child) child = startChild();
    }, 2000).unref?.();
  });
  const api = {
    proc,
    ask(payload) {
      return new Promise((res, rej) => {
        const timer = setTimeout(() => {
          const i = waiters.indexOf(entry);
          if (i >= 0) waiters.splice(i, 1);
          rej(new Error('python 查询超时'));
        }, PY_TIMEOUT_MS);
        const entry = {
          resolve: (v) => {
            clearTimeout(timer);
            res(v);
          },
          reject: (e) => {
            clearTimeout(timer);
            rej(e);
          },
        };
        waiters.push(entry);
        proc.stdin.write(payload + '\n');
      });
    },
    dispose() {
      try {
        proc.stdin.end();
      } catch {}
      try {
        proc.kill();
      } catch {}
    },
  };
  return api;
}

async function askResident(sids) {
  if (!child) child = startChild();
  if (!child) throw new Error('python 未就绪');
  return child.ask(sids);
}

function askOneShot(sids) {
  return new Promise((res, rej) => {
    const script = join(PLUGIN_DIR, 'zusage.py');
    const proc = spawn(PY, [join(HERE, 'readonly-guard.py'), script, 'json', sids], {
      cwd: PLUGIN_DIR,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    let err = '';
    proc.stdout.on('data', (d) => (out += d));
    proc.stderr.on('data', (d) => (err += d));
    const timer = setTimeout(() => {
      try {
        proc.kill();
      } catch {}
      rej(new Error('python 一次性查询超时'));
    }, PY_TIMEOUT_MS);
    proc.on('exit', (code) => {
      clearTimeout(timer);
      if (code !== 0) return rej(new Error('python 退出 ' + code + ' ' + err.slice(0, 200)));
      res(out.trim());
    });
  });
}

/* ---------- 快照：TTL 缓存 + 并发合流 ---------- */
const cache = new Map(); // sids -> { at, body }
const inflight = new Map();

function sanitize(raw) {
  // 抹掉会泄露会话内容的字段；只留数字与标识
  const out = raw;
  const strip = (o) => {
    if (o && typeof o === 'object') delete o.title;
  };
  strip(out.session);
  if (Array.isArray(out.recent)) out.recent.forEach(strip);
  if (out.sub && Array.isArray(out.sub.list)) out.sub.list.forEach(strip);
  return out;
}

/**
 * 修正每个会话各自的 context_window。
 *
 * 为什么必须逐条修：zusage.py 的 context_window 来自 cfg["context_window"]（默认 128000）。
 * 插件的模型目录查表 CATALOG_PATH 是**硬编码的 Windows 路径**
 * （zusage.py:417 `D:\ZCode\resources\model-providers\...`），在 Linux 上永远落空 →
 * 落到 128000。而本机 deepseek-v4.1-flash 的真实窗口是 1000000，不修就会把
 * 248166 显示成 194%。
 *
 * 会话池里每条都有自己的 last.model（顶层 last 永远是「最近会话」的，见 withMine），
 * 所以逐条按自己的模型查。
 */
function applyContextWindow(payload) {
  const catalog = loadContextCatalog();
  const fix = (h) => {
    if (!h || typeof h !== 'object') return;
    const model = h.last && h.last.model;
    const ctx = model ? catalog.get(model) : null;
    if (ctx) {
      h.context_window = ctx;
      h.context_auto = true;
    }
  };
  fix(payload);
  fix(payload.session);
  if (Array.isArray(payload.recent)) payload.recent.forEach(fix);
  return payload;
}

/** 池里没有该会话时用的零值壳（与 zusage.py 的空快照字段对齐） */
function stubSession(sid) {
  return {
    sid: sid || '', active: false, turns: 0, requests: 0,
    input: 0, output: 0, reasoning: 0, cache_read: 0, cache_write: 0,
    total: 0, tool_calls: 0, retries: 0, ctx: 0, updated: '',
    last_turn: {
      requests: 0, retries: 0, tool_calls: 0, tool_errors: 0, input: 0, output: 0,
      reasoning: 0, cache_read: 0, cache_write: 0, total: 0, duration_ms: 0, ttft_ms: 0,
    },
    last: { duration_ms: 0, ttft_ms: 0, model: '', tps: 0 },
    code: { add: null, del: null, files: null },
    ctx_exc: 0,
    tools: { total: 0, errors: 0, list: [] },
    sub: {
      requests: 0, total: 0, input: 0, output: 0, cache_read: 0, reasoning: 0,
      cache_write: 0, active: false, list: [],
    },
    context_window: 0, context_auto: false,
  };
}

async function getSnapshot(sids, mine) {
  const key = sids;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < SNAPSHOT_TTL_MS) return withMine(hit.body, mine);

  if (inflight.has(key)) {
    const body = await inflight.get(key);
    return withMine(body, mine);
  }

  const job = (async () => {
    let text;
    try {
      text = unstable ? await askOneShot(sids) : await askResident(sids);
    } catch (e) {
      log('查询失败，回退一次性:', e.message);
      unstable = true;
      text = await askOneShot(sids);
    }
    const parsed = JSON.parse(text);
    if (parsed.error) throw new Error(parsed.error);
    sanitize(parsed);
    applyContextWindow(parsed);
    cache.set(key, { at: Date.now(), body: parsed });
    if (cache.size > 64) cache.delete(cache.keys().next().value);
    return parsed;
  })();

  inflight.set(key, job);
  try {
    return withMine(await job, mine);
  } finally {
    inflight.delete(key);
  }
}

/** mine = 页面当前焦点的会话 id；overlay 靠它认「本会话」，绝不回退到别的会话 */
/**
 * 把 payload 收口到「mine 那个会话」。
 *
 * 为什么必须重写而不只是换 session 字段：zusage.py 的 snapshot() 顶层
 * session / last / last_turn / tools / code / ctx_exc / context_window **永远是最新会话的**
 * （`base = _session_snapshot(con, latest_sid, cfg)`，force_sids 只被插进 recent 池）。
 * 实测（本机两个会话对照）：A 会话自己最后一次请求是 duration_ms=1986 / ttft 空，
 * 而顶层 last 给的是 B（最新）会话的 11574 / 8052 —— 只换 session 字段，
 * 会让「速度 / 本轮」显示成别的会话的数字。
 *
 * 插件原版 overlay 的做法（overlay.js:665-694）就是：一切取自池里匹配 mine 的那条，
 * 顶层字段一概不用；池里没有就用零值壳，绝不回退到别的会话。这里对齐同一语义，
 * 同时把顶层字段也镜像过去，让下游（我们的 overlay）读哪一层都自洽。
 */
function withMine(body, mine) {
  const copy = { ...body };
  copy.mine = mine || '';
  if (typeof mine !== 'string') return copy;

  let p = null;
  if (mine) {
    const pool = body.recent || [];
    p = pool.find((s) => s && s.sid === mine) || null;
    if (!p && body.session && body.session.sid === mine) p = body.session;
    if (!p) p = stubSession(mine);
  } else {
    p = stubSession('');
  }

  copy.session = p;
  copy.last = p.last || {};
  copy.last_turn = p.last_turn || {};
  copy.tools = p.tools || { total: 0, errors: 0, list: [] };
  copy.sub = p.sub || stubSession('').sub;
  copy.code = p.code || { add: null, del: null, files: null };
  copy.ctx_exc = p.ctx_exc || 0;
  copy.context_window = p.context_window || 0;
  copy.context_auto = !!p.context_auto;
  copy.own = !!(mine && p.sid === mine);
  // today 是跨会话合计，保持全局，不随 mine 变
  copy.context_source = p.context_auto ? 'provider_config' : (p.context_window ? 'fallback' : 'none');
  return copy;
}

/* ---------- HTTP ---------- */
const MIME = {
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
};

function send(res, code, body, headers = {}) {
  res.writeHead(code, { 'Cache-Control': 'no-store', ...headers });
  res.end(body);
}

function tokenOk(url) {
  if (!TOKEN) return true;
  const t = url.searchParams.get('token') || '';
  if (t.length !== TOKEN.length) return false;
  let diff = 0;
  for (let i = 0; i < t.length; i++) diff |= t.charCodeAt(i) ^ TOKEN.charCodeAt(i);
  return diff === 0;
}

/** 静态文件白名单：只允许这几个路径，杜绝任意读文件。
 *  注意 overlay.js 是**我们自己的**一份实现（窄屏/宽屏自适应），不是插件仓库里那份
 *  Electron 桌面版 —— 方案 §5.2 决策 A：避免两套让位逻辑同时写同一个输入框卡片。 */
const STATIC = new Map([
  ['/zusage/boot.js', join(HERE, 'boot.js')],
  ['/zusage/overlay.js', join(HERE, 'overlay.js')],
]);

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const path = url.pathname.replace(/\/+$/, '') || '/';

  /* /zusage/health 经 nginx 是公网可达的，所以公开分支只回一个 ok；
   * 详情（含插件目录绝对路径）必须带 token 才给。 */
  if (path === '/zusage/health') {
    if (!tokenOk(url)) return send(res, 200, JSON.stringify({ ok: true }), { 'Content-Type': MIME['.json'] });
    return send(res, 200, JSON.stringify({
      ok: true,
      resident: !!child,
      unstable,
      tokenRequired: !!TOKEN,
      pluginDir: PLUGIN_DIR,
      ctxModels: loadContextCatalog().size,
    }), { 'Content-Type': MIME['.json'] });
  }

  const file = STATIC.get(path);
  if (file) {
    if (!existsSync(file)) return send(res, 404, 'not found');
    const st = statSync(file);
    res.writeHead(200, {
      'Content-Type': MIME[extname(file)] || 'application/octet-stream',
      'Content-Length': st.size,
      // boot/pump 改动要立即生效，不缓存
      'Cache-Control': 'no-store',
    });
    return createReadStream(file).pipe(res);
  }

  if (path === '/zusage/snapshot') {
    if (!tokenOk(url)) return send(res, 401, JSON.stringify({ error: 'unauthorized' }), { 'Content-Type': MIME['.json'] });
    const sids = (url.searchParams.get('sids') || '').slice(0, 600);
    const mine = (url.searchParams.get('mine') || '').slice(0, 200);
    try {
      const body = await getSnapshot(sids, mine);
      return send(res, 200, JSON.stringify(body), { 'Content-Type': MIME['.json'] });
    } catch (e) {
      log('snapshot 失败:', e.message);
      return send(res, 500, JSON.stringify({ error: String(e.message || e).slice(0, 200) }), {
        'Content-Type': MIME['.json'],
      });
    }
  }

  return send(res, 404, 'not found');
});

server.listen(PORT, HOST, () => {
  log(`zusage 旁挂服务 http://${HOST}:${PORT}`);
  log('插件目录:', PLUGIN_DIR, '| token 校验:', TOKEN ? '开' : '关');
  loadContextCatalog();
  log('模型目录载入:', ctxCatalog.size, '条');
  child = startChild();
});

function shutdown() {
  log('退出中…');
  child?.dispose();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1500).unref?.();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
