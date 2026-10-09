/**
 * ZCode 用量状态条 · Web 版（一份实现，两套布局）
 *
 *   窄屏 <768px（手机，用户 2026-10-07 选定「① 极简一行」）
 *     ⚡ 86 t/s        ◔ 31.7%        Σ 1.45M
 *     点整条 → 底部抽屉看全量
 *
 *   宽屏 ≥768px（桌面）—— 与 ZCode 桌面客户端插件条（插件仓库 overlay.js v61）同款：
 *     ⚡86 t/s │ ▬31.7% │ ⟳67.8K 90% 7次 ⇅18.4s ▮1.4s │ 💬1.45M 90% 23轮 187次 │ 🔧142 ⓘ2 │ 📅3.42M │ ⑂236.4K ● ⚙
 *     等宽字 14px / 玻璃胶囊 / 发丝分隔 / 46×5 上下文微条 / 三档自调色（#3ecf8e·#f5b944·#ff6b57）
 *     悬停某一组 → 条上方浮出该组明细（弹层是条的子树，鼠标可直接移进去）
 *     点某一组 → 钉住该明细（再点一次 / 点条外 / Esc 收起）
 *
 * 数据来自 boot.js 推送的 window.__zusageUpdate(payload)（沿用插件原版契约）。
 *
 * ── 与树内移动布局的共存约定（重要）──
 * 1. 只读 DOM。对页面唯一的写操作：给输入框视觉卡片设 style.marginBottom 让位（§ensurePad）。
 *    不加 class、不改 <html>、不注入全局 CSS 变量、不注册全局事件。
 * 2. 让位量自适应：够放就不占位，不够才补差额。不硬编码 24px（插件原版硬编码，
 *    在桌面客户端成立；web 上底部留白归移动布局那条线管，随时可能变）。
 * 3. 断点与树内 useIsNarrowViewport.ts 一字不差：(max-width: 767.5px)。
 *    两套断点会导致「布局已切换、状态栏没切换」的错帧。
 * 4. 锚点优先用稳定 test-id / data 属性，启发式只兜底。
 */
(function () {
  'use strict';
  if (window.__zusageOverlay) return;
  window.__zusageOverlay = true;

  var MY_GEN = (window.__zusageGen = (window.__zusageGen || 0) + 1);
  function stale() { return MY_GEN !== window.__zusageGen; }

  var NARROW_Q = '(max-width: 767.5px)';   // 与 useIsNarrowViewport.ts:3 同值
  // H_WIDE = 客户端插件条实测高度（14px × 1.3 行高 + 2px 内边距 + 1px 边框 + 17px ⚙ 撑高）
  var H_NARROW = 22, H_WIDE = 32, GAP = 4, BREATH = 4;   // BREATH：条下方到"卡片正下方内容"的最小缝

  /* ---------- 语言 ----------
   * 不能用 <html lang>：两个页面都是 lang="en"，但界面实际是中文（实测）。
   * ZCode 的真值在 localStorage["zcode-locale-preference"]（IntlProvider.tsx:33，
   * 取值 zh-CN / en-US / system）；未设置时回退浏览器语言。每次 render 重算，
   * 用户在设置里改语言后一次轮询内跟上。 */
  var zh = true;
  function detectZh() {
    try {
      var p = localStorage.getItem('zcode-locale-preference');
      if (p === 'zh-CN') return true;
      if (p === 'en-US') return false;
    } catch (e) {}
    var nav = String(navigator.language || (navigator.languages && navigator.languages[0]) || '').toLowerCase();
    if (nav) return nav.indexOf('zh') === 0;
    return !/^en/i.test(document.documentElement.lang || '');
  }
  zh = detectZh();
  function L(a, b) { return zh ? a : b; }

  /* ---------- 显示项开关（窄屏/宽屏各一套，localStorage 持久化） ----------
   * 为什么分两套：手机一行放得下 5 项就满了（实测 7 项 344px > 可用 345px，
   * 等于零间隙），桌面却有 800+px。用一套配置会顾此失彼。 */
  var SHOW_KEY = 'zusage.web.show2';
  var DEFAULT_SHOW = {
    n: { speed: 1, ctx: 1, cache: 1, session: 1, today: 1, tools: 0, sub: 0, turn: 0 },
    /* 宽屏默认与客户端插件条条面一致：生成速度 / 上下文 / 本轮 / 会话累计 / 工具 / 今日 / 子代理 */
    w: { speed: 1, ctx: 1, cache: 1, turn: 1, session: 1, tools: 1, today: 1, sub: 1 },
  };
  var show = JSON.parse(JSON.stringify(DEFAULT_SHOW));
  try {
    var s0 = JSON.parse(localStorage.getItem(SHOW_KEY) || 'null');
    if (s0 && typeof s0 === 'object') {
      ['n', 'w'].forEach(function (m) {
        if (s0[m] && typeof s0[m] === 'object') {
          for (var k in show[m]) if (k in s0[m]) show[m][k] = s0[m][k] ? 1 : 0;
        }
      });
    }
  } catch (e) {}
  function cfg() { return show[mode === 'n' ? 'n' : 'w']; }
  function persistShow() { try { localStorage.setItem(SHOW_KEY, JSON.stringify(show)); } catch (e) {} }

  /* ---------- 数字 ---------- */
  /* ---------- 数字（与插件同款口径） ---------- */
  function fmt(n) {
    n = Number(n) || 0;
    if (n < 0) n = 0;
    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(Math.round(n));
  }
  /* 客户端 sec()：无值显示 –，不显示 0.0s */
  function secs(ms) { return ms ? ((Number(ms) || 0) / 1000).toFixed(1) + 's' : '–'; }
  function pct(used, size) {
    if (!size || size <= 0) return null;
    return Math.min(999, Math.max(0, (used / size) * 100));
  }
  /* 客户端条面直接打印 last.tps 原值（86 而不是 86.0） */
  function tpsText(v) {
    if (v == null || !isFinite(v)) return '–';
    var n = Number(v);
    return Number.isInteger(n) ? String(n) : n.toFixed(1);
  }

  /* ---------- 主题色 ----------
   * 窄屏沿用客户端语义变量（var + 字面兜底）；
   * 宽屏走客户端插件条的自调三档（.zu-ok/.zu-warm/.zu-hot）——插件里刻意不用
   * 客户端调色板变量（terminal-bright-* 是语法高亮色板，绿档在条面小字号下发白，
   * 见插件 overlay.js 第 186-192 行注释），web 端照抄同值以保证与客户端一致。 */
  var TONE = {
    ok: 'var(--color-green-500,#3ba272)',
    warn: 'var(--color-amber-500,#d99a2b)',
    bad: 'var(--color-red-400,#e5484d)',
    mute: 'var(--color-foreground-subtle,#9aa3b2)',
    faint: 'var(--color-foreground-subtlest,#6f7785)',
    text: 'var(--color-foreground,#e8eaed)',
    bg: 'var(--color-background,#161616)',
    border: 'var(--color-border,rgba(255,255,255,.12))',
    hover: 'var(--color-hover,rgba(255,255,255,.06))',
  };
  var FONT = '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
  var MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,"Cascadia Mono",monospace';
  // 客户端插件条的字体栈（插件 overlay.js:167 原样）
  var MONO_C = "Consolas,'Cascadia Mono',Menlo,'Microsoft YaHei UI','Microsoft YaHei',monospace";

  function speedTone(v) { return v == null ? TONE.mute : v >= 70 ? TONE.ok : v >= 40 ? TONE.warn : TONE.bad; }
  /* 上下文三档：窗口 ≥100 万时同一百分比的绝对量更大 → 40/60 提前预警，其余 70/85
   * （插件 overlay.js:526-532 同款口径）。size 缺省视为小窗口，保持窄屏原口径。 */
  function ctxTone(p, exc, size) {
    if (exc) return TONE.bad;
    if (p == null) return TONE.mute;
    var big = (Number(size) || 0) >= 1000000;
    var hi = big ? 60 : 85, mid = big ? 40 : 70;
    return p >= hi ? TONE.bad : p >= mid ? TONE.warn : TONE.ok;
  }
  /* 宽屏用类名着色（CSS 里三档值 + 浅色加深版），与客户端条同源 */
  function speedClass(v) { return v == null ? 'zu-k' : v >= 70 ? 'zu-ok' : v >= 40 ? 'zu-warm' : 'zu-hot'; }
  function ctxClass(p, exc, size) {
    if (exc) return 'zu-hot';
    var big = (Number(size) || 0) >= 1000000;
    return p >= (big ? 60 : 85) ? 'zu-hot' : p >= (big ? 40 : 70) ? 'zu-warm' : 'zu-ok';
  }
  function hitRate(s) {
    var denom = (s.input || 0);
    if (!denom) return null;
    return Math.min(100, ((s.cache_read || 0) / denom) * 100);
  }

  /* ---------- CSS ---------- */
  var CSS = [
    '.zu-bar{position:fixed;z-index:20;display:none;align-items:center;box-sizing:border-box;',
    'font:11.5px/1 ' + FONT + ';color:' + TONE.mute + ';-webkit-user-select:none;user-select:none;',
    'font-variant-numeric:tabular-nums;pointer-events:auto}',
    '.zu-bar.on{display:flex}',
    /* 窄屏：三格均分 */
    '.zu-bar.n{justify-content:space-between;gap:6px;height:' + H_NARROW + 'px;cursor:pointer}',
    '.zu-bar.n .zu-g{flex:0 1 auto;min-width:0}',
    '.zu-bar.n{font-size:11px}',
    /* 宽屏：与 ZCode 桌面客户端插件条同款 —— 玻璃胶囊 + 发丝分隔 + 等宽字 */
    '.zu-bar.w{gap:2px;padding:2px 6px;border-radius:9px;',
    'font:14px/1.3 ' + MONO_C + ';color:var(--color-foreground-subtle,#8791a3);',
    'background:rgba(15,18,25,.86);background:color-mix(in srgb,' + TONE.bg + ' 86%,transparent);',
    'backdrop-filter:blur(14px) saturate(1.3);-webkit-backdrop-filter:blur(14px) saturate(1.3);',
    'border:1px solid ' + TONE.border + ';',
    'box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 4px 14px rgba(0,0,0,.38),0 1px 3px rgba(0,0,0,.28)}',
    '.zu-g{display:inline-flex;align-items:center;gap:4px;min-width:0;white-space:nowrap;',
    'overflow:hidden;text-overflow:ellipsis}',
    '.zu-bar.w .zu-g{padding:2px 6px;border-radius:6px;cursor:default;transition:background-color .12s ease-out}',
    '.zu-bar.w .zu-g:hover,.zu-bar.w .zu-g.zu-open{background:' + TONE.hover + '}',
    /* 发丝分隔：独立元素，不用 box-shadow，才与客户端的"条目之间的短线"一致 */
    '.zu-sep{width:1px;height:15px;background:var(--color-border,rgba(255,255,255,.09));flex:0 0 auto;margin:0 1px}',
    '.zu-k{color:#7e8899}',
    '.zu-v{color:var(--color-foreground,#e9edf4);font-weight:600}',
    '.zu-pct{font-weight:700}',
    '.zu-ico{width:12px;height:12px;flex:0 0 auto;opacity:.85}',
    '.zu-cbar{display:inline-block;width:46px;height:5px;border-radius:999px;',
    'background:var(--color-hover,rgba(255,255,255,.1));overflow:hidden;flex:0 0 auto}',
    '.zu-cbar>i{display:block;height:100%;border-radius:999px;background:currentColor;transition:width .3s}',
    '.zu-gear{flex:0 0 auto;font-size:17px;border-radius:6px}',
    '.zu-eb{background:color-mix(in srgb,var(--color-destructive,#ff6b57) 14%,transparent);',
    'color:var(--color-destructive,#ff8a73);border-radius:999px;padding:0 6px;line-height:16px;',
    'font-weight:600;display:inline-flex;align-items:center;gap:2px}',
    '.zu-ok{color:#3ecf8e}.zu-warm{color:#f5b944}.zu-hot{color:#ff6b57}',
    /* 浅色主题：只兜"程度差"（阴影减重）与三档加深版，其余颜色走客户端变量自动翻转 */
    '.zu-bar.w.zu-light{box-shadow:inset 0 1px 0 rgba(255,255,255,.85),0 4px 14px rgba(15,23,42,.12),0 1px 3px rgba(15,23,42,.08)}',
    '.zu-bar.w.zu-light .zu-ok{color:#0f9d6c}.zu-bar.w.zu-light .zu-warm{color:#b6791a}.zu-bar.w.zu-light .zu-hot{color:#d8482f}',
    '.zu-n{font-weight:600}',
    '.zu-u{opacity:.6;font-size:10.5px}',
    '.zu-mini{width:22px;height:2.5px;border-radius:2px;background:color-mix(in srgb,' + TONE.text + ' 16%,transparent);overflow:hidden;flex:none}',
    '.zu-mini>i{display:block;height:100%;border-radius:2px;transition:width .3s}',
    '@keyframes zu-pulse{0%,100%{opacity:1}50%{opacity:.35}}',
    '.zu-exc{animation:zu-pulse 1s ease-in-out infinite}',
    '.zu-dot{animation:zu-pulse 1.6s ease-in-out infinite;font-size:14px;line-height:1}',
    /* 明细弹层（宽屏）：条的子元素，绝对定位在条上方 —— 鼠标从条面移进弹层不触发条的
       mouseleave（弹层是后代节点），因此不再需要"空中走廊/宽限定时器"那套补丁。
       注：条的 backdrop-filter 会让 fixed 后代以条为包含块，所以这里必须是 absolute。 */
    '.zu-pop{position:absolute;left:0;bottom:calc(100% + 8px);z-index:1;min-width:190px;max-width:420px;',
    'background:rgba(19,22,30,.97);background:color-mix(in srgb,' + TONE.bg + ' 96%,transparent);',
    'backdrop-filter:blur(18px) saturate(1.3);-webkit-backdrop-filter:blur(18px) saturate(1.3);',
    'border:1px solid var(--color-border,rgba(255,255,255,.09));border-radius:12px;padding:10px 14px;',
    'font:13px/1.6 ' + MONO_C + ';color:var(--color-foreground,#c6cdd9);',
    'box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 12px 32px rgba(0,0,0,.5),0 2px 8px rgba(0,0,0,.35);',
    'display:none;white-space:normal;max-height:60vh;overflow:auto;scrollbar-width:thin}',
    '.zu-pop.on{display:block}',
    '.zu-pop h5{margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:.06em;color:var(--color-foreground,#eef2f8)}',
    '.zu-r{display:flex;justify-content:space-between;gap:14px}',
    '.zu-r>span:first-child{color:var(--color-foreground-subtle,#8791a3)}',
    '.zu-r>span:last-child{font-weight:600;font-variant-numeric:tabular-nums}',
    '.zu-set{display:flex;align-items:center;gap:8px;padding:3px 0;cursor:pointer;user-select:none}',
    '.zu-set input{margin:0;cursor:pointer;accent-color:var(--color-brand,#57c7ff)}',
    /* 抽屉（窄屏） */
    /* 关闭态必须彻底让位：opacity:0 的 fixed 全屏层照样吃掉整页点击
       （实测：抽屉没开时连侧栏里的任务条目都点不动）。pointer-events + visibility 双保险。 */
    '.zu-back{position:fixed;inset:0;z-index:60;background:rgba(0,0,0,.42);opacity:0;',
    'visibility:hidden;pointer-events:none;transition:opacity .18s,visibility .18s}',
    '.zu-back.on{opacity:1;visibility:visible;pointer-events:auto}',
    '.zu-sheet{position:fixed;left:0;right:0;bottom:0;z-index:61;max-height:72vh;overflow:auto;',
    '-webkit-overflow-scrolling:touch;background:' + TONE.bg + ';color:' + TONE.text + ';',
    'border-top:1px solid ' + TONE.border + ';border-radius:14px 14px 0 0;',
    'padding:6px 14px calc(14px + env(safe-area-inset-bottom,0px));font:13px/1.5 ' + FONT + ';',
    'transform:translateY(100%);transition:transform .22s cubic-bezier(.2,.8,.2,1);',
    'visibility:hidden;pointer-events:none;box-shadow:0 -10px 30px rgba(0,0,0,.35)}',
    '.zu-sheet.on{transform:translateY(0);visibility:visible;pointer-events:auto}',
    '.zu-grab{width:36px;height:4px;border-radius:2px;background:' + TONE.border + ';margin:6px auto 10px}',
    '.zu-h{font-size:12px;font-weight:600;color:' + TONE.mute + ';margin:14px 0 6px;',
    'display:flex;justify-content:space-between;align-items:baseline}',
    '.zu-h:first-of-type{margin-top:4px}',
    '.zu-bar2{height:4px;border-radius:2px;background:' + TONE.border + ';overflow:hidden;margin:6px 0 2px}',
    '.zu-fill{height:100%;border-radius:2px;transition:width .3s}',
    '.zu-note{font-size:11px;color:' + TONE.faint + ';margin-top:10px;line-height:1.6}',
  ].join('');

  function styleOnce() {
    if (document.getElementById('zu-style')) return;
    var st = document.createElement('style');
    st.id = 'zu-style';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  /* ---------- 图标：全部取自 ZCode 自带的 lucide-react（同源同风格） ----------
   * 24 视框 + round cap/join；小尺寸下把 stroke-width 提到 2.4 才不糊。 */
  function svg(inner, size) {
    size = size || 10;
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" ' +
      'style="flex:none" aria-hidden="true">' + inner + '</svg>';
  }
  var I = {
    bolt: svg('<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>'),
    db: svg('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>'),
    chat: svg('<path d="M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z"/>'),
    tool: svg('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z"/>'),
    cal: svg('<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>'),
    sub: svg('<path d="M15 6a9 9 0 0 0-9 9V3"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/>'),
    turn: svg('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
  };

  /* ---------- 宽屏图标：客户端插件条 ico() 原样 ----------
   * 12px / stroke-width 1.8 / 路径逐条抄自插件 overlay.js:481-560，保证与客户端同形。 */
  function svgC(inner) {
    return '<svg class="zu-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>';
  }
  var IC = {
    bolt: svgC('<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>'),
    turn: svgC('<path d="M23 4v6h-6"/><path d="M20.49 15A9 9 0 1 1 18.36 5.64L23 10"/>'),
    swap: svgC('<path d="M6 3h12M6 21h12M8 3v3.5L12 11l4-4.5V3M8 21v-3.5L12 13l4 4.5V21"/>'),
    bars: svgC('<path d="M5 20v-5M12 20v-9M19 20V5"/>'),
    chat: svgC('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
    tool: svgC('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>'),
    warn: svgC('<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>'),
    cal: svgC('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
    sub: svgC('<path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>'),
  };

  /* ---------- 运行状态 ---------- */
  var bar = null, pop = null, sheet = null, back = null;
  var lastData = null, mode = '';
  var composer = null, cardCache = null, hideSince = 0, lastPos = [0, 0];
  var appliedPad = 0, basePad = 0, baseCaptured = false;
  var padKind = 0;        // 0=未标定 1=下方锚定（pad 会顶起卡片） 2=普通流（pad 只在下方腾空间）
  var padPreBottom = 0;   // 施加 pad 前的卡片底边，供下一帧标定布局类型
  var lastVh = 0, lastVw = 0;
  var popKey = '', popPinned = false;
  var hWideNow = H_WIDE;  // 宽屏条实测高度（字体/条目撑出来的，用于让位与夹取）
  var nextGapCache = { at: 0, v: -1 };   // 卡片正下方内容的间距，缓存 300ms

  function isNarrow() {
    try { return window.matchMedia(NARROW_Q).matches; } catch (e) { return innerWidth <= 767.5; }
  }

  function ensureDom() {
    styleOnce();
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'zu-bar';
      bar.setAttribute('data-zusage', '1');
      document.body.appendChild(bar);
      bar.addEventListener('click', onBarClick);
      bar.addEventListener('mouseover', onGroupHover);
      /* 弹层是条的后代节点：鼠标从条面移进弹层不算离开条（mouseleave 只在离开整棵子树时触发），
         所以这里直接收起即可，不需要宽限定时器。 */
      bar.addEventListener('mouseleave', function () { if (!popPinned) hidePop(); });
      pop = document.createElement('div');
      pop.className = 'zu-pop';
      pop.setAttribute('role', 'tooltip');
      /* 挂在条内部（而不是 body）：① 鼠标可直达，不必跨越 8px 真空带；
         ② 条的 backdrop-filter 使其成为 fixed 后代的包含块，所以 CSS 用 absolute。 */
      bar.appendChild(pop);
      pop.addEventListener('click', function (e) { e.stopPropagation(); });
      /* 钉住后的关闭路径：点条外 / Esc（否则钉住的弹层无从收起） */
      document.addEventListener('click', function (e) {
        if (popPinned && bar && !bar.contains(e.target)) unpinPop();
      }, true);
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && popPinned) unpinPop();
      }, true);
    }
    if (!sheet) {
      back = document.createElement('div');
      back.className = 'zu-back';
      back.addEventListener('click', closeSheet);
      sheet = document.createElement('div');
      sheet.className = 'zu-sheet';
      sheet.innerHTML = '<div class="zu-grab"></div><div class="zu-body"></div>';
      document.body.appendChild(back);
      document.body.appendChild(sheet);
    }
  }

  /* ---------- 定位（复用插件原版在桌面端实测的启发式） ---------- */
  function reallyVisible(el) {
    try {
      var cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
      if (parseFloat(cs.opacity || '1') < 0.05) return false;
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  }
  function findComposer() {
    // 1) 稳定锚点
    try {
      var exact = document.querySelector('[data-testid="v4-composer-input"]');
      if (exact && reallyVisible(exact)) return exact;
    } catch (e) {}
    // 2) 启发式：视口下半部的可见 textarea（聊天输入框特征）
    var best = null;
    document.querySelectorAll('textarea').forEach(function (ta) {
      if (!reallyVisible(ta)) return;
      var r = ta.getBoundingClientRect();
      if (r.top < innerHeight * 0.45) return;
      if (!best || r.top > best.getBoundingClientRect().top) best = ta;
    });
    return best;
  }
  function isVisualBox(el) {
    try {
      var cs = getComputedStyle(el);
      return cs.borderTopWidth !== '0px' || cs.backgroundColor !== 'rgba(0, 0, 0, 0)';
    } catch (e) { return false; }
  }
  function cardOf(el) {
    var cr = el.getBoundingClientRect();
    var p = el.parentElement, last = el, i = 0;
    for (; p && p !== document.body && i < 8; p = p.parentElement, i++) {
      var r = p.getBoundingClientRect();
      if (r.height > cr.height * 8 + 80) break;
      if (isVisualBox(p)) return p;
      last = p;
    }
    return last;
  }

  function isOwnEl(el) {
    try { return !!(el.closest && el.closest('[data-zusage],.zu-pop,.zu-sheet,.zu-back')); } catch (e) { return false; }
  }

  /**
   * 卡片正下方"下一个内容块"到卡片下沿的间距（没有则返回 -1）。
   *
   * 为什么必须看它：让位若只按"到视口底还剩多少"算，卡片下沿到下一个兄弟内容之间的
   * 空档就不在账上。线上实测（2026-10-09，新任务落地页）：卡片下沿到建议 chips 只隔
   * 一个 mt-6 = 24px，而条要 36px（高 32 + 顶缝 4）→ 条压住 chips 8px。
   * 客户端插件条是固定 24px 让位带（插件 overlay.js:1180），不存在这个问题。
   * 做法：沿祖先链逐层看后继兄弟，取最近的一个 —— 覆盖"内容不在卡片同级 DOM"的情况
   * （ZCode 里卡片在 FORM 内、chips 行是 FORM 的后继兄弟）。纯装饰层与自己的浮层跳过。
   */
  function gapToNextBelow(card) {
    try {
      var now = Date.now();
      if (now - nextGapCache.at < 300) return nextGapCache.v;
      nextGapCache.at = now;
      var cr = card.getBoundingClientRect();
      var best = -1, p = card, lvl = 0;
      for (; p && p !== document.body && lvl < 8; p = p.parentElement, lvl++) {
        for (var s = p.nextElementSibling; s; s = s.nextElementSibling) {
          var r;
          try { r = s.getBoundingClientRect(); } catch (e) { continue; }
          if (r.height < 4 || r.width < 8) continue;                     // 空壳 / 0 高装饰层
          if (r.bottom <= cr.bottom + 2) continue;                       // 在卡片上方或齐平
          if (r.top >= innerHeight) continue;                            // 视口外
          if (r.right < cr.left + 8 || r.left > cr.right - 8) continue;  // 与卡片不横向重叠
          if (isOwnEl(s)) continue;
          var cs = getComputedStyle(s);
          if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.05) continue;
          if (best < 0 || r.top < best) best = r.top;
        }
      }
      nextGapCache.v = best < 0 ? -1 : Math.max(0, Math.round(best - cr.bottom));
      return nextGapCache.v;
    } catch (e) { return -1; }
  }

  /**
   * 让位量自适应：够放就不占位，不够才补差额。
   * 两个约束取更紧的一个：
   *   ① 卡片下沿 → 视口底还剩多少（够就不占位）
   *   ② 卡片下沿 → 正下方内容块还剩多少（不够就把它推下去）
   * 两者都按"剥掉我们自己已施加的量"来算基线，才不会 加了撤、撤了加 地震荡。
   */
  function ensurePad() {
    if (!cardCache) return;
    try {
      if (!baseCaptured) {
        basePad = parseFloat(getComputedStyle(cardCache).marginBottom) || 0;
        baseCaptured = true;
        padKind = 0; padPreBottom = 0;
      }
      var ar = cardCache.getBoundingClientRect();
      var need = (mode === 'n' ? H_NARROW : hWideNow) + GAP;

      /* 一次标定：加 margin-bottom 对两种定位方式效果相反 ——
       *   下方锚定（如 position:fixed;bottom:0）→ 卡片被顶起来，卡片底边上移
       *   普通流（ZCode 真实的 composer 卡片）→ 卡片本身不动，只在它下方腾出空间
       * 用同一个基线去算就会「够了就撤、撤了又不够」来回震荡（隔离测试实测到）。
       * 所以先在 0 → 非 0 的那一帧记下卡片底，下一帧看它有没有跟着动，定下类型。 */
      if (padKind === 0 && appliedPad > 0 && padPreBottom) {
        var moved = padPreBottom - ar.bottom;
        padKind = Math.abs(moved - appliedPad) < 3 ? 1 : 2;
        padPreBottom = 0;
      }

      // ① 视口余量（锚定布局下卡片被顶起 appliedPad，要剥掉）
      var R = padKind === 1 ? innerHeight - ar.bottom - appliedPad : innerHeight - ar.bottom;
      // ② 正下方内容余量：两种布局下该内容与卡片的间距都被我们撑大了 appliedPad
      var gn = gapToNextBelow(cardCache);
      var hasNext = gn >= 0;
      if (hasNext) R = Math.min(R, Math.max(0, gn - appliedPad));

      var target = Math.max(0, need + (hasNext ? BREATH : 0) - R);
      if (Math.abs(target - appliedPad) > 2) {
        if (appliedPad === 0) padPreBottom = ar.bottom;   // 记录施加前的底边，供下一帧标定
        appliedPad = target;
        nextGapCache.at = 0;                              // 让位变了 → 间距基线必须重测
        cardCache.style.marginBottom = (basePad + target) + 'px';
      }
    } catch (e) {}
  }
  function releasePad() {
    if (cardCache && baseCaptured) {
      try { cardCache.style.marginBottom = basePad ? basePad + 'px' : ''; } catch (e) {}
    }
    appliedPad = 0; baseCaptured = false; padKind = 0; padPreBottom = 0;
  }

  /** 是否在设置界面。
   *  用户口径（2026-10-07 真机）：**开抽屉不隐藏**（层级降到抽屉之下即可，
   *  抽屉会自然盖住条），**只有进设置才该隐藏**。
   *  用 elementFromPoint 做通用遮挡判据会连抽屉一起误伤，所以只认这个稳定标记。 */
  function onSettingsPage() {
    try {
      var el = document.querySelector('[data-testid="settings-page"]');
      if (!el) return false;
      var r = el.getBoundingClientRect();
      return r.width > 80 && r.height > 80;
    } catch (e) { return false; }
  }

  function track() {
    if (stale()) return;
    try {
      var want = isNarrow() ? 'n' : 'w';
      if (want !== mode) {
        mode = want;
        bar.className = 'zu-bar ' + mode;   // 整体换类名 → 主题的 .zu-light 要补回
        applyTheme();
        bar.style.width = '';               // 窄屏设过定宽，切回宽屏必须清掉
        lastPos = [0, 0];
        hidePop();
      }
      // 视口变化（旋转 / 软键盘）会改变底部留白与让位效果 → 重新标定布局类型
      if (innerHeight !== lastVh || innerWidth !== lastVw) {
        lastVh = innerHeight; lastVw = innerWidth; padKind = 0; padPreBottom = 0;
      }

      if (!composer || !composer.isConnected) {
        releasePad();
        composer = findComposer();
        if (!composer) { hideSince = 0; hideBar(); return; }
        cardCache = cardOf(composer);
        baseCaptured = false;
      }
      if (cardCache && !cardCache.isConnected) { cardCache = cardOf(composer); baseCaptured = false; }
      ensurePad();

      var r = composer.getBoundingClientRect();
      // 开抽屉/浮动面板一律照常显示（靠层级让它们盖住条）；只有设置界面才收起来
      var on = r.width > 60 && r.height > 14 && r.bottom > 0 && r.top < innerHeight &&
        !onSettingsPage();
      if (!on) {
        if (!hideSince) hideSince = Date.now();
        if (Date.now() - hideSince > 400) { hideBar(); hidePop(); }
        return;
      }
      hideSince = 0;
      if (!bar.classList.contains('on')) bar.classList.add('on');

      var anchor = cardCache || composer;
      var ar = anchor.getBoundingClientRect();
      // 条高实测（客户端同款 CSS 下由 14px 字号 + 17px ⚙ 撑出 ~32px）；首帧回退到常量
      if (mode === 'w') {
        var oh = Math.round(bar.offsetHeight);
        if (oh >= 16) hWideNow = oh;
      }
      var hBar = mode === 'n' ? H_NARROW : hWideNow;
      var left = Math.round(ar.left) + (mode === 'w' ? 0 : 2);
      // 底部夹取留 2px：会话页卡片下沿只剩 ~36px，条 32 + 顶缝 4 正好用满，
      // 不给窗口底边留缝会贴死在边沿（客户端注释的口径也是"条下留一点"）。
      var top = Math.max(4, Math.min(Math.round(ar.bottom + GAP), innerHeight - hBar - 2));
      if (left !== lastPos[0] || top !== lastPos[1]) {
        bar.style.left = left + 'px';
        bar.style.top = top + 'px';
        lastPos = [left, top];
      }
      /* 宽度上限：窄屏仍按卡片宽（手机一行定宽均分）；宽屏改按视口 —— 客户端条是按内容
       * 撑开的、不受输入框限制，而对齐后条目变多（加了"本轮"），再按卡片宽夹会把数值
       * 挤成省略号（同数据实测：卡片 840 / 内容 940 时 "1.4s"→"1"、"187次"→"187"）。 */
      var maxW = mode === 'n'
        ? Math.max(80, Math.round(ar.width - 8))
        : Math.max(160, Math.round(innerWidth - Math.round(ar.left) - 4));
      if (bar.style.maxWidth !== maxW + 'px') bar.style.maxWidth = maxW + 'px';
      if (mode === 'n' && bar.style.width !== maxW + 'px') bar.style.width = maxW + 'px';
    } catch (e) {
      window.__zusageDiag = String((e && e.stack) || e);
    }
  }
  function hideBar() {
    if (bar && bar.classList.contains('on')) bar.classList.remove('on');
    hidePop();
  }

  /* ---------- 取值 ---------- */
  function pick(p) {
    if (!p) return null;
    var s = p.session || null;
    /* 严格本会话：mine 为空（新建草稿）或快照里的会话不是 mine → 一律零值。
     * 不能只看「s 存在」——zusage.py 在 force_sid 为空时会返回**最近一个会话**
     * （snapshot() 的 force_sids 为空则回退最近会话），而 last/last_turn/sub/tools
     * 全都是 `where session_id=?` 限定在那个会话上的。放过去就会串显别人的数字。 */
    var own = !!(p.mine && s && s.sid === p.mine);
    if (!own) s = null;

    var size = 0, src = '';
    // 1) 页面原生下发（最准，跟随模型）
    var m = readNative();
    if (m.max) { size = m.max; src = 'native'; }
    // 2) sidecar 修正值（provider_config 查表）
    if (!size) {
      size = (s && s.context_window) || p.context_window || 0;
      src = size ? 'catalog' : '';
    }
    var used = (s && s.ctx) || (m.used || 0);
    return {
      s: s, p: p, own: own,
      tps: own && p.last && isFinite(p.last.tps) ? p.last.tps : null,
      used: used, size: size, src: src,
      cp: pct(used, size),
      exc: !!(s && s.ctx_exc),
    };
  }

  /** 读页面原生读数：data-usage-* 优先（稳定属性），aria-label 兜底 */
  var nativeCache = { at: 0, max: 0, used: 0 };
  function readNative() {
    if (Date.now() - nativeCache.at < 3000) return nativeCache;
    nativeCache.at = Date.now();
    var el = null;
    try { el = document.querySelector('[data-usage-max]'); } catch (e) {}
    if (el) {
      var mx = parseInt(el.getAttribute('data-usage-max') || '', 10);
      var us = parseInt(el.getAttribute('data-usage-used') || '', 10);
      if (mx > 0) { nativeCache.max = mx; nativeCache.used = us > 0 ? us : 0; return nativeCache; }
    }
    var card = cardCache || composer;
    if (card) {
      var els = card.querySelectorAll('button,[aria-label],[title]');
      for (var i = 0; i < els.length; i++) {
        var t = els[i].getAttribute('aria-label') || els[i].getAttribute('title') || '';
        var mm = t.match(/总量\s*([\d,，]+)/) || t.match(/of\s*([\d,]+)\s*$/i);
        if (mm) { nativeCache.max = parseInt(mm[1].replace(/[,，]/g, ''), 10) || 0; return nativeCache; }
      }
    }
    nativeCache.max = 0; nativeCache.used = 0;
    return nativeCache;
  }

  /* ---------- 渲染 ----------
   * 窄屏：① 极简一行（用户 2026-10-07 真机选定：不要环 —— 输入框工具栏已有原生上下文环，
   *       重复；一行有空间就多显示点内容）。
   * 宽屏：与 ZCode 桌面客户端插件条同款 —— 条目构成、顺序、图标、数字口径全部照抄插件
   *       overlay.js html()（生成速度 / 上下文 / 本轮 / 会话累计 / 工具调用 / 今日合计 /
   *       子代理 / ⚙），组间用独立发丝分隔元素。 */

  /** 只替换条的"条目区"：弹层现在是条的子元素，整体 bar.innerHTML=… 会把它一起销毁。 */
  function setGroups(html) {
    var olds = bar.querySelectorAll(':scope > .zu-g, :scope > .zu-sep');
    for (var i = 0; i < olds.length; i++) olds[i].parentNode.removeChild(olds[i]);
    if (html) bar.insertAdjacentHTML('afterbegin', html);
  }

  function buildItems(v) {
    var s = v.s, p = v.p, out = [], hr;
    var cTone = ctxTone(v.cp, v.exc, v.size);
    var pTxt = v.cp == null ? '–' : (v.cp >= 100 ? '100' : v.cp.toFixed(1)) + '%';
    var SH = cfg();
    if (SH.speed) {
      out.push({ k: 'speed', ic: I.bolt, v: tpsText(v.tps), u: 't/s', c: speedTone(v.tps),
                 t: L('生成速度', 'Speed') });
    }
    if (SH.ctx) {
      out.push({ k: 'ctx', bar: Math.min(100, v.cp || 0), bc: cTone, v: pTxt, c: cTone,
                 exc: v.exc, t: L('上下文占比', 'Context usage') });
    }
    if (SH.cache && s) {
      hr = hitRate(s);
      if (hr != null) out.push({ k: 'cache', ic: I.db, v: hr.toFixed(0) + '%', c: TONE.text,
                                 t: L('缓存命中率', 'Cache hit rate') });
    }
    if (SH.turn && s && s.last_turn) {
      out.push({ k: 'turn', ic: I.turn, v: secs(s.last_turn.duration_ms), u: secs(s.last_turn.ttft_ms),
                 c: TONE.text, t: L('本轮耗时 / 首字', 'Turn duration / TTFT') });
    }
    if (SH.session && s) {
      out.push({ k: 'session', ic: I.chat, v: fmt(s.total),
                 u: SH.turn ? '' : (s.turns || 0) + L('轮', 't'),
                 c: TONE.text, t: L('本会话累计', 'Session total') });
    }
    if (SH.tools && s && s.tools && s.tools.total) {
      out.push({ k: 'tools', ic: I.tool, v: String(s.tools.total), err: s.tools.errors || 0,
                 c: TONE.text, t: L('工具调用', 'Tool calls') });
    }
    if (SH.today && p.today) {
      out.push({ k: 'today', ic: I.cal, v: fmt(p.today.total), c: TONE.text,
                 t: L('今日合计', 'Today') });
    }
    if (SH.sub && s && s.sub && (s.sub.total || s.sub.requests)) {
      out.push({ k: 'sub', ic: I.sub, v: fmt(s.sub.total), dot: s.sub.active, c: TONE.text,
                 t: L('子代理', 'Subagents') });
    }
    return out;
  }

  function miniBar(pctv, color) {
    return '<span class="zu-mini"><i style="width:' + pctv + '%;background:' + color + '"></i></span>';
  }

  function renderNarrow(v) {
    var items = buildItems(v), g = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      g.push('<span class="zu-g' + (it.exc ? ' zu-exc' : '') + '" title="' + it.t + '">' +
        (it.bar != null ? miniBar(it.bar, it.bc) : (it.ic || '')) +
        '<span class="zu-n" style="color:' + it.c + '">' + it.v + '</span>' +
        (it.u ? '<span class="zu-u">' + it.u + '</span>' : '') +
        (it.err ? '<span class="zu-n" style="color:' + TONE.bad + '">✕' + it.err + '</span>' : '') +
        (it.dot ? '<span style="color:' + TONE.ok + '">●</span>' : '') + '</span>');
    }
    setGroups(g.join(''));
    bar.title = L('点击查看明细与显示项', 'Tap for details');
  }

  /* 插件同款：token 后跟缓存命中率（无输入则不出） */
  function cachePct(cache, input) {
    return input > 0 ? '<span class="zu-k">' + Math.round(cache / input * 100) + '%</span>' : '';
  }

  function renderWide(v) {
    var s = v.s, p = v.p, SH = cfg(), g = [];
    var lt = (s && s.last_turn) || null, last = p.last || {};
    if (SH.speed && v.tps != null) {
      g.push('<span class="zu-g" data-g="speed">' + IC.bolt +
        '<span class="' + speedClass(v.tps) + '">' + tpsText(v.tps) + '</span>' +
        '<span class="zu-k">t/s</span></span>');
    }
    if (SH.ctx) {
      var cc = ctxClass(v.cp, v.exc, v.size);
      g.push('<span class="zu-g" data-g="ctx">' + (v.size
        ? '<span class="zu-cbar"><i class="' + cc + '" style="width:' +
          Math.min(100, v.cp || 0).toFixed(1) + '%"></i></span>' +
          '<span class="zu-pct ' + cc + '">' + (v.cp == null ? '–' : v.cp.toFixed(1) + '%') + '</span>'
        : '<span class="zu-v">' + fmt(v.used) + '</span>') + '</span>');
    }
    if (SH.turn && lt) {
      g.push('<span class="zu-g" data-g="turn">' + IC.turn +
        '<span class="zu-v">' + fmt(lt.total) + '</span>' +
        (SH.cache ? cachePct(lt.cache_read, lt.input) : '') +
        '<span class="zu-k">' + (lt.requests || 0) + L('次', ' req') + '</span>' +
        IC.swap + '<span class="zu-k">' + secs(last.duration_ms) + '</span>' +
        IC.bars + '<span class="zu-k">' + secs(last.ttft_ms) + '</span></span>');
    }
    if (SH.session && s) {
      g.push('<span class="zu-g" data-g="session">' + IC.chat +
        '<span class="zu-v">' + fmt(s.total) + '</span>' +
        (SH.cache ? cachePct(s.cache_read, s.input) : '') +
        '<span class="zu-k">' + (s.turns || 0) + L('轮 ', ' turns · ') + (s.requests || 0) + L('次', ' req') + '</span></span>');
    }
    if (SH.tools && s && s.tools && s.tools.total) {
      g.push('<span class="zu-g" data-g="tools">' + IC.tool +
        '<span class="zu-v">' + s.tools.total + '</span>' +
        (s.tools.errors ? '<span class="zu-eb">' + IC.warn + s.tools.errors + '</span>' : '') + '</span>');
    }
    if (SH.today && p.today) {
      g.push('<span class="zu-g" data-g="today">' + IC.cal +
        '<span class="zu-v">' + fmt(p.today.total) + '</span></span>');
    }
    if (SH.sub && s && s.sub && (s.sub.total || s.sub.requests)) {
      g.push('<span class="zu-g" data-g="sub">' + IC.sub +
        '<span class="zu-v">' + fmt(s.sub.total) + '</span>' +
        (s.sub.active ? '<span class="zu-ok zu-dot">●</span>' : '') + '</span>');
    }
    /* ⚙ 紧跟条目之后（客户端同款，没有弹性空隙把它推到最右） */
    g.push('<span class="zu-g zu-gear" data-g="settings">⚙</span>');
    setGroups(g.join('<span class="zu-sep"></span>'));
    /* setGroups 会重建条目节点，把悬停/钉住的高亮带回来 */
    if (popKey) markOpen(bar.querySelector('[data-g="' + popKey + '"]'));
    if (pop && popKey) renderPop(popKey);
  }

  function LABELS() {
    return {
      speed: L('生成速度', 'Speed'), ctx: L('上下文占比', 'Context'),
      cache: L('缓存命中率', 'Cache hit'), session: L('本会话累计', 'Session'),
      tools: L('工具调用', 'Tools'), today: L('今日合计', 'Today'),
      sub: L('子代理', 'Subagents'), turn: L('本轮耗时', 'Turn time'),
    };
  }
  /** 显示项开关（窄屏抽屉与宽屏弹层共用；改完即时生效并记住） */
  function settingsHtml() {
    var labels = LABELS(), SH = cfg(), h = head(L('显示项', 'Shown items'));
    for (var k in labels) {
      h += '<label class="zu-set"><input type="checkbox" data-show="' + k + '"' +
        (SH[k] ? ' checked' : '') + '><span>' + labels[k] + '</span></label>';
    }
    h += '<div class="zu-note">' + L('改动立即生效并记住。语言跟随客户端界面语言。',
      'Applied immediately and remembered. Language follows the client UI.') + '</div>';
    return h;
  }

  function row(k, val) { return '<div class="zu-r"><span>' + k + '</span><span>' + val + '</span></div>'; }
  function head(t) { return '<h5>' + t + '</h5>'; }

  function renderPop(key) {
    if (!v_cur) return;
    var v = v_cur, s = v.s, p = v.p, h = '';
    if (key === 'settings') {
      h += settingsHtml();
    } else if (key === 'speed') {
      h += head(L('生成速度', 'Generation speed'));
      if (v.own && p.last) {
        h += row(L('速度', 'Speed'), tpsText(p.last.tps) + ' t/s');
        h += row(L('模型', 'Model'), String(p.last.model || '–'));
        h += row(L('耗时', 'Duration'), secs(p.last.duration_ms));
        h += row(L('首字延迟', 'TTFT'), secs(p.last.ttft_ms));
      } else h += row(L('暂无数据', 'No data'), '–');
      h += '<div class="zu-note">' + L('输出 tokens ÷ 生成耗时，取最近一次完成的请求。', 'Output tokens ÷ generation time, last completed request.') + '</div>';
    } else if (key === 'ctx') {
      h += head(L('上下文', 'Context'));
      h += row(L('已用', 'Used'), fmt(v.used));
      h += row(L('窗口总量', 'Window'), v.size ? fmt(v.size) : '–');
      h += row(L('占比', 'Usage'), v.cp == null ? '–' : v.cp.toFixed(1) + '%');
      h += '<div class="zu-bar2"><div class="zu-fill" style="width:' + Math.min(100, v.cp || 0) + '%;background:' + ctxTone(v.cp, v.exc, v.size) + '"></div></div>';
      h += '<div class="zu-note">' + L('窗口来源：', 'Window source: ') +
        (v.src === 'native' ? L('客户端原生下发', 'client (native)') : v.src === 'catalog' ? L('模型目录', 'model catalog') : L('未知', 'unknown')) + '</div>';
      if (v.exc) h += '<div class="zu-note" style="color:' + TONE.bad + '">' +
        L('上一次请求因超出上下文窗口被拒绝。建议回滚上一轮、换更大窗口的模型，或压缩 / 新开会话。',
          'The last request was rejected for exceeding the context window.') + '</div>';
    } else if (key === 'turn' && s && s.last_turn) {
      var lt = s.last_turn;
      h += head(L('本轮', 'This turn'));
      h += row(L('合计', 'Total'), fmt(lt.total));
      h += row(L('输入 / 输出', 'In / Out'), fmt(lt.input) + ' / ' + fmt(lt.output));
      h += row(L('缓存命中 / 写入', 'Cache r/w'), fmt(lt.cache_read) + ' / ' + fmt(lt.cache_write));
      h += row(L('思考', 'Reasoning'), fmt(lt.reasoning));
      h += row(L('请求 / 工具', 'Req / Tools'), (lt.requests || 0) + ' / ' + (lt.tool_calls || 0));
      h += row(L('耗时 / 首字', 'Dur / TTFT'), secs(lt.duration_ms) + ' / ' + secs(lt.ttft_ms));
    } else if (key === 'session' && s) {
      var hr = hitRate(s);
      h += head(L('会话累计', 'Session'));
      h += row(L('合计', 'Total'), fmt(s.total));
      h += row(L('输入 / 输出', 'In / Out'), fmt(s.input) + ' / ' + fmt(s.output));
      h += row(L('缓存命中', 'Cache read'), fmt(s.cache_read) + (hr == null ? '' : ' · ' + hr.toFixed(1) + '%'));
      h += row(L('缓存写入', 'Cache write'), fmt(s.cache_write));
      h += row(L('思考', 'Reasoning'), fmt(s.reasoning));
      h += row(L('轮次 / 请求', 'Turns / Req'), (s.turns || 0) + ' / ' + (s.requests || 0));
      h += row(L('重试', 'Retries'), String(s.retries || 0));
      if (s.code && s.code.add != null) {
        h += row(L('代码变更', 'Code'), '+' + s.code.add + ' / −' + s.code.del + ' · ' + s.code.files + L(' 文件', ' files'));
      }
    } else if (key === 'tools' && s && s.tools) {
      h += head(L('工具调用', 'Tools'));
      h += row(L('总计', 'Total'), String(s.tools.total));
      h += row(L('错误', 'Errors'), String(s.tools.errors || 0));
      (s.tools.list || []).slice(0, 10).forEach(function (t) {
        h += row(String(t.name) + (t.errors ? ' ✕' + t.errors : ''), String(t.count));
      });
    } else if (key === 'today' && p.today) {
      h += head(L('今日合计', 'Today'));
      h += row(L('合计', 'Total'), fmt(p.today.total));
      h += row(L('输入 / 输出', 'In / Out'), fmt(p.today.input) + ' / ' + fmt(p.today.output));
      h += row(L('缓存命中', 'Cache read'), fmt(p.today.cache_read));
      h += row(L('请求', 'Requests'), String(p.today.requests || 0));
      h += '<div class="zu-note">' + L('今天所有会话的合计，跨会话汇总。', 'All sessions today.') + '</div>';
    } else if (key === 'sub' && s && s.sub) {
      h += head(L('子代理', 'Subagents'));
      h += row(L('合计', 'Total'), fmt(s.sub.total));
      h += row(L('请求', 'Requests'), String(s.sub.requests || 0));
      if (s.sub.active) h += row(L('状态', 'Status'), '<span style="color:' + TONE.ok + '">● ' + L('运行中', 'running') + '</span>');
      (s.sub.list || []).slice(0, 10).forEach(function (x) {
        h += row(String(x.name || x.sid || '').slice(0, 30), fmt(x.total));
      });
      h += '<div class="zu-note">' + L('子代理用量独立统计，不计入会话累计。', 'Subagent usage is counted separately.') + '</div>';
    }
    pop.innerHTML = h;
    popKey = key;
    /* 先以 visibility:hidden 量出尺寸，再定位，避免出现一帧跳位。
     * 弹层是条的 absolute 后代（条的 backdrop-filter 使其成为包含块），所以 left 用
     * "相对条左缘"的偏移、纵向由 CSS 的 bottom:calc(100% + 8px) 固定；横向仍需夹进视口。 */
    pop.style.visibility = 'hidden';
    pop.classList.add('on');
    try {
      var br = bar.getBoundingClientRect();
      var t = bar.querySelector('[data-g="' + key + '"]');
      var tr = t ? t.getBoundingClientRect() : br;
      var pw = pop.offsetWidth;
      var rel = Math.round(tr.left - br.left);
      var lo = Math.round(-br.left) + 4, hi = Math.round(innerWidth - br.left - pw - 4);
      pop.style.left = Math.max(lo, Math.min(rel, Math.max(lo, hi))) + 'px';
    } catch (e) {}
    pop.style.visibility = '';
  }
  function hidePop() { if (pop) { pop.classList.remove('on'); popKey = ''; popPinned = false; } }
  /** 取消钉住（点条外 / Esc / 条被隐藏时走这里） */
  function unpinPop() { hidePop(); }

  var v_cur = null;
  function render(payload) {
    lastData = payload;
    if (!bar) return;
    zh = detectZh();        // 用户在设置里改语言后，一次轮询内跟上（下面统一重渲染）
    v_cur = pick(payload);
    if (!v_cur) return;
    if (mode === 'n') renderNarrow(v_cur); else renderWide(v_cur);
    if (sheet && sheet.classList.contains('on')) renderSheet();
  }

  /* ---------- 抽屉（窄屏） ---------- */
  function renderSheet() {
    if (!v_cur) return;
    var v = v_cur, s = v.s, p = v.p, out = '';
    if (!s) {
      // 草稿/空会话也要给「显示项」开关，否则用户在这个页面没法调（实测反馈：抽屉里只有一行字）
      out = '<div class="zu-note">' + L('当前会话还没有用量记录。', 'No usage recorded for this session yet.') + '</div>' +
        settingsHtml() +
        '<div class="zu-note">' + L('数据只读自本机 db.sqlite，不联网。',
          'Read-only from the local db.sqlite.') + '</div>';
      sheet.querySelector('.zu-body').innerHTML = out;
      return;
    }
    out += '<div class="zu-h"><span>' + L('上下文', 'Context') + '</span><span style="font-weight:600;color:' +
      ctxTone(v.cp, v.exc, v.size) + '">' + (v.cp == null ? '–' : v.cp.toFixed(1) + '%') + '</span></div>' +
      row(L('已用', 'Used'), fmt(v.used)) + row(L('窗口总量', 'Window'), v.size ? fmt(v.size) : '–') +
      '<div class="zu-bar2"><div class="zu-fill" style="width:' + Math.min(100, v.cp || 0) +
      '%;background:' + ctxTone(v.cp, v.exc, v.size) + '"></div></div>';
    if (v.exc) out += '<div class="zu-note" style="color:' + TONE.bad + '">' +
      L('上一次请求因超出上下文窗口被拒绝，建议回滚上一轮或换更大窗口的模型。',
        'Last request exceeded the context window.') + '</div>';

    var lt = s.last_turn;
    if (lt) out += '<div class="zu-h"><span>' + L('本轮', 'This turn') + '</span></div>' +
      row(L('合计', 'Total'), fmt(lt.total)) +
      row(L('输入 / 输出', 'In / Out'), fmt(lt.input) + ' / ' + fmt(lt.output)) +
      row(L('缓存命中 / 写入', 'Cache r/w'), fmt(lt.cache_read) + ' / ' + fmt(lt.cache_write)) +
      row(L('思考', 'Reasoning'), fmt(lt.reasoning)) +
      row(L('请求 / 工具', 'Req / Tools'), (lt.requests || 0) + ' / ' + (lt.tool_calls || 0)) +
      row(L('耗时 / 首字', 'Dur / TTFT'), secs(lt.duration_ms) + ' / ' + secs(lt.ttft_ms));

    if (v.own && p.last) out += '<div class="zu-h"><span>' + L('最近一次请求', 'Last request') + '</span></div>' +
      row(L('模型', 'Model'), String(p.last.model || '–')) +
      row(L('生成速度', 'Speed'), tpsText(p.last.tps) + ' t/s');

    var hr = hitRate(s);
    out += '<div class="zu-h"><span>' + L('会话累计', 'Session') + '</span></div>' +
      row(L('合计', 'Total'), fmt(s.total)) +
      row(L('输入 / 输出', 'In / Out'), fmt(s.input) + ' / ' + fmt(s.output)) +
      row(L('缓存命中 / 写入', 'Cache r/w'), fmt(s.cache_read) + ' / ' + fmt(s.cache_write)) +
      row(L('思考', 'Reasoning'), fmt(s.reasoning)) +
      row(L('轮次 / 请求', 'Turns / Req'), (s.turns || 0) + ' / ' + (s.requests || 0)) +
      row(L('重试', 'Retries'), String(s.retries || 0));
    if (hr != null) out += row(L('缓存命中率', 'Cache hit rate'), hr.toFixed(1) + '%');

    if (s.tools && s.tools.total) {
      out += '<div class="zu-h"><span>' + L('工具调用', 'Tools') + '</span><span style="font-weight:600;color:' +
        ((s.tools.errors || 0) ? TONE.bad : TONE.mute) + '">' + s.tools.total +
        ((s.tools.errors || 0) ? ' · ✕' + s.tools.errors : '') + '</span></div>';
      (s.tools.list || []).slice(0, 8).forEach(function (t) {
        out += row(String(t.name) + (t.errors ? ' ✕' + t.errors : ''), String(t.count));
      });
    }

    if (s.sub && (s.sub.total || s.sub.requests)) {
      out += '<div class="zu-h"><span>' + L('子代理', 'Subagents') + '</span>' +
        (s.sub.active ? '<span style="color:' + TONE.ok + '">● ' + L('运行中', 'running') + '</span>' : '') + '</div>' +
        row(L('合计', 'Total'), fmt(s.sub.total)) + row(L('请求', 'Requests'), String(s.sub.requests || 0));
      (s.sub.list || []).slice(0, 6).forEach(function (x) {
        out += row(String(x.name || x.sid || '').slice(0, 28), fmt(x.total));
      });
    }

    if (p.today) {
      out += '<div class="zu-h"><span>' + L('今日合计', 'Today') + '</span></div>' +
        row(L('合计', 'Total'), fmt(p.today.total)) +
        row(L('输入 / 输出', 'In / Out'), fmt(p.today.input) + ' / ' + fmt(p.today.output)) +
        row(L('请求', 'Requests'), String(p.today.requests || 0));
    }

    out += settingsHtml();
    out += '<div class="zu-note">' + L('数据只读自本机 db.sqlite，不联网。窗口来源：',
      'Read-only from the local db.sqlite. Window source: ') +
      (v.src === 'native' ? L('客户端原生下发', 'client (native)') :
        v.src === 'catalog' ? L('模型目录', 'model catalog') : L('未知', 'unknown')) + '</div>';
    sheet.querySelector('.zu-body').innerHTML = out;
  }

  /* ---------- 交互 ----------
   * 宽屏：悬停 = 临时看明细；点击 = 钉住（弹层是条的子树，鼠标可直接移进去点复选框）；
   *       再点同一组 / 点条外 / Esc = 收起。钉住后悬停其他组不改内容（与客户端"面板
   *       开着时条面不弹 tooltip"一致）。 */
  function markOpen(g) {
    var ns = bar.querySelectorAll('.zu-open');
    for (var i = 0; i < ns.length; i++) ns[i].classList.remove('zu-open');
    if (g) g.classList.add('zu-open');
  }
  function onBarClick(e) {
    if (mode === 'n') return openSheet();
    var g = e.target.closest('[data-g]');
    if (!g) return;
    var key = g.getAttribute('data-g');
    /* 只有"已钉住且是同一组"才收起；悬停打开后再点同一组应当是「钉住」而不是关掉
     * （旧实现按 popKey 判断 → 悬停已开，一点就关，这就是"用起来怪"的来源之一）。 */
    if (popPinned && popKey === key) { hidePop(); markOpen(null); return; }
    markOpen(g);
    renderPop(key);
    popPinned = true;
  }
  function onGroupHover(e) {
    if (mode !== 'w' || popPinned) return;
    var g = e.target.closest('[data-g]');
    if (!g) return;
    var key = g.getAttribute('data-g');
    if (key && key !== popKey) {
      markOpen(g);
      renderPop(key);
    }
  }
  function onPopChange(e) {
    var k = e.target && e.target.getAttribute && e.target.getAttribute('data-show');
    if (!k) return;
    cfg()[k] = e.target.checked ? 1 : 0;
    persistShow();
    if (v_cur) { if (mode === 'n') renderNarrow(v_cur); else renderWide(v_cur); }
  }
  function openSheet() {
    if (!sheet || !lastData || !v_cur) return;
    renderSheet();
    back.classList.add('on');
    sheet.classList.add('on');
    hidePop();
  }
  function closeSheet() {
    if (!sheet) return;
    back.classList.remove('on');
    sheet.classList.remove('on');
  }

  /* ---------- 启动 ---------- */
  /* 主题跟随（与客户端插件同源）：客户端主题切换时往 <html> 挂/摘 .dark，宽屏条的
   * 三档自调色与阴影按明暗切一套（其余颜色引用客户端变量，级联自动翻转）。
   * 观察类属性而不是 matchMedia：信号源与变量翻转同源，不会出现"变量已翻、档位没翻"。 */
  function applyTheme() {
    try {
      if (bar) bar.classList.toggle('zu-light', !document.documentElement.classList.contains('dark'));
    } catch (e) {}
  }
  function boot() {
    ensureDom();
    applyTheme();
    try {
      new MutationObserver(applyTheme).observe(document.documentElement, {
        attributes: true, attributeFilter: ['class'],
      });
    } catch (e) {}
    pop.addEventListener('change', onPopChange);
    sheet.addEventListener('change', onPopChange);
    (function loop() {
      if (stale()) return;
      track();
      // 条一旦移出视口/隐藏就收掉弹层，避免悬空
      if (popKey && !bar.classList.contains('on')) hidePop();
      requestAnimationFrame(loop);
    })();
  }
  if (document.body) boot();
  else document.addEventListener('DOMContentLoaded', boot, { once: true });

  window.__zusageUpdate = function (d) {
    if (stale() || !d || typeof d !== 'object') return;
    try { render(d); } catch (e) { window.__zusageDiag = String((e && e.stack) || e); }
  };
})();
