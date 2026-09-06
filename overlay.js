/* ZCode token 用量状态条 v28（渲染进程注入，自包含 IIFE）。
 * 形态：输入框视觉卡片正下方的悬浮胶囊条 —— 给卡片加 margin-bottom 上移让位，
 *       条 fixed 悬浮在卡片边框外、窗口底边上的空带里，rAF 每帧跟随，左缘与卡片对齐。
 *       （条不能放在输入框中心点所在矩形内：命中检测自遮挡 = 周期性闪烁，v12-v14 实测。）
 * 视觉（v28）：半透明胶囊底 + 组间 │ 分隔 + 上下文微进度条（三档色）+ 子代理呼吸灯；
 *       条面只放主数值，明细进 hover title。
 * 数据：主进程泵推 window.__zusageUpdate；显示项可在 ⚙ 面板配置（localStorage 持久化）。
 * 当前会话（v34）：泵按窗口注入 payload.mine（客户端渲染端经 IPC 向主进程上报的焦点会话 id，
 *       可为空串）；overlay 优先显示 mine 对应的池条目，池里还没有（新会话没数据）就显示
 *       零值 —— 绝不回退到"别的会话"的数字（多窗口共享 localStorage 时旧启发式必错，实测）。
 *       mine 缺失（辅助窗口/旧泵）才走 localStorage 键启发式 pickCurrent。
 * 热更新：主进程 mtime 检测 + new Function 语法校验后重注入；代际守卫防旧实例打架。
 * 诊断：异常与挂载信息写 window.__zusageDiag，由主进程泵取回写 diag-<n>.json。 */
(function () {
  if (window.__zusageOverlay) return;
  window.__zusageOverlay = true;
  /* 代际守卫：热更新后旧实例（interval/rAF 闭包无法外部清除）发现代号落后即永久罢工 */
  var MY_GEN = (window.__zusageGen = (window.__zusageGen || 0) + 1);
  function stale() { return MY_GEN !== window.__zusageGen; }

  var FATAL = (window.__zusageDiag = {});
  setTimeout(function () {
    try { main$(); } catch (e) {
      FATAL.fatal = String((e && e.stack) || e);
      var b = document.getElementById("zusage-bar");
      if (b) b.style.cssText = "position:fixed;right:16px;bottom:16px;font:12px monospace;color:#ff7a59;" +
        "background:#1a0f0d;border:1px solid #ff7a59;border-radius:6px;padding:4px 10px;z-index:2147483647";
    }
  }, 0);

  function main$() {
    var VERSION = "v53";   // 每轮递增；悬停状态条可见，用于确认热更新到达
    var LS = { show: "zusage3.show", ctxOv: "zusage3.ctxOv" };

    /* ---------- 状态 ---------- */
    var state = {
      show: { win: 1, ctx: 1, today: 1, turn: 0, sub: 1, tools: 1 },
      ctxOv: "", data: null, nativeCtx: 0,
      excActive: false,   // 当前会话处于"上下文超限被拒"状态（render 时按 picked 会话计算）
      excGone: false,     // 用户点 ✕ 关闭了本次气泡；超限解除后自动复位
    };
    function ls(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } }
    function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }
    try {
      var s = JSON.parse(ls(LS.show, "null"));
      if (s && typeof s === "object") {
        for (var k in state.show) if (k in s) state.show[k] = s[k] ? 1 : 0;
      }
      state.ctxOv = ls(LS.ctxOv, "");
    } catch (e) { }
    function persist() {
      lsSet(LS.show, JSON.stringify(state.show));
      lsSet(LS.ctxOv, state.ctxOv);
    }

    /* ---------- DOM ---------- */
    /* 样式表挂在 bar 内部而非 head：React 可能清理 head 里的外来 style，
     * CSS 一旦失效条就退化成 static 占位元素（"输入框下方空白"的历史根因）。 */
    var style = document.createElement("style");
    style.textContent =
      /* 胶囊条：半透明深底 + 微边框，与输入框视觉卡片形成层次；中文标签显式落雅黑 */
      "#zusage-bar{position:fixed;font:14px/1.3 Consolas,'Cascadia Mono',Menlo,'Microsoft YaHei UI',monospace;" +
      "color:#aab3c0;background:rgba(15,17,23,.78);border:1px solid rgba(255,255,255,.08);border-radius:7px;" +
      "box-shadow:0 2px 10px rgba(0,0,0,.35);padding:2px 10px;user-select:none;" +
      "display:flex;align-items:center;gap:9px;white-space:nowrap;" +
      /* 条不能 overflow:hidden：设置面板是条的子元素、展开在条外上方，裁剪会吞掉面板（v15"点设置看不到窗口"根因）。
       * 超宽截断由 #zu-main 自己负责。 */
      "z-index:50}" +
      "#zu-main{overflow:hidden;min-width:0;flex:0 1 auto;display:flex;align-items:center;gap:5px}" +
      /* zu-main 不得带 .it class：querySelectorAll('.it') 收集 tooltip 时会把它算进第 0 位，
       * tips 全部错位一位且末项为 null —— v31"悬停只有空胶囊/内容错位"的根因 */
      ".it{display:flex;align-items:center;gap:5px}" +
      ".sep{color:#39414f;flex:0 0 auto}" +
      ".k{color:#8a93a5}.v{color:#e6ebf3}" +
      ".pct{font-weight:700}.warm{color:#ffc53d}.hot{color:#ff7a59}.ok{color:#35c46f}" +
      ".exc{color:#ff2d55;text-shadow:0 0 6px rgba(255,45,85,.55)}" +
      ".dim{color:#6b7484}.btn{cursor:pointer;padding:0 4px;border-radius:3px;color:#77808f}" +
      ".btn:hover{color:#fff}" +
      "#zu-gear{flex:0 0 auto;padding:2px 6px;font-size:15px;border-radius:4px}" +
      /* 上下文微进度条：量感一眼可读，填充色随占比三档。
       * 填充块 background:currentColor —— 三档色类只给 color，填充靠 currentColor 着色
       * （v28 只写了 height 没写背景，填充块全透明 = "空条"根因）。 */
      ".cbar{display:inline-block;width:44px;height:6px;border-radius:3px;" +
      "background:rgba(255,255,255,.12);overflow:hidden;flex:0 0 auto}" +
      ".cbar>i{display:block;height:100%;border-radius:3px;background:currentColor}" +
      /* 子代理运行中呼吸灯 */
      "@keyframes zupulse{0%,100%{opacity:1}50%{opacity:.2}}" +
      ".dot{animation:zupulse 1.6s ease-in-out infinite}" +
      ".panel{position:absolute;bottom:calc(100% + 8px);left:0;background:rgba(18,20,27,.97);" +
      "border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:10px 12px;display:none;" +
      "flex-direction:column;gap:6px;font:13px/1.6 Consolas,Menlo,monospace;color:#cfd6e0;" +
      "box-shadow:0 6px 20px rgba(0,0,0,.5);min-width:270px;max-height:72vh;overflow:auto;" +
      "white-space:normal;z-index:2147483647}" +
      ".panel.open{display:flex}.panel label{display:flex;align-items:center;gap:7px;cursor:pointer}" +
      ".panel input[type=text]{width:90px;background:#0c0e13;border:1px solid rgba(255,255,255,.16);" +
      "color:#e8ecf2;border-radius:4px;padding:1px 5px;font:inherit}" +
      ".panel .hr{border-top:1px solid rgba(255,255,255,.1);margin:3px 0}" +
      ".panel .cap{color:#818b9c;margin-bottom:2px;font-size:11px;letter-spacing:.05em}" +
      ".panel .phead{font-weight:700;color:#e6ebf3;font-size:13px;margin-bottom:4px}" +
      ".panel .pver{color:#57c7ff;font-weight:400;font-size:11px;margin-left:6px}" +
      ".panel label em{font-style:normal;color:#77808f;font-size:11px;display:block;margin-left:21px}" +
      ".panel label{align-items:flex-start}" +
      ".panel input[type=checkbox]{accent-color:#57c7ff;margin-top:3px}" +
      ".panel label:hover{color:#e8ecf2}" +
      ".panel .pnote{font-size:11px;line-height:1.5;color:#77808f;margin-top:2px}" +
      /* 子代理明细面板（v49）：点击条目弹出的固定面板（与设置面板同机制，互斥打开）；
       * 面板不随鼠标消失，绕开悬停+tab 的全部几何问题。 */
      ".zu-sub{cursor:pointer}" +
      ".zu-sub:hover .v{color:#fff}" +
      ".panel.subp{width:400px;max-width:60vw}" +
      ".subrow{padding:4px 0;border-bottom:1px dashed rgba(255,255,255,.07)}" +
      ".subrow:last-child{border-bottom:none}" +
      ".subname{font-weight:700;color:#e6ebf3;overflow-wrap:anywhere}" +
      ".substat{font-size:11.5px;color:#9aa3b2}" +
      /* 自绘 tooltip（v31）：向上弹出（原生 title 方向不可控且会被窗口下缘遮挡），
       * 支持多 tab；white-space:pre-line 保留数据里的 \n 换行。
       * v32：fixed 挂 body —— 原 absolute 挂 bar，被页面消息流的层叠上下文盖住
       * （diag 实证 disp=block 但不可见），挂 body 用视口坐标独立定位。 */
      ".tip{position:fixed;background:rgba(18,20,27,.98);" +
      "border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:8px 11px;" +
      "font:12.5px/1.6 Consolas,'Microsoft YaHei UI',monospace;color:#cfd6e0;" +
      "box-shadow:0 6px 20px rgba(0,0,0,.5);white-space:pre-line;z-index:2147483646;" +
      "max-width:560px;display:none}" +
      ".ttabs{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;" +
      "border-bottom:1px solid rgba(255,255,255,.1);padding-bottom:6px}" +
      ".ttab{cursor:pointer;padding:1px 8px;border-radius:4px;background:rgba(255,255,255,.06);" +
      "color:#9aa3b2;white-space:nowrap}" +
      ".ttab:hover{color:#e6ebf3}" +
      ".ttab.on{background:rgba(87,199,255,.18);color:#57c7ff}" +
      ".tbody{max-height:60vh;overflow:auto}" +
      /* 超限告警气泡（v39）：挂 body 的独立浮层（与 .tip 同套路，免受条面重建影响），
       * 红边警示 + 建议步骤 + 可点击复制的会话 ID；user-select:text 允许手动选中兜底 */
      ".zusage-exc{position:fixed;max-width:470px;background:rgba(26,15,13,.97);" +
      "border:1px solid rgba(255,45,85,.55);border-radius:8px;padding:10px 13px;" +
      "font:13px/1.75 Consolas,'Microsoft YaHei UI',monospace;color:#e8ecf2;" +
      "box-shadow:0 6px 24px rgba(0,0,0,.55);white-space:normal;z-index:2147483647;" +
      "user-select:text;display:none}" +
      ".zusage-exc .xb-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px}" +
      ".zusage-exc .xb-title{font-weight:700;color:#ff2d55}" +
      ".zusage-exc .xb-close{cursor:pointer;color:#8a93a5;padding:0 3px;font-size:14px;line-height:1}" +
      ".zusage-exc .xb-close:hover{color:#fff}" +
      ".zusage-exc .xb-sid{color:#57c7ff;cursor:pointer;text-decoration:underline dotted}" +
      ".zusage-exc .xb-sid:hover{color:#8adcff}" +
      ".zusage-exc .xb-copied{color:#35c46f;font-size:12px;margin-left:6px;display:none}";

    /* 结构：文字项 + ⚙ 紧跟其后（无弹性空隙，不再推到最右）；面板绝对定位向上展开 */
    var bar = document.createElement("div");
    bar.id = "zusage-bar";
    bar.style.display = "none";   // 定位成功前不显示（避免占位）
    bar.innerHTML = '<span id="zu-main">…</span>' +
      '<span class="btn" id="zu-gear">⚙</span>';
    var panel = document.createElement("div");
    panel.className = "panel";
    panel.innerHTML =
      '<div class="phead">⚙ 状态条设置<span class="pver">' + VERSION + '</span></div>' +
      '<div class="cap">显示项（条面）</div>' +
      '<label><input type="checkbox" data-k="ctx"><span>上下文<em>进度条 + 百分比，颜色随占比变化</em></span></label>' +
      '<label><input type="checkbox" data-k="turn"><span>本轮<em>tokens / 次数 / 单次耗时 / 首字</em></span></label>' +
      '<label><input type="checkbox" data-k="win"><span>会话累计<em>当前会话 tokens / 轮数 / 次数</em></span></label>' +
      '<label><input type="checkbox" data-k="tools"><span>工具调用<em>当前会话，悬停看各工具明细与错误</em></span></label>' +
      '<label><input type="checkbox" data-k="today"><span>今日合计<em>今天所有会话的消耗</em></span></label>' +
      '<label><input type="checkbox" data-k="sub"><span>子代理<em>后台子代理消耗，点击条目看明细面板</em></span></label>' +
      '<div class="hr"></div>' +
      '<div class="cap">上下文窗口</div>' +
      '<label><input type="text" id="zu-ctxov" placeholder="自动(按模型)"><span class="dim">留空 = 自动（原生UI > 模型目录）</span></label>' +
      '<div class="hr"></div>' +
      '<div class="pnote">悬停条面各项看明细；点击子代理项看全部明细面板。数据源 ~/.zcode/cli/db/db.sqlite（只读），请求完成后才落库，数值随上次完成请求变化。</div>';
    bar.appendChild(panel);
    /* 子代理明细面板（v49）：与设置面板同款 .panel 机制，点击条目开关，互斥打开 */
    var subPanel = document.createElement("div");
    subPanel.className = "panel subp";
    bar.appendChild(subPanel);
    /* 自绘 tooltip：向上弹出、支持 tab（v45 起配空中走廊，移入点击不再被移开即隐掐断）；取代原生 title（方向不可控，在窗口底边会朝下被遮挡） */
    /* 自绘 tooltip 挂 body（fixed 视口坐标）：挂 bar 内会被消息流的层叠上下文盖住（v31 实证） */
    var tip = document.createElement("div");
    tip.className = "tip";
    tip.id = "zusage-tip";
    tip.style.display = "none";
    document.body.appendChild(tip);
    bar.appendChild(style);           // 样式随条走，不进 head（防清理）
    /* 内联兜底：即使样式表意外失效，定位行为也不退化。
     * z-index 50：压得过聊天列表/输入框外层容器（Tailwind z-20），又不盖权限菜单等弹层（实测值）。 */
    bar.style.position = "fixed";
    bar.style.zIndex = "50";
    document.body.appendChild(bar);

    /* ---------- 超限告警气泡（v39）：当前会话"上下文超限被拒"时自动弹出，随条定位 ---------- */
    var excBubble = document.createElement("div");
    excBubble.className = "zusage-exc";
    /* 热更新防重：上一实例的气泡可能残留（收尾清理只删 bar/tip），先移除旧的再挂新的 */
    try {
      document.querySelectorAll(".zusage-exc").forEach(function (el) { if (el !== excBubble) el.remove(); });
    } catch (e) { }
    /* 文案只进 textContent/静态 innerHTML；会话 ID 是动态数据，一律走 textContent 防注入 */
    excBubble.innerHTML =
      '<div class="xb-head"><span class="xb-title">⚠ 上下文超限</span>' +
      '<span class="xb-close" aria-label="关闭，本次不再提醒">✕</span></div>' +
      '<div class="xb-step">最近一次请求因超出上下文窗口容量被拒绝，本轮对话暂时无法继续。建议依次尝试：</div>' +
      '<div class="xb-step">① 回滚上一轮对话，去掉超限的那次请求后继续；</div>' +
      '<div class="xb-step">② 换用上下文窗口更大的模型继续，或压缩 / 精简本会话；</div>' +
      '<div class="xb-step">③ 仍无法解决时，新开一个对话，把下面的会话 ID（必要时连同工作区路径）发给它，' +
      '让新会话读取本会话的记录文件接手修复。</div>' +
      '<div>会话 ID：<span class="xb-sid"></span><span class="xb-copied">已复制</span></div>';
    document.body.appendChild(excBubble);
    var excSid = excBubble.querySelector(".xb-sid"), excCopied = excBubble.querySelector(".xb-copied");
    excSid.addEventListener("click", function () {
      if (stale() || !excSid.textContent) return;
      try {
        var ta = document.createElement("textarea");
        ta.value = excSid.textContent;
        ta.style.cssText = "position:fixed;left:-9999px;top:0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      } catch (e) { }
      excCopied.style.display = "inline";
      clearTimeout(excCopied.timer);
      excCopied.timer = setTimeout(function () { excCopied.style.display = "none"; }, 1500);
    });
    excBubble.querySelector(".xb-close").addEventListener("click", function () {
      if (stale()) return;
      state.excGone = true;
      syncExcBubble();
    });

    var $ = function (id) { return bar.querySelector(id); };
    var main = $("#zu-main"), gear = $("#zu-gear");

    /* ---------- 渲染 ---------- */
    function fmt(n) {
      n = n || 0;
      if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
      if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
      return String(n);
    }
    function sec(ms) { return ms ? (ms / 1000).toFixed(1) + "s" : "–"; }

    /* 超限判定：最近一次"上下文超限被拒"晚于最近成功请求 = 仍处于超限状态（该请求
     * status=error，不进任何 completed 统计，只能这样单独检测）。html() 与气泡共用。 */
    function excActive(s) {
      return !!(s && s.ctx_exc > 0 && s.ctx_exc >= (s.last_at || 0));
    }

    function cachePct(cache, input) {
      // 无原生 title：原生提示向下弹且与自绘 tooltip 双显闪烁（v33 移除），定义见各项 tooltip 明细
      return input > 0 ? '<span class="dim">' + Math.round(cache / input * 100) + "%</span>" : "";
    }

    /* 显示顺序：本轮 → 上下文 → 会话 → 工具 → 今日 → 子代理；token 后带缓存命中率。
     * v31：条面只放主数值；悬停改自绘 tooltip（向上弹、支持 tab），随渲染以 tips[]
     * 按 .it 顺序挂到元素 __tip——全部为字符串单页（{tabs:[...]} 多 tab 机制保留但
     * v49 起无条目使用：子代理已改点击面板，tooltip 不再承载 tab 切换）。
     * 缓存写入/思考/重试/错误均 >0 才显示，0 时不产生噪音。 */
    function fmtTime(ms) {
      var d = ms ? new Date(ms) : null;
      return d ? (d.getHours() < 10 ? "0" : "") + d.getHours() + ":" +
        (d.getMinutes() < 10 ? "0" : "") + d.getMinutes() + ":" +
        (d.getSeconds() < 10 ? "0" : "") + d.getSeconds() : "?";
    }
    function ioc(inp, out, cache, rea, cw) {   // 悬停明细行：输入/输出/缓存命中/缓存写入/思考
      return "输入 " + fmt(inp) + " / 输出 " + fmt(out) + " / 缓存命中 " + fmt(cache) +
        (cw > 0 ? " / 缓存写入 " + fmt(cw) : "") +
        (rea > 0 ? " / 思考 " + fmt(rea) : "");
    }
    function html(d) {
      var sess = d.session || {}, lt = d.last_turn || {}, today = d.today || {}, last = d.last || {};
      var tls = d.tools || { total: 0, errors: 0, list: [] };
      var items = [], tips = [];
      function it(inner, tip, cls) { items.push('<span class="it' + (cls ? " " + cls : "") + '">' + inner + "</span>"); tips.push(tip || null); }
      if (last.tps) {   // 最近一次请求的生成速度，置顶显示；随数值三档变色
        var tpsCls = last.tps >= 70 ? "ok" : last.tps >= 40 ? "warm" : "hot";
        it('<span class="' + tpsCls + '">' + last.tps + '</span><span class="dim">t/s</span>',
          "生成速度：最近完成请求的输出 tokens ÷ 生成耗时（首 token → 完成）\n≥70 t/s 绿色 · 40–70 黄色 · <40 红色");
      }
      if (state.show.ctx) {
        var cw = parseInt(state.ctxOv, 10) || state.nativeCtx || d.context_window || 0;
        var pct = cw ? sess.ctx / cw * 100 : 0;
        var exc = excActive(sess);
        /* 占比档位分两套：窗口 ≥100 万时同一百分比的绝对 token 量大，40/60 提前预警；其余维持 70/85。 */
        var big = cw >= 1000000;
        var cls = exc ? "exc" : pct >= (big ? 60 : 85) ? "hot" : pct >= (big ? 40 : 70) ? "warm" : "ok";
        var bar = cw ? '<span class="cbar"><i class="' + cls + '" style="width:' +
          Math.min(100, pct).toFixed(1) + '%"></i></span>' : "";
        it(bar + (cw ? '<span class="pct ' + cls + '">' + pct.toFixed(1) + "%</span>"
              : '<span class="v' + (exc ? " exc" : "") + '">' + fmt(sess.ctx) + "</span>"),
          "上下文：当前会话上下文大小（最近一次请求的总输入）÷ 窗口容量\n已用 " +
          fmt(sess.ctx) + " / 窗口 " + fmt(cw) +
          "\n颜色随占比：" + (big
            ? "≤40% 绿 · 40–60% 黄 · ≥60% 红（窗口 ≥100 万）"
            : "<70% 绿 · 70–85% 黄 · ≥85% 红") +
          " · 超限被拒=亮红闪烁" +
          (exc ? "\n⚠ 上下文超限：最近一次请求超出窗口容量被拒绝（" + fmtTime(d.ctx_exc) +
            "），需要压缩会话或新开会话" : ""));
      }
      if (state.show.turn) {
        it('<span class="k">本轮</span><span class="v">' + fmt(lt.total) + "</span>" +
          cachePct(lt.cache_read, lt.input) +
          '<span class="dim">' + (lt.requests || 0) + "次·耗时" + sec(last.duration_ms) +
          "·首字" + sec(last.ttft_ms) + "</span>",
          "本轮：最近一轮的 token 消耗（该轮共 " + (lt.requests || 0) + " 次模型请求）\n" +
          ioc(lt.input, lt.output, lt.cache_read, lt.reasoning, lt.cache_write) +
          "\n单次耗时 " + sec(last.duration_ms) + " · 首字 " + sec(last.ttft_ms) +
          " · 轮总耗时 " + sec(lt.duration_ms) +
          (lt.tool_calls ? " · 工具调用 " + lt.tool_calls : "") +
          ((lt.retries || lt.tool_errors) ? " · 重试 " + (lt.retries || 0) + " · 工具错误 " + (lt.tool_errors || 0) : ""));
      }
      if (state.show.win) {
        it('<span class="k">会话</span><span class="v">' + fmt(sess.total) + "</span>" +
          cachePct(sess.cache_read, sess.input) +
          '<span class="dim">' + (sess.turns || 0) + "轮 " + (sess.requests || 0) + "次</span>",
          "会话累计：当前会话全部请求的 token 消耗\n" + ioc(sess.input, sess.output, sess.cache_read, sess.reasoning, sess.cache_write) +
          "\n" + (sess.turns || 0) + " 轮 · " + (sess.requests || 0) + " 次请求" +
          (sess.tool_calls ? " · 工具调用 " + sess.tool_calls : "") +
          (sess.retries ? " · 重试 " + sess.retries : "") +
          (d.code && (d.code.add || d.code.del) ?
            "\n代码变更 +" + (d.code.add || 0) + " / -" + (d.code.del || 0) +
            (d.code.files ? "（" + d.code.files + " 文件）" : "") : ""));
      }
      if (state.show.tools) {
        var toolLines = [];
        (tls.list || []).forEach(function (t1) {
          toolLines.push(t1.name + " " + t1.count + "次 · " + sec(t1.duration_ms) +
            (t1.errors ? " · 错 " + t1.errors : ""));
        });
        it('<span class="k">工具</span><span class="v">' + (tls.total || 0) + "</span>" +
          (tls.errors ? '<span class="hot">' + tls.errors + "误</span>" : ""),
          "工具调用：当前会话的工具使用统计（按调用次数排序）\n" +
          (toolLines.length ? toolLines.join("\n") : "无工具调用记录"));
      }
      if (state.show.today) {
        it('<span class="k">今日</span><span class="v">' + fmt(today.total) + "</span>",
          "今日合计：今天所有会话的 token 消耗\n" + ioc(today.input, today.output, today.cache_read, today.reasoning, today.cache_write) +
          "\n" + (today.requests || 0) + " 次请求" + (today.retries ? " · 重试 " + today.retries : ""));
      }
      if (state.show.sub && d.sub && (d.sub.total || d.sub.active)) {
        /* v50：悬停 tooltip 只放汇总 + 打开面板的提示；各子代理明细在点击弹出的
         * 固定面板里用页签切换（面板固定不随鼠标，页签点击没有几何问题） */
        it('<span class="k">子代理</span><span class="v">' + fmt(d.sub.total) + "</span>" +
          (d.sub.active ? '<span class="ok dot">●</span>' : ""),
          "子代理：当前会话的后台子代理消耗（独立统计，不计入会话累计）\n" +
          ioc(d.sub.input, d.sub.output, d.sub.cache_read, d.sub.reasoning, d.sub.cache_write) +
          "，共 " + (d.sub.requests || 0) + " 次" + (d.sub.active ? " · 运行中" : "") +
          "\n点击条目打开面板，查看每个子代理的具体消耗", "zu-sub");
      }
      return { s: items.join('<span class="sep">│</span>') || '<span class="dim">全部显示项已关闭</span>', tips: tips };
    }

    /* 当前会话识别（v34 起 mine 优先，此处为兜底启发式）：ZCode 在 localStorage 的
     * "zcode-v4-last-session:v1:<工作区路径>" 键里保存每个工作区当前打开的会话 id。
     * 键随会话切换由应用更新（新开空会话时应用还会删键）；但 localStorage 全窗口共享、
     * 键对"自动新建的会话"更新滞后，多窗口下根本分不清哪个键属于本窗口 ——
     * 所以 v34 起正常路径是泵按窗口注入的 mine（渲染端经 IPC 上报的焦点会话），
     * 只有泵无该窗口信息时（辅助窗口/旧泵）才落到这里：取所有键值与 payload.recent
     * 的 sid 求交集；多候选取最近活跃（数值 last_at 比较，勿用 HH:MM:SS 字符串，跨午夜会排错）。
     * v32：键值即使不在 recent 池里也作为 want 返回（挂 window.__zusageWantSid），
     * 泵把它传给 zusage.py 强制纳入快照——刚开的新会话/超出 6+6 池的会话也能显示自己的数据。
     * 注意：会话标题不在可扫描的 DOM 文本节点里（顶栏标题扫描方案实测永远失败，勿回退）。 */
    var pickedSid = "", wsPrev = null, lastHtml = "";
    function pickCurrent(d) {
      var recent = d.recent || [];
      if (!recent.length) return null;
      var bySid = {}, kv = {};
      for (var i = 0; i < recent.length; i++) bySid[recent[i].sid] = recent[i];
      try {
        Object.keys(localStorage).forEach(function (k) {
          if (/^zcode-v4-last-session:/.test(k)) {
            var v = localStorage.getItem(k);
            if (v && /^[A-Za-z0-9_-]+$/.test(v)) kv[k] = v;
          }
        });
      } catch (e) { return null; }
      if (!Object.keys(kv).length) return null;
      var hit = {}, firstKeyVal = "";
      for (var k in kv) {
        if (bySid[kv[k]]) hit[k] = kv[k];
        if (!firstKeyVal) firstKeyVal = kv[k];
      }
      var switched = null;
      if (wsPrev) {
        for (var k2 in kv) {
          if (wsPrev[k2] && wsPrev[k2] !== kv[k2]) switched = kv[k2];   // 该工作区刚切换了会话
        }
      }
      wsPrev = kv;
      /* want = 当前真正打开的会话（切换优先，否则任一键值），交由泵强制补拉 */
      var want = switched || firstKeyVal;
      if (switched && bySid[switched]) return { sess: bySid[switched], want: want };
      var cands = [];
      for (var k3 in hit) cands.push(bySid[hit[k3]]);
      if (cands.length === 1) return { sess: cands[0], want: want };
      if (!cands.length) return { sess: null, want: want };
      var best = cands[0];   // 多候选：取最近活跃的
      for (var n = 1; n < cands.length; n++) {
        if ((cands[n].last_at || 0) > (best.last_at || 0)) best = cands[n];
      }
      return { sess: best, want: want };
    }

    /* 子代理查看视图识别：子代理会话的任务提示词会作为聊天气泡文本出现在消息流里，
     * 用 recent 中 is_sub 候选的标题前缀（前 30 字）在 DOM 文本里匹配。
     * （主会话的标题不在 DOM 文本里，但子代理视图的气泡是普通文本，可匹配。） */
    /* 零值会话 stub：当前会话在共享池里还没有数据行（新会话没发过消息/还没拉到）时，
     * 显示"当前会话的 0 值"。绝不回退到池里别的会话 —— 显示错会话的数字正是 v34 修的 bug。 */
    function stubFor(sid) {
      return { sid: String(sid || ""), title: "", active: false, turns: 0, requests: 0,
        input: 0, output: 0, reasoning: 0, cache_read: 0, cache_write: 0, total: 0,
        tool_calls: 0, retries: 0, ctx: 0, updated: "", last_at: 0, ctx_exc: 0,
        last_turn: {requests: 0, retries: 0, tool_calls: 0, tool_errors: 0, input: 0, output: 0,
          reasoning: 0, cache_read: 0, cache_write: 0, total: 0, duration_ms: 0, ttft_ms: 0},
        last: {duration_ms: 0, ttft_ms: 0, model: "", tps: 0},
        code: {add: null, del: null, files: null},
        tools: {total: 0, errors: 0, list: []},
        sub: {requests: 0, total: 0, input: 0, output: 0, cache_read: 0, reasoning: 0,
          cache_write: 0, active: false, list: []},
        context_window: 0, context_auto: false };
    }
    function render(d) {
      state.data = d;
      var mine = d.mine;   // 泵按窗口注入：string=本窗口焦点会话（空串=该窗口当前无会话），undefined=泵无该窗口信息
      var pc = null, p = null;
      if (typeof mine === "string") {
        pickedSid = mine;
        window.__zusageWantSid = mine;
        if (mine) {
          var rec = d.recent || [];
          for (var i = 0; i < rec.length; i++) if (rec[i].sid === mine) { p = rec[i]; break; }
          if (!p) p = stubFor(mine);
        } else {
          p = stubFor("");
        }
      } else {
        pc = pickCurrent(d);
        p = pc ? pc.sess : null;
        pickedSid = p ? p.sid : "";
        window.__zusageWantSid = (pc && pc.want) || "";   // 泵读取后让 zusage.py 强制纳入该会话
        if (!p) p = stubFor((pc && pc.want) || "");
      }
      state.excActive = excActive(p);   // 气泡的显示依据（track 每帧消费）
      var view = {
        session: p, last_turn: p.last_turn || {}, last: p.last || {},
        today: d.today, context_window: p.context_window, context_auto: p.context_auto,
        code: p.code, ctx_exc: p.ctx_exc, tools: p.tools,
        sub: p.sub || {requests: 0, total: 0, input: 0, output: 0, cache_read: 0, active: false, list: []},
      };
      state.lastSub = view.sub;   // 子代理明细面板的数据源（开关面板/数据推送刷新用）
      var h = html(view);
      /* 内容没变就不重建 DOM：泵每 1.5s 推送一次，无条件 innerHTML 会重建条面+重启呼吸灯
       * 动画+触发 tooltip 重画 = 悬停时周期性闪烁（v33 修复）。变化时才重建。 */
      if (h.s !== lastHtml) {
        lastHtml = h.s;
        main.innerHTML = h.s;
      }
      var els = main.querySelectorAll(".it");
      for (var i = 0; i < els.length; i++) els[i].__tip = h.tips[i] || null;
      /* 数据刷新时正在悬停：接管同位置的新元素继续显示。只调位置不重画内容——
       * 数字每 1.5s 都在变，重画就是闪烁；旧文本保持到鼠标下次移动为止。 */
      if (tipFor) {
        var nu = null;
        if (tipFor === main) nu = main;
        else if (tipFor.__n === els.length) nu = els[tipFor.__idx];
        if (nu && nu.__tip) { tipFor = nu; positionTip(nu, true); } else hideTip(false, "render-orphan");
        /* 接管失败（条目结构变化）也受总闸保护：鼠标仍在条上时保持旧 tooltip，
         * 下一次 mousemove 会重新判定该显示哪一项 */
      }
      if (subPanel.classList.contains("open")) buildSubPanel(view.sub);   // 面板开着：随推送实时刷新
    }
    window.__zusageUpdate = function (d) { if (stale()) return; try { render(d); } catch (e) { FATAL.updateErr = String((e && e.stack) || e); } };

    /* ---------- 自绘 tooltip：向上弹出（原生 title 方向不可控），支持 tab ---------- */
    var tipFor = null, tipTab = 0, lastMoveAt = 0, tipSig = "";
    var mouseInBar = false;   // 鼠标是否悬停在条面/tooltip/面板上（最近一次 mousemove 判定）
    var tipGraceTimer = 0;    // 越顶宽限定时器（v46）
    var tipStats = { mv: 0, shows: 0, hides: 0, last: "", lastHide: "", corridor: 0, grace: 0 };   // 诊断：随 mount diag 回写
    /* 隐藏总闸（v33.3）：鼠标还在条面上时，任何路径（数据刷新接管失败/输入框重建/
     * 瞬时不可见判定）都不得隐藏 tooltip——只有鼠标真正离开条面（left-bar，force）
     * 或条面整体隐藏（force）才允许。 */
    function hideTip(force, cause) {
      if (!force && mouseInBar) { tipStats.lastHide = "blocked:" + (cause || "?"); return; }
      if (tipGraceTimer) { clearTimeout(tipGraceTimer); tipGraceTimer = 0; }
      if (tip.style.display !== "none") tip.style.display = "none";
      tipFor = null;
      tipSig = "";
      tipStats.hides++;
      tipStats.lastHide = cause || "?";
    }
    function positionTip(el, keepTop) {
      var tr = tip.getBoundingClientRect(), ar = el.getBoundingClientRect();
      var left = ar.left + ar.width / 2 - tr.width / 2;
      left = Math.max(8, Math.min(left, Math.max(8, innerWidth - tr.width - 8)));
      /* v48：keepTop=切页签/数据刷新时保持顶边——页签行在盒顶，顶边不动则页签行不动，
       * 鼠标停在页签上不会因盒形变化被甩出去（外面是 iframe 静默区，甩出去就回不来了）。
       * 只有首次弹出才按条目重新锚定（悬空 8px）。 */
      var top = (keepTop && tip.style.top) ? parseFloat(tip.style.top) : 0;
      if (!isFinite(top) || top < 8) top = Math.max(8, ar.top - tr.height - 8);
      tip.style.left = Math.round(left) + "px";
      tip.style.top = Math.round(top) + "px";
    }
    function drawTip(el, keepTop) {
      var t = el && el.__tip;
      if (!t) { tip.style.display = "none"; tipFor = null; return; }
      var body;
      var tabIdx = 0;
      var sig;
      if (typeof t === "string") {
        sig = "s:" + t;
      } else {   // {tabs:[{name,text}]}
        tabIdx = Math.max(0, Math.min(tipTab, t.tabs.length - 1));
        sig = "t" + tabIdx + ":" + t.tabs[tabIdx].text;
      }
      /* 幂等：内容与页签都没变就只调位置，不重建 innerHTML（重建=悬停中每 1.5s 闪烁） */
      if (sig === tipSig && tip.style.display === "block") {
        positionTip(el, true);
        return;
      }
      tipSig = sig;
      if (typeof t !== "string") {
        var h = '<div class="ttabs">';
        for (var i = 0; i < t.tabs.length; i++)
          h += '<span class="ttab' + (i === tabIdx ? " on" : "") + '" data-i="' + i + '"></span>';
        h += '</div><div class="tbody"></div>';
        tip.innerHTML = h;
        var tabs = tip.querySelectorAll(".ttab");
        for (var j = 0; j < t.tabs.length; j++) tabs[j].textContent = t.tabs[j].name;
        body = tip.querySelector(".tbody");
        body.textContent = t.tabs[tabIdx].text;
        tip.style.display = "block";
        /* v48：多页签固定盒宽=各页签最大自然宽度（帽 560）——切换时盒宽不变、配合
         * keepTop 页签行一个像素不动，鼠标不会因盒缩被甩进 iframe 静默区（甩出去
         * 任何事件都收不到，宽限到点只能收场）。 */
        var maxW = 0;
        for (var m = 0; m < t.tabs.length; m++) {
          body.textContent = t.tabs[m].text;
          tip.style.width = "auto";
          maxW = Math.max(maxW, tip.getBoundingClientRect().width);
        }
        body.textContent = t.tabs[tabIdx].text;
        tip.style.width = Math.min(Math.ceil(maxW), 560) + "px";
      } else {
        tip.style.width = "";   // 单页恢复自适应宽（清掉页签盒的固定宽残留）
        tip.innerHTML = '<div class="tbody"></div>';
        body = tip.firstChild;
        body.textContent = t;
        tip.style.display = "block";
      }
      positionTip(el, keepTop);
    }
    function showTipFor(el) {
      tipFor = el;
      tipTab = 0;
      var its = main.querySelectorAll(".it");
      el.__idx = el === main ? -1 : Array.prototype.indexOf.call(its, el);
      el.__n = its.length;   // render 接管时校验条目数未变，数量变了 __idx 就不可信
      tipStats.shows++;
      tipStats.last = el === main ? "main" : String(el.textContent || "").slice(0, 12);
      drawTip(el, false);
    }
    /* 事件挂 document 捕获阶段（v32）：React/应用层可能在冒泡路上 stopPropagation，
     * 挂 main 的 mousemove 收不到（v31 实证：原生 title 有 hover、自绘 tip 无事件）。
     * stale() 守卫：热更新后旧实例监听器必须罢工，否则新旧两个 tip 同时工作。 */
    document.addEventListener("mousemove", function (e) {
      if (stale()) return;
      tipStats.mv++;
      if (Date.now() - lastMoveAt < 150) return;
      lastMoveAt = Date.now();
      var t = e.target;
      if (!t || !t.closest) return;
      if (t.closest("#zusage-tip")) { mouseInBar = true; cancelTipGrace(); return; }   // 鼠标移入 tooltip：保持
      if (t.closest(".panel")) {
        /* 面板上不保留条面 tooltip（v42）：开设置的路上触发过条目 tooltip 的话，
         * 这分支若不清掉它，鼠标在面板里它就永不消失。 */
        mouseInBar = true;
        if (tipFor) hideTip(true, "panel-hover");
        return;
      }
      if (!t.closest("#zusage-bar")) {
        /* 空中走廊（v45）：悬空缝是"条外且 tip 外"的真空带，采样事件落在这里会被误判
         * 成"移开"立即熄灭——鼠标在 tooltip 横向范围、底边下方 ~12px 内时视同仍在条上，
         * 穿行去 tooltip 点 tab 的路上不熄灭。 */
        if (tipFor && inTipCorridor(e.clientX, e.clientY)) {
          tipStats.corridor++;
          mouseInBar = true;
          cancelTipGrace();
          return;
        }
        /* 越顶宽限（v46）：mousemove 有 150ms 节流采样，快速上移时两个采样点之间能跨过
         * 整个 tooltip，采样点落在 tip 上方的死区——此前立即隐藏就把"正要进 tip"错杀成
         * "离开"。改挂 300ms 宽限：下一个采样落回 tip/走廊就取消（tip 仍可点 tab），
         * 确实继续远离才真正收起。横向/向下移出不享受宽限，依旧立即消失。 */
        if (tipFor && inTipColumn(e.clientX, e.clientY)) {
          tipStats.grace++;
          mouseInBar = true;   // 视同在途：总闸拦住 render-orphan 等路径趁机熄灯
          if (!tipGraceTimer) tipGraceTimer = setTimeout(function () {
            tipGraceTimer = 0;
            hideTip(true, "grace-expire");
          }, 300);
          return;
        }
        mouseInBar = false;
        if (tipFor) hideTip(true, "left-bar");   // v43：移开条面立即消失，不再留 300ms 缓冲
        return;
      }
      mouseInBar = true;
      cancelTipGrace();
      if (panel.classList.contains("open")) return;   // 面板开着：条面不弹 tooltip（弹出也会被面板盖住，只露边角）
      var el = t.closest(".it");
      if (el) {
        if (el !== tipFor) showTipFor(el);
      } else if (tipFor) {
        /* 条面内非条目区（项间隔/分隔符/⚙）：保持当前 tooltip 不动（此处刻意无操作）。
         * 慢速移动时命中目标在条目↔空隙间反复跳，若此处切换内容/隐藏
         * 就是"出现一下立刻消失"的闪烁（v33.2，概览 tooltip 因此废除） */
      }
    }, true);
    /* 鼠标甩出窗口外/窗口失焦：tooltip 立即收起（v43）。
     * v47 根因修正：ZCode 条面上方的聊天区是独立浏览上下文（iframe/webview，findComposer
     * 穿透同源 iframe 即佐证），光标从条面跨入其上时宿主文档会发 documentElement
     * mouseleave、且后续 mousemove 全部进子文档——diag 实证（mv 上万但 corridor/grace
     * 计数全 0、lastHide=win-leave）这就是"一向上移 tooltip 就消失"的根因：v43 把这种
     * 内部 leave 当成了甩出窗口。故 mouseleave 按退出点分流：
     *   · 退出点贴窗口边缘 = 真离开窗口 → 立即收；
     *   · 内部 leave 且退出点在 tip 横向列内（±30px）= 去 tooltip/回条面的穿行 → 400ms
     *     宽限（子上下文区域事件停摆，靠定时器兜底收回；tip/条面/走廊事件会取消它）；
     *   · 内部 leave 列外 = 横向走掉 → 立即收。 */
    function tipBoundaryHide() {
      if (stale()) return;
      mouseInBar = false;
      if (tipFor) hideTip(true, "win-leave");
    }
    window.addEventListener("blur", tipBoundaryHide, true);
    function tipLeaveAt(x, y) {
      if (stale() || !tipFor) return;
      if (x <= 1 || x >= innerWidth - 2 || y <= 1 || y >= innerHeight - 2) {
        tipBoundaryHide();
        return;
      }
      var r = tip.getBoundingClientRect();
      if (x >= r.left - 30 && x <= r.right + 30) {
        tipStats.grace++;
        mouseInBar = true;   // 在途：总闸拦住 render-orphan 等路径趁机熄灯
        cancelTipGrace();
        tipGraceTimer = setTimeout(function () {
          tipGraceTimer = 0;
          hideTip(true, "grace-expire");
        }, 400);
      } else {
        mouseInBar = false;
        hideTip(true, "left-bar");
      }
    }
    document.documentElement.addEventListener("mouseleave", function (e) {
      tipLeaveAt(e.clientX, e.clientY);
    }, true);
    /* 光标离开 tooltip 本体：上行越顶/下行回条面 → 列内宽限；横向 → 立即收（分流同上） */
    tip.addEventListener("mouseleave", function (e) {
      tipLeaveAt(e.clientX, e.clientY);
    }, true);
    tip.addEventListener("mouseenter", function () {
      if (stale()) return;
      cancelTipGrace();
    }, true);
    /* 空中走廊判定（v45）：tooltip 矩形横向 ±4px、纵向 tip 顶边到底边下方 12px
     * （覆盖条面顶边到 tooltip 底边的 8px 悬空缝）。只在 tipFor 存在时被调用。 */
    function inTipCorridor(x, y) {
      var r = tip.getBoundingClientRect();
      return x >= r.left - 4 && x <= r.right + 4 && y >= r.top && y <= r.bottom + 12;
    }
    /* 越顶宽限判定（v46）：tip 正上方 ±30px、高 80px 的柱形区——快速上移的采样点
     * 落进这里说明鼠标刚越过 tip 顶边，大概率下一拍就停在 tip 里。 */
    function inTipColumn(x, y) {
      var r = tip.getBoundingClientRect();
      return x >= r.left - 30 && x <= r.right + 30 && y >= r.top - 80 && y < r.top;
    }
    function cancelTipGrace() {
      if (tipGraceTimer) { clearTimeout(tipGraceTimer); tipGraceTimer = 0; }
    }
    tip.addEventListener("click", function (e) {
      if (stale()) return;
      var b = e.target && e.target.closest ? e.target.closest(".ttab") : null;
      if (!b || !tipFor) return;
      tipTab = +b.getAttribute("data-i") || 0;
      drawTip(tipFor, true);   // 切页签：顶边锚定，页签行不动（v48）
    });

    /* ---------- 设置面板 ---------- */
    function syncPanel() {
      panel.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
        cb.checked = !!state.show[cb.dataset.k];
      });
      $("#zu-ctxov").value = state.ctxOv;
    }
    gear.addEventListener("click", function (e) {
      e.stopPropagation();
      syncPanel();
      var opening = !panel.classList.contains("open");
      panel.classList.toggle("open");
      if (opening) {
        subPanel.classList.remove("open");   // 两面板同位重叠，互斥打开（v49）
        hideTip(true, "gear-open");   // 移向齿轮的路上可能触发过条目 tooltip，开面板时一并收掉
      }
    });
    panel.addEventListener("change", function (e) {
      var t = e.target;
      if (t.type === "checkbox") {
        state.show[t.dataset.k] = t.checked ? 1 : 0;
        persist();
      } else if (t.id === "zu-ctxov") {
        state.ctxOv = t.value.trim();
        persist();
      }
      if (state.data) render(state.data);
    });
    document.addEventListener("mousedown", function (e) {
      if (panel.classList.contains("open") && !bar.contains(e.target)) panel.classList.remove("open");
      if (subPanel.classList.contains("open") && !bar.contains(e.target)) subPanel.classList.remove("open");
    });

    /* ---------- 子代理明细面板（v49）：点击条目弹出的固定面板，替代悬停 tooltip ---------- */
    var subPanelTab = 0;   // 面板当前页签（0=汇总，i=第 i 个子代理）；数据刷新时保持
    function buildSubPanel(sub) {
      var list = (sub && sub.list) || [];
      if (subPanelTab > list.length) subPanelTab = 0;   // 列表变短则回汇总
      subPanel.innerHTML = "";
      var head = document.createElement("div");
      head.className = "phead";
      head.textContent = "子代理明细";
      var ver = document.createElement("span");
      ver.className = "pver";
      ver.textContent = "独立统计 · 不计入会话累计";
      head.appendChild(ver);
      subPanel.appendChild(head);
      if (!sub || (!sub.total && !sub.active)) {
        var empty = document.createElement("div");
        empty.className = "pnote";
        empty.textContent = "当前会话没有子代理记录";
        subPanel.appendChild(empty);
        return;
      }
      /* 页签行：汇总 + 每个子代理（复用 .ttabs/.ttab 样式）；动态名走 textContent 防注入 */
      var tabsRow = document.createElement("div");
      tabsRow.className = "ttabs";
      var labels = ["汇总"];
      list.forEach(function (s1) {
        /* task=派发时的 description（右侧"子智能体目录"同源）；无则退回 title/线路名 */
        var nm = String(s1.task || "").trim() || String(s1.title || "").trim() ||
          String(s1.agent || "sub").replace(/^zcode-/, "") + "…" + String(s1.sid).slice(-4);
        labels.push(nm.length > 12 ? nm.slice(0, 11) + "…" : nm);
      });
      labels.forEach(function (lb, i) {
        var tb = document.createElement("span");
        tb.className = "ttab" + (i === subPanelTab ? " on" : "");
        tb.setAttribute("data-i", String(i));
        tb.textContent = lb;
        tabsRow.appendChild(tb);
      });
      subPanel.appendChild(tabsRow);
      var body = document.createElement("div");
      body.className = "tbody";   // 复用 tooltip 内容容器的限高滚动
      subPanel.appendChild(body);
      function fillRow(row, s1) {
        var nm = String(s1.task || "").trim() || String(s1.title || "").trim() ||
          String(s1.agent || "sub").replace(/^zcode-/, "") + "…" + String(s1.sid).slice(-4);
        var n = document.createElement("div");
        n.className = "subname";
        n.textContent = nm + "（" + String(s1.agent || "subagent").replace(/^zcode-/, "") +
          " …" + String(s1.sid).slice(-4) + "）" + (s1.active ? " · 运行中" : "");
        var st = document.createElement("div");
        st.className = "substat";
        st.textContent = "总消耗 " + fmt(s1.total) + " · " + (s1.requests || 0) + " 次请求" +
          (s1.last ? " · 最后活动 " + fmtTime(s1.last) : "");
        var br = document.createElement("div");
        br.className = "substat dim";
        br.textContent = ioc(s1.input, s1.output, s1.cache_read, s1.reasoning, s1.cache_write);
        row.appendChild(n);
        row.appendChild(st);
        row.appendChild(br);
      }
      if (subPanelTab === 0) {
        var p1 = document.createElement("div");
        p1.className = "pnote";
        p1.textContent = ioc(sub.input, sub.output, sub.cache_read, sub.reasoning, sub.cache_write) +
          "，共 " + (sub.requests || 0) + " 次" + (sub.active ? " · 有子代理运行中" : "");
        body.appendChild(p1);
        if (!list.length) {
          var p2 = document.createElement("div");
          p2.className = "pnote";
          p2.textContent = "当前会话还没有子代理记录";
          body.appendChild(p2);
        }
      } else {
        var row = document.createElement("div");
        row.className = "subrow";
        fillRow(row, list[subPanelTab - 1]);
        body.appendChild(row);
      }
    }
    function toggleSubPanel() {
      var opening = !subPanel.classList.contains("open");
      subPanel.classList.toggle("open", opening);
      if (opening) {
        panel.classList.remove("open");   // 与设置面板互斥
        buildSubPanel(state.lastSub);
        hideTip(true, "sub-open");
      }
    }
    bar.addEventListener("click", function (e) {
      if (stale()) return;
      if (e.target && e.target.closest && e.target.closest(".zu-sub")) {
        e.stopPropagation();
        toggleSubPanel();
      }
    });
    subPanel.addEventListener("click", function (e) {
      if (stale()) return;
      var b = e.target && e.target.closest ? e.target.closest(".ttab") : null;
      if (!b) return;
      subPanelTab = +b.getAttribute("data-i") || 0;
      buildSubPanel(state.lastSub);   // 面板固定，切页签只换内容
    });

    /* ---------- 输入框查找（穿透 open shadow / 同源 iframe） ---------- */
    var COMPOSER_SEL = 'textarea, [contenteditable="true"], [role="textbox"], .ProseMirror, .ql-editor';
    var deepCache = { el: null, at: 0 };
    function visible(el) {
      var r = el.getBoundingClientRect();
      return r.width > 40 && r.height > 8 && r.bottom > 0 && r.top < innerHeight;
    }
    function deepFind() {
      if (deepCache.el && deepCache.el.isConnected && reallyVisible(deepCache.el)) return deepCache.el;
      if (Date.now() - deepCache.at < 1000) return null;
      deepCache.at = Date.now();
      var found = null;
      function scan(doc, depth) {
        if (found || !doc || depth > 8) return;
        try {
          doc.querySelectorAll(COMPOSER_SEL).forEach(function (el) {
            if (!found && visible(el) && el.getBoundingClientRect().top > innerHeight * 0.45) found = el;
            else if (found && el.getBoundingClientRect().top > found.getBoundingClientRect().top && visible(el)) found = el;
          });
        } catch (e) { }
        if (found) return;
        try {
          doc.querySelectorAll("*").forEach(function (el) {
            if (!found && el.shadowRoot) scan(el.shadowRoot, depth + 1);
          });
        } catch (e) { }
        try {
          doc.querySelectorAll("iframe").forEach(function (fr) {
            if (!found) { try { if (fr.contentDocument) scan(fr.contentDocument, depth + 1); } catch (e) { } }
          });
        } catch (e) { }
      }
      scan(document, 0);
      if (found) deepCache.el = found;
      return found;
    }
    /* 输入框是否真的露在屏幕上：中心点命中测试。
     * 设置页等覆盖层不卸载聊天 DOM 也不改几何，只能用 elementFromPoint 判断是否被盖住。 */
    /* 自己的浮层（设置面板/超限气泡/悬停 tooltip）展开在条上方、必然覆盖输入区中心：
     * 若算进遮挡判定，会进 隐藏→复显→再隐藏 的循环（v40 离线复现的"打开设置闪烁"根因；
     * v41 补 tooltip——会话/工具的长 tooltip 同样盖住输入框中心，悬停 0.4s 后闪一下且 tooltip 消失）。
     * 只豁免 track 的可见性路径；findComposer 找输入框时不用（ownOK 缺省 false）。 */
    function isOwnOverlay(el) {
      try { return !!(el && el.closest && el.closest("#zusage-tip,.panel,.zusage-exc")); } catch (e) { return false; }
    }
    function reallyVisible(el, ownOK) {
      if (!visible(el)) return false;
      var r = el.getBoundingClientRect();
      var hit;
      try { hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); } catch (e) { return true; }
      if (!hit) return false;
      if (hit === el || hit.contains(el) || el.contains(hit)) return true;   // 命中自身/祖先/内部装饰层
      if (ownOK && isOwnOverlay(hit)) return true;
      return false;
    }
    function findComposer() {
      var best = null;
      document.querySelectorAll("textarea").forEach(function (ta) {
        if (!reallyVisible(ta)) return;
        var r = ta.getBoundingClientRect();
        if (r.top < innerHeight * 0.45) return;   // 聊天输入框特征：在视口下半部（排除设置页等处元素）
        if (!best || r.top > best.getBoundingClientRect().top) best = ta;
      });
      return best || deepFind();
    }
    function collectDiag(extra) {
      if (everMounted) return;  /* 已成功挂载的窗口才允许写诊断，防后台空窗口覆盖 */
      if (Date.now() - (collectDiag.at || 0) < 5000) { if (extra) Object.assign(window.__zusageDiag, extra); return; }
      collectDiag.at = Date.now();
      /* 只在 window.__zusageDiag 本体上原地更新，绝不整体替换对象：
       * v14 曾用 Object.assign(d, ...) 换新对象，FATAL 仍指旧对象，
       * 之后写入的 mount/trackErr 全进孤儿对象，diag 文件永远看不到（实测翻车）。 */
      var d = window.__zusageDiag;
      d.time = new Date().toISOString();
      d.version = VERSION;
      d.href = String(location.href).slice(0, 90);
      var shallow = {};
      COMPOSER_SEL.split(",").forEach(function (sel) {
        try { shallow[sel.trim()] = document.querySelectorAll(sel).length; } catch (e) { }
      });
      d.shallow = shallow;
      d.iframes = document.querySelectorAll("iframe").length;
      var sr = 0;
      (function walk(doc) {
        if (!doc || !doc.querySelectorAll) return;
        var all;
        try { all = doc.querySelectorAll("*"); } catch (e) { return; }
        for (var i = 0; i < all.length; i++) {
          if (all[i].shadowRoot) { sr++; walk(all[i].shadowRoot); }
        }
      })(document);
      d.shadowRoots = sr;
      if (extra) Object.assign(d, extra);
    }

    /* 清理历史版本残留在 DOM 上的样式，恢复原状：
     * v12-v14 内嵌形态的 textarea padding（data-zu-pad）与 v18 前位置的旧标记 */
    function releasePads() {
      try {
        document.querySelectorAll("[data-zu-pad],[data-zu-old-pad]").forEach(function (el) {
          el.style.paddingBottom = el.dataset.zuPad || el.dataset.zuOldPad || "";
          delete el.dataset.zuPad;
          delete el.dataset.zuOldPad;
        });
        document.querySelectorAll("[data-zu-cardpad]").forEach(function (el) {
          el.style.marginBottom = el.dataset.zuCardPad || "";
          delete el.dataset.zuCardPad;
        });
      } catch (e) { }
    }
    try { releasePads(); } catch (e) { }

    /* 卡片下移让位：给视觉卡片加 margin-bottom 空出悬浮带（条在卡片正下方）。
     * React 重渲染会重置内联样式，track() 逐帧对比补回（v14 同款机制）。 */
    var CARD_MARGIN = "30px";
    function ensureCardPad() {
      if (!cardCache) return;
      try {
        if (cardCache.dataset.zuCardPad === undefined) cardCache.dataset.zuCardPad = cardCache.style.marginBottom || "";
        if (cardCache.style.marginBottom !== CARD_MARGIN) cardCache.style.marginBottom = CARD_MARGIN;
      } catch (e) { }
    }

    /* 从 textarea 向上找视觉卡片容器（有可见边框或背景的最近祖先）——条的锚点 + 读原生总量按钮 */
    function isVisualBox(el) {
      try {
        var cs = getComputedStyle(el);
        return cs.borderTopWidth !== "0px" || cs.backgroundColor !== "rgba(0, 0, 0, 0)";
      } catch (e) { return false; }
    }
    function cardOf(el) {
      var cr = el.getBoundingClientRect();
      var p = el.parentElement, last = el, i = 0;
      for (; p && p !== document.body && i < 8; p = p.parentElement, i++) {
        var r = p.getBoundingClientRect();
        if (r.height > cr.height * 8 + 80) break;          // 会话级大容器（高度异常）
        if (isVisualBox(p)) return p;                       // 第一个有边框/背景的层 = 视觉卡片
        last = p;
      }
      return last;
    }
    /* 读原生 UI 的上下文总量（输入框工具行按钮的文本/aria-label，如"…总量 1,000,000"）。
     * 服务端下发、自动跟随模型，优先级高于 catalog 查表和 config fallback。 */
    var nativeCtx = { val: 0, at: 0 };
    function readNativeCtx(card) {
      if (Date.now() - nativeCtx.at < 5000) return nativeCtx.val;
      nativeCtx.at = Date.now();
      var els = card.querySelectorAll("button, [aria-label], [title]");
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        var t = el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent || "";
        var m = t.match(/总量\s*([\d,，]+)/);
        if (m) {
          nativeCtx.val = parseInt(m[1].replace(/[,,]/g, ""), 10);
          return nativeCtx.val;
        }
      }
      return nativeCtx.val;
    }

    /* ---------- 定位：重活低频（找输入框/找卡片），逐帧只做矩形跟随 ---------- */
    var composer = null, cardCache = null, everMounted = false, lastMountDiagAt = 0;
    var curDisplay = "none", lastPos = [-1, -1], hideSince = 0;

    function setComposer(el) {
      releasePads();   // 旧卡片的让位边距先还原，新卡片马上重新加
      composer = el;
      cardCache = null;
      if (composer) {
        var c = cardOf(composer);
        if (c && c !== document.body) cardCache = c;
      }
      if (cardCache) ensureCardPad();
    }
    function heavy() {
      if (stale()) return;
      var el = findComposer();
      if (el && el !== composer) setComposer(el);
      else if (!el && composer && !composer.isConnected) setComposer(null);
      if (!composer) collectDiag();   // 一直找不到输入框的窗口：周期性写环境诊断（everMounted 门防覆盖）
      if (composer) {
        if (!cardCache || !cardCache.isConnected) {
          var c = cardOf(composer);
          cardCache = c && c !== document.body ? c : null;
        }
        if (cardCache) {
          ensureCardPad();   // React 重渲染可能重建卡片/重置内联边距，对比后再补
          state.nativeCtx = readNativeCtx(cardCache);
        }
      }
      if (state.data) {
        var msid = typeof state.data.mine === "string" ? state.data.mine : null;
        var pSid;
        if (msid !== null) pSid = msid;   // mine 模式：泵会在会话切换时推新 payload，这里只需同步 pickedSid
        else {
          var pc = pickCurrent(state.data);
          pSid = pc ? (pc.sess ? pc.sess.sid : pc.want) : "";
        }
        if (pSid !== pickedSid) render(state.data);   // 切换了会话：立即按缓存 payload 重渲染
      }
    }
    function hideBar() {
      if (curDisplay !== "none") { bar.style.display = "none"; curDisplay = "none"; }
      hideTip(true, "bar-hidden");   // 条面整体隐藏时 tooltip 必须跟着走（force 绕过总闸）
    }
    /* 超限气泡同步（track 每帧调）：位置贴条上沿、左缘对齐；条隐藏/超限解除/已关闭则藏。
     * exc 解除时复位 excGone，下次再超限会重新弹。display 切换与测量在同一同步块内
     * 完成后才绘制，无首帧闪位。 */
    function syncExcBubble() {
      var show = state.excActive && !state.excGone && curDisplay === "flex";
      if (!show) {
        if (!state.excActive) state.excGone = false;
        if (excBubble.style.display !== "none") excBubble.style.display = "none";
        return;
      }
      var sidStr = pickedSid || "（未知）";
      if (excSid.textContent !== sidStr) excSid.textContent = sidStr;
      if (excBubble.style.display !== "block") excBubble.style.display = "block";
      var br = bar.getBoundingClientRect();
      var r = excBubble.getBoundingClientRect();
      var left = Math.max(8, Math.round(Math.min(br.left, innerWidth - r.width - 8)));
      var top = Math.max(8, Math.round(br.top - r.height - 8));
      if (excBubble.style.left !== left + "px") excBubble.style.left = left + "px";
      if (excBubble.style.top !== top + "px") excBubble.style.top = top + "px";
    }
    /* 可见性判定（带卡片豁免）：输入框视觉卡片内部的装饰层（占位符/镜像/焦点层等）
     * 瞬时盖到输入框中心不算"被盖住"——逐帧严格判定会造成显示/隐藏来回横跳（闪烁）；
     * 设置页等真正的覆盖层不在卡片内，仍判为盖住。
     * 条外挂在输入框上方，不参与对输入框中心的遮挡（v12-v14 内嵌形态的自遮挡闪烁根因）。 */
    function coverOK(el) {
      var r = el.getBoundingClientRect();
      var hit;
      try { hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); } catch (e) { return true; }
      if (!hit) return false;
      if (hit === el || hit.contains(el) || el.contains(hit)) return true;
      if (cardCache && cardCache.contains(el) && cardCache.contains(hit)) return true;
      if (isOwnOverlay(hit)) return true;   // 自己的面板/气泡盖住输入框中心不算被盖（v40）
      return false;
    }
    function track() {
      if (stale()) return;   // 旧实例罢工，把舞台让给新实例
      try {
        syncExcBubble();   // 超限气泡：显示/隐藏/贴条定位（内部自判状态，隐藏分支零开销）
        if (!composer || !composer.isConnected) { hideSince = 0; hideBar(); return; }
        if (cardCache && cardCache.style.marginBottom !== CARD_MARGIN) ensureCardPad();   // 流式期间 React 重置内联边距时立刻补回
        var r = composer.getBoundingClientRect();
        var on = r.width > 60 && r.height > 14 && r.bottom > 0 && r.top < innerHeight &&
          (reallyVisible(composer, true) || coverOK(composer));
        if (!on) {
          // 迟滞：连续 400ms 判定不可见才隐藏，瞬时失败（流式装饰层等）不闪
          if (!hideSince) hideSince = Date.now();
          if (Date.now() - hideSince > 400) { hideBar(); }
          return;
        }
        hideSince = 0;
        if (curDisplay !== "flex") { bar.style.display = "flex"; curDisplay = "flex"; }
        /* 悬浮在输入框视觉卡片正下方（卡片被 margin 上移让位），胶囊左缘与卡片左缘对齐 */
        var anchor = cardCache || composer;
        var ar = anchor.getBoundingClientRect();
        var left = Math.round(ar.left);
        var top = Math.round(ar.bottom + 4);
        top = Math.max(8, Math.min(top, innerHeight - bar.offsetHeight - 2));
        if (left !== lastPos[0] || top !== lastPos[1]) {
          bar.style.left = left + "px"; bar.style.top = top + "px";
          lastPos = [left, top];
        }
        var maxW = Math.max(60, Math.round(ar.width - 24));
        if (bar.style.maxWidth !== maxW + "px") bar.style.maxWidth = maxW + "px";
        everMounted = true;
        if (Date.now() - lastMountDiagAt > 5000) {
          lastMountDiagAt = Date.now();
          var br = bar.getBoundingClientRect(), bh = null;
          try { bh = document.elementFromPoint(br.left + br.width / 2, br.top + br.height / 2); } catch (e) { }
          FATAL.mount = {
            version: VERSION, mode: "below-card",
            tip: (function () { return { stats: tipStats, disp: tip.style.display, barRect: (function () { var b = bar.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; })() }; })(),
            composerRect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
            anchorRect: [Math.round(ar.left), Math.round(ar.top), Math.round(ar.width), Math.round(ar.height)],
            barRect: [Math.round(br.left), Math.round(br.top), Math.round(br.width), Math.round(br.height)],
            /* 条中心命中元素：若被应用层盖住（z 序问题）此处直接暴露 */
            barHit: bh ? bh.tagName + "." + String(bh.className).slice(0, 40) : String(bh),
            bottomGap: Math.round(innerHeight - ar.bottom),
            nativeCtx: state.nativeCtx,
            picked: pickedSid,
            exc: state.excActive ? (state.excGone ? "on-gone" : "on") : 0,
            href: String(location.href).slice(0, 120),
            lsVals: (function () {
              try {
                var out = [];
                Object.keys(localStorage).forEach(function (k) {
                  out.push(k + " = " + String(localStorage.getItem(k)).slice(0, 90));
                });
                var wk = [];
                Object.keys(window).forEach(function (k) {
                  if (/zcode|session|store|state|tab/i.test(k)) wk.push(k);
                });
                out.push("WINDOW_KEYS=" + wk.join("|").slice(0, 300));
                return out.join("\n").slice(0, 7000) || "(none)";
              } catch (e) { return "ERR:" + e; }
            })(),
            lastSessionVals: (function () {
              try {
                var out = [];
                Object.keys(localStorage).forEach(function (k) {
                  if (/last-session/i.test(k)) out.push(k.slice(22) + "=" + String(localStorage.getItem(k)).slice(0, 50));
                });
                return out.join(" | ").slice(0, 1000) || "(none)";
              } catch (e) { return "ERR:" + e; }
            })(),
          };
        }
      } catch (e) {
        if (Date.now() - (track.errAt || 0) > 5000) {
          track.errAt = Date.now();
          FATAL.trackErr = String((e && e.stack) || e);
        }
      } finally {
        requestAnimationFrame(track);   // 自调度循环：异常绝不能杀死循环（setInterval 无此问题，rAF 有）
      }
    }

    setInterval(heavy, 600);
    heavy();
    requestAnimationFrame(track);

    syncPanel();
    render({ session: {}, last_turn: {}, today: {}, context_window: 0 });
  }
})();
