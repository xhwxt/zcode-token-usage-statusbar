/**
 * ZCode 用量状态条 · Web 版（一份实现，两套布局）
 *
 *   窄屏 <768px（手机，用户 2026-10-07 选定「① 极简一行」）
 *     ⚡ 86 t/s        ◔ 31.7%        Σ 1.45M
 *     点整条 → 底部抽屉看全量
 *
 *   宽屏 ≥768px（桌面）
 *     ⚡86 t/s │ ◔31.7% │ ⧗18.4s 1.4s │ 💬1.45M 86% 23轮 │ 🔧142 ② │ ☀3.42M │ ⑂236.4K ● │ ⚙
 *     悬停/点某一组 → 条上方弹该组明细
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
  var H_NARROW = 22, H_WIDE = 28, GAP = 4;

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
    w: { speed: 1, ctx: 1, cache: 1, turn: 0, session: 1, tools: 1, today: 1, sub: 1 },
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
  function fmt(n) {
    n = Number(n) || 0;
    if (n < 0) n = 0;
    if (n < 1000) return String(Math.round(n));
    if (n < 1e6) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
    if (n < 1e9) return (n / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M';
    return (n / 1e9).toFixed(2) + 'B';
  }
  function secs(ms) { return ((Number(ms) || 0) / 1000).toFixed(1) + 's'; }
  function pct(used, size) {
    if (!size || size <= 0) return null;
    return Math.min(999, Math.max(0, (used / size) * 100));
  }
  function tpsText(v) {
    if (v == null || !isFinite(v)) return '–';
    return v >= 100 ? String(Math.round(v)) : v.toFixed(1);
  }

  /* ---------- 主题色（客户端语义变量 + 字面兜底） ---------- */
  var TONE = {
    ok: 'var(--color-green-500,#3ba272)',
    warn: 'var(--color-amber-500,#d99a2b)',
    bad: 'var(--color-red-400,#e5484d)',
    mute: 'var(--color-foreground-subtle,#9aa3b2)',
    faint: 'var(--color-foreground-subtlest,#6f7785)',
    text: 'var(--color-foreground,#e8eaed)',
    bg: 'var(--color-background,#161616)',
    border: 'var(--color-border,rgba(255,255,255,.12))',
  };
  var FONT = '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
  var MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,"Cascadia Mono",monospace';

  function speedTone(v) { return v == null ? TONE.mute : v >= 70 ? TONE.ok : v >= 40 ? TONE.warn : TONE.bad; }
  function ctxTone(p, exc) { return exc ? TONE.bad : p == null ? TONE.mute : p >= 85 ? TONE.bad : p >= 70 ? TONE.warn : TONE.ok; }
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
    /* 宽屏：胶囊底 + 发丝分隔 */
    '.zu-bar.w{height:' + H_WIDE + 'px;gap:0;padding:0 8px;border-radius:9px;',
    'background:color-mix(in srgb,' + TONE.bg + ' 78%,transparent);',
    'border:1px solid ' + TONE.border + ';backdrop-filter:blur(8px);',
    '-webkit-backdrop-filter:blur(8px)}',
    '.zu-g{display:inline-flex;align-items:center;gap:4px;min-width:0;white-space:nowrap;',
    'overflow:hidden;text-overflow:ellipsis}',
    '.zu-bar.w .zu-g{padding:0 8px;height:100%;border-radius:7px;cursor:default;transition:background .12s}',
    '.zu-bar.w .zu-g:hover,.zu-bar.w .zu-g.zu-open{background:color-mix(in srgb,' + TONE.text + ' 10%,transparent)}',
    '.zu-bar.w .zu-g+.zu-g{box-shadow:inset 1px 0 color-mix(in srgb,' + TONE.text + ' 12%,transparent)}',
    '.zu-n{font-weight:600}',
    '.zu-u{opacity:.6;font-size:10.5px}',
    '.zu-grow{flex:1 1 auto}',
    '.zu-mini{width:22px;height:2.5px;border-radius:2px;background:color-mix(in srgb,' + TONE.text + ' 16%,transparent);overflow:hidden;flex:none}',
    '.zu-mini>i{display:block;height:100%;border-radius:2px;transition:width .3s}',
    '@keyframes zu-pulse{0%,100%{opacity:1}50%{opacity:.35}}',
    '.zu-exc{animation:zu-pulse 1s ease-in-out infinite}',
    /* 明细弹层（宽屏）：条上方 */
    '.zu-pop{position:fixed;z-index:21;min-width:190px;max-width:340px;',
    'background:' + TONE.bg + ';border:1px solid ' + TONE.border + ';border-radius:10px;',
    'padding:10px 12px;font:12px/1.6 ' + FONT + ';color:' + TONE.text + ';',
    'box-shadow:0 10px 30px rgba(0,0,0,.42);display:none}',
    '.zu-pop.on{display:block}',
    '.zu-pop h5{margin:0 0 6px;font-size:11px;font-weight:600;color:' + TONE.faint + ';letter-spacing:.3px}',
    '.zu-r{display:flex;justify-content:space-between;gap:14px}',
    '.zu-r>span:first-child{color:' + TONE.mute + '}',
    '.zu-r>span:last-child{font-weight:600;font-variant-numeric:tabular-nums}',
    '.zu-set{display:flex;align-items:center;gap:8px;padding:2px 0;cursor:pointer;user-select:none}',
    '.zu-set input{margin:0;cursor:pointer}',
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
    gear: svg('<path d="M14 17H5"/><path d="M19 7h-9"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>'),
    turn: svg('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
  };

  /* ---------- 运行状态 ---------- */
  var bar = null, pop = null, sheet = null, back = null;
  var lastData = null, mode = '';
  var composer = null, cardCache = null, hideSince = 0, lastPos = [0, 0];
  var appliedPad = 0, basePad = 0, baseCaptured = false;
  var padKind = 0;        // 0=未标定 1=下方锚定（pad 会顶起卡片） 2=普通流（pad 只在下方腾空间）
  var padPreBottom = 0;   // 施加 pad 前的卡片底边，供下一帧标定布局类型
  var lastVh = 0, lastVw = 0;
  var popKey = '';

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
      bar.addEventListener('mouseleave', function () { setTimeout(hidePop, 120); });
      pop = document.createElement('div');
      pop.className = 'zu-pop';
      pop.setAttribute('role', 'tooltip');
      /* 必须挂在 body 而不是 bar 里：renderWide/renderNarrow 每次都 bar.innerHTML=…，
         挂在 bar 内的弹层会被一起销毁（实测：悬停永远打不开）。 */
      document.body.appendChild(pop);
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

  /**
   * 让位量自适应：够放就不占位，不够才补差额。
   * cardBottom0 剥掉我们自己加的量 → 无论移动布局把底部留白做成 0 / 34px(安全区) / 其它，
   * 结果都不会叠加成大缝，也不会压住内容。
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
      var need = (mode === 'n' ? H_NARROW : H_WIDE) + GAP;

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

      // R = 卡片下方的「自然」余量，已剥掉我们自己造成的位移
      var R = padKind === 1 ? innerHeight - ar.bottom - appliedPad : innerHeight - ar.bottom;
      var target = Math.max(0, need - R);
      if (Math.abs(target - appliedPad) > 2) {
        if (appliedPad === 0) padPreBottom = ar.bottom;   // 记录施加前的底边，供下一帧标定
        appliedPad = target;
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
      if (want !== mode) { mode = want; bar.className = 'zu-bar ' + mode; lastPos = [0, 0]; hidePop(); }
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
      var left = Math.round(ar.left) + (mode === 'w' ? 0 : 2);
      var top = Math.max(4, Math.min(Math.round(ar.bottom + GAP), innerHeight - (mode === 'n' ? H_NARROW : H_WIDE) - 2));
      if (left !== lastPos[0] || top !== lastPos[1]) {
        bar.style.left = left + 'px';
        bar.style.top = top + 'px';
        lastPos = [left, top];
      }
      var maxW = Math.max(80, Math.round(ar.width - (mode === 'n' ? 8 : 0)));
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

  /* ---------- 渲染：窄屏与宽屏共用同一份条目清单 ---------- *
   * 用户反馈（2026-10-07 真机）：
   *   1) 不要再画环 —— 输入框工具栏本来就有原生的上下文环，重复；
   *   2) 一行有这么大空间，多显示点内容。
   * 所以窄屏与宽屏用同一份条目构建，只是呈现疏密不同。 */
  function buildItems(v) {
    var s = v.s, p = v.p, out = [], hr;
    var cTone = ctxTone(v.cp, v.exc);
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
    bar.innerHTML = g.join('');
    bar.title = L('点击查看明细与显示项', 'Tap for details');
  }

  function renderWide(v) {
    var items = buildItems(v), g = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      g.push('<span class="zu-g' + (it.exc ? ' zu-exc' : '') + '" data-g="' + it.k + '">' +
        (it.bar != null ? miniBar(it.bar, it.bc) : (it.ic || '')) +
        '<span class="zu-n" style="color:' + it.c + '">' + it.v + '</span>' +
        (it.u ? '<span class="zu-u">' + it.u + '</span>' : '') +
        (it.err ? '<span class="zu-n" style="color:' + TONE.bad + '">✕' + it.err + '</span>' : '') +
        (it.dot ? '<span style="color:' + TONE.ok + '">●</span>' : '') + '</span>');
    }
    g.push('<span class="zu-grow"></span>');
    g.push('<span class="zu-g" data-g="settings" style="cursor:pointer">' + I.gear + '</span>');
    bar.innerHTML = g.join('');
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
      h += '<div class="zu-bar2"><div class="zu-fill" style="width:' + Math.min(100, v.cp || 0) + '%;background:' + ctxTone(v.cp, v.exc) + '"></div></div>';
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
    /* 先以 visibility:hidden 量出尺寸，再定位，避免出现一帧跳位 */
    pop.style.visibility = 'hidden';
    pop.classList.add('on');
    try {
      var br = bar.getBoundingClientRect();
      var t = bar.querySelector('[data-g="' + key + '"]');
      var tr = t ? t.getBoundingClientRect() : br;
      var pw = pop.offsetWidth, ph = pop.offsetHeight;
      var left = Math.max(4, Math.min(Math.round(tr.left), innerWidth - pw - 4));
      var top = Math.max(4, Math.round(br.top - ph - 8));
      pop.style.left = left + 'px';
      pop.style.top = top + 'px';
    } catch (e) {}
    pop.style.visibility = '';
  }
  function hidePop() { if (pop) { pop.classList.remove('on'); popKey = ''; } }

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
      ctxTone(v.cp, v.exc) + '">' + (v.cp == null ? '–' : v.cp.toFixed(1) + '%') + '</span></div>' +
      row(L('已用', 'Used'), fmt(v.used)) + row(L('窗口总量', 'Window'), v.size ? fmt(v.size) : '–') +
      '<div class="zu-bar2"><div class="zu-fill" style="width:' + Math.min(100, v.cp || 0) +
      '%;background:' + ctxTone(v.cp, v.exc) + '"></div></div>';
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

  /* ---------- 交互 ---------- */
  function onBarClick(e) {
    if (mode === 'n') return openSheet();
    var g = e.target.closest('[data-g]');
    if (!g) return;
    var key = g.getAttribute('data-g');
    if (popKey === key) hidePop();
    else { renderPop(key); g.classList.add('zu-open'); }
  }
  function onGroupHover(e) {
    if (mode !== 'w') return;
    var g = e.target.closest('[data-g]');
    if (!g) return;
    var key = g.getAttribute('data-g');
    if (key && key !== popKey) {
      bar.querySelectorAll('.zu-open').forEach(function (n) { n.classList.remove('zu-open'); });
      g.classList.add('zu-open');
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
  function boot() {
    ensureDom();
    pop.addEventListener('change', onPopChange);
    sheet.addEventListener('change', onPopChange);
    pop.addEventListener('click', function (e) { e.stopPropagation(); });
    (function loop() {
      if (stale()) return;
      track();
      // 条一旦移出视口就收掉弹层，避免悬空
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
