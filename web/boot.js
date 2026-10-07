/**
 * zusage web 引导脚本 —— 数据泵（唯一注入入口）。
 *
 * 它负责原 Electron 主进程 `inject-main.cjs` 干的那两件事：
 *   1. 把 payload 送进页面：window.__zusageUpdate(payload)
 *   2. 告诉服务端「本窗口当前是哪个会话」：payload.mine
 *
 * 与桌面版的差异（web 没有文件系统、没有 IPC）：
 *   - 触发：fs.watch 事件 → 轮询（可见 2~4s / 隐藏 20s / visibilitychange 立即）
 *   - 当前会话：IPC 上报 → composer 祖先链的 data-session-id（两端 DOM 一致，见方案 §6.3）
 *   - token：直连页读 URL 的 ?token=；远控页 URL 上没有，钩一次 WebSocket 从 /ws?token= 取
 *
 * 渲染全部交给 overlay.js（一份实现，窄屏/宽屏自适应）。
 */
(function () {
  'use strict';
  if (window.__zusageBoot) return;
  window.__zusageBoot = true;

  var NS = (window.__zusageWeb = window.__zusageWeb || {});
  NS.version = 'web-2';
  NS.token = '';
  NS.lastPayload = null;
  NS.diag = { bootAt: Date.now(), polls: 0, errors: 0, lastError: '' };

  var BASE = '/zusage';
  var OVERLAY = BASE + '/overlay.js';

  /* ---------- token ---------- */
  function fromUrl() {
    try {
      var t = new URLSearchParams(location.search).get('token');
      if (t) { NS.token = t; return true; }
    } catch (e) {}
    return false;
  }
  /* 远控页 URL 上没有 token —— 它是 relay 经 workspace-bridge 下发的 wsUrl 里的查询参数。
   * 只在确实拿不到时才挂钩，避免无谓地改动直连页的运行环境。 */
  function hookWebSocket() {
    var Native = window.WebSocket;
    if (!Native || Native.__zusageHooked) return;
    function Hooked(url, protocols) {
      try {
        var u = new URL(String(url), location.href);
        var t = u.searchParams.get('token');
        if (t) NS.token = t;
      } catch (e) {}
      return protocols === undefined ? new Native(url) : new Native(url, protocols);
    }
    Hooked.prototype = Native.prototype;
    ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) {
      try { Hooked[k] = Native[k]; } catch (e) {}
    });
    Hooked.__zusageHooked = true;
    try { window.WebSocket = Hooked; } catch (e) {}
  }
  if (!fromUrl()) hookWebSocket();

  /* ---------- 当前会话（与插件同源规则：composer 祖先链的 data-session-id） ---------- */
  function currentSid() {
    var panes;
    try { panes = document.querySelectorAll('[data-session-id]'); } catch (e) { return ''; }
    var fallback = '';
    for (var i = 0; i < panes.length; i++) {
      var el = panes[i], r;
      try { r = el.getBoundingClientRect(); } catch (e) { continue; }
      if (r.width < 40 || r.height < 40 || r.bottom < 0 || r.top > innerHeight) continue;
      var sid = el.getAttribute('data-session-id') || '';
      if (!sid || sid === 'draft') continue;
      try { if (el.contains(document.activeElement)) return sid; } catch (e) {}
      if (!fallback) fallback = sid;
    }
    return fallback;
  }

  /* ---------- overlay 注入（只注入一次；overlay 自己按视口宽度切换布局） ---------- */
  function injectOverlay() {
    if (document.querySelector('script[data-zusage-overlay]')) return;
    var s = document.createElement('script');
    s.src = OVERLAY;
    s.setAttribute('data-zusage-overlay', '1');
    s.async = false;
    s.onerror = function () { NS.diag.errors++; NS.diag.lastError = 'overlay 载入失败'; };
    document.head.appendChild(s);
  }

  /* ---------- 数据泵 ---------- */
  var timer = null, inflightReq = null;

  function cadence() {
    if (document.hidden) return 20000;
    return NS.lastPayload && NS.lastPayload.mine ? 2500 : 4000;
  }

  function poll() {
    if (inflightReq) return;
    if (!NS.token) { schedule(); return; }
    var sid = currentSid();
    NS.sid = sid;

    // overlay 可能列了「想看」的会话（插件原版的分屏机制），一起带上
    var want = [];
    try {
      if (Array.isArray(window.__zusageWantSids)) want = window.__zusageWantSids.slice(0, 6);
      else if (window.__zusageWantSid) want = [window.__zusageWantSid];
    } catch (e) {}
    var sids = [];
    if (sid) sids.push(sid);
    want.forEach(function (s) { if (s && sids.indexOf(s) < 0) sids.push(s); });

    var url = BASE + '/snapshot?token=' + encodeURIComponent(NS.token) +
      '&sids=' + encodeURIComponent(sids.join(',')) +
      '&mine=' + encodeURIComponent(sid);

    inflightReq = fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        NS.diag.polls++;
        NS.lastPayload = d;
        d.mine = sid;
        try { window.__zusageUpdate && window.__zusageUpdate(d); } catch (e) {
          NS.diag.errors++; NS.diag.lastError = 'update: ' + e.message;
        }
      })
      .catch(function (e) {
        NS.diag.errors++; NS.diag.lastError = String(e.message || e);
      })
      .then(function () { inflightReq = null; schedule(); });
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(poll, cadence());
  }

  /* ---------- 事件 ---------- */
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) schedule(); else poll();
  }, true);
  window.addEventListener('online', poll);

  function start() {
    injectOverlay();
    poll();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
