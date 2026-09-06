# -*- coding: utf-8 -*-
"""README 功能导览图 · 逐张截图脚本（带真实交互态 + 自动裁空白）
对 docs/tour/tour.html 的每个迷你窗口（真实 overlay.js 渲染）独立截图：
  hero 主视觉 1 张（聊天流 + 完整状态条 + 编号标注）
  item-1~6 真实悬停弹出 tooltip 明细
  item-7 点击子代理条目弹出明细面板
  item-8 点击 ⚙ 弹出设置面板
全部交互走 Playwright 真实鼠标输入（CDP）。特写图按"内容包围盒"自动裁切
（tooltip/状态条/卡片/面板的联合区域 + 14px 边距），去掉上方空白。
device_scale_factor=2 直出 2 倍高清。
用法：先在仓库根起 HTTP 服务（python -m http.server 8799 --bind 127.0.0.1），
再 python docs/tour/shoot.py，产物在 docs/tour/shots/。
"""
from playwright.sync_api import sync_playwright
import pathlib

BASE = "http://127.0.0.1:8799/docs/tour/tour.html"
OUT = pathlib.Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

# 内容包围盒：这些元素联合起来 + pad 边距 = 截图区域（display:none 的元素自动跳过）
UNION_JS = """() => {
  const sels = ['#zusage-tip', '#zusage-bar', '.composer-card', '.panel.open'];
  let top = Infinity, left = Infinity, right = -Infinity, bottom = -Infinity, found = false;
  for (const s of sels) {
    const el = document.querySelector(s);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    found = true;
    top = Math.min(top, r.top); left = Math.min(left, r.left);
    right = Math.max(right, r.right); bottom = Math.max(bottom, r.bottom);
  }
  if (!found) return null;
  const pad = 14;
  const x = Math.max(0, left - pad), y = Math.max(0, top - pad);
  return { x, y,
    width: Math.min(innerWidth, right + pad) - x,
    height: Math.min(innerHeight, bottom + pad) - y };
}"""

# (文件名, URL 参数, 视口宽, 视口高, 就绪条件, 交互动作, 交互后等待出现的元素, 是否裁空白)
JOBS = [
    ("hero.png",   "?embed=1&hero=1", 1148, 260,
     "document.querySelectorAll('.mark').length >= 8", None, None, False),
    ("item-1.png", "?embed=1&hl=0",   640, 360,
     "document.querySelectorAll('.it.hl').length >= 1",
     ("hover", "#zu-main .it >> nth=0"), ".tip", True),
    ("item-2.png", "?embed=1&hl=1",   640, 360,
     "document.querySelectorAll('.it.hl').length >= 1",
     ("hover", "#zu-main .it >> nth=1"), ".tip", True),
    ("item-3.png", "?embed=1&hl=2",   640, 360,
     "document.querySelectorAll('.it.hl').length >= 1",
     ("hover", "#zu-main .it >> nth=2"), ".tip", True),
    ("item-4.png", "?embed=1&hl=3",   640, 360,
     "document.querySelectorAll('.it.hl').length >= 1",
     ("hover", "#zu-main .it >> nth=3"), ".tip", True),
    ("item-5.png", "?embed=1&hl=4",   640, 360,
     "document.querySelectorAll('.it.hl').length >= 1",
     ("hover", "#zu-main .it >> nth=4"), ".tip", True),
    ("item-6.png", "?embed=1&hl=5",   640, 360,
     "document.querySelectorAll('.it.hl').length >= 1",
     ("hover", "#zu-main .it >> nth=5"), ".tip", True),
    # 子代理：悬停 tooltip 只有汇总，点击弹出固定明细面板（v50 双层分工）→ 展示面板
    ("item-7.png", "?embed=1&hl=6",   640, 520,
     "document.querySelectorAll('.it.hl').length >= 1",
     ("click", ".zu-sub"), ".panel.open", True),
    # ⚙：点击打开设置面板（视口加高让面板完整入镜）
    ("item-8.png", "?embed=1&hl=gear", 640, 900,
     "document.querySelectorAll('.btn.hl').length >= 1",
     ("click", "#zu-gear"), ".panel.open", True),
]

# 两轮：中文版出 shots/，英文版（&lang=en，弹层/条面文本替换为英文）出 shots/en/
ROUNDS = [(OUT, ""), (OUT / "en", "&lang=en")]

with sync_playwright() as p:
    browser = p.chromium.launch()
    for out_dir, lang_q in ROUNDS:
      out_dir.mkdir(exist_ok=True)
      for name, q, w, h, ready, action, wait_sel, crop in JOBS:
        page = browser.new_page(
            viewport={"width": w, "height": h},
            device_scale_factor=2,
        )
        page.goto(BASE + q + lang_q, wait_until="load")
        try:
            page.wait_for_function(ready, timeout=15000)
        except Exception as exc:
            print(name, "READY TIMEOUT:", exc)
        page.wait_for_timeout(600)
        note = ""
        if action is not None:
            kind, sel = action
            # 先把鼠标放到页面上部空档再移向目标：跨过 150ms mousemove 节流窗口
            page.mouse.move(w / 2, 8)
            page.wait_for_timeout(250)
            loc = page.locator(sel)
            if kind == "hover":
                loc.hover(timeout=5000)
            else:
                loc.click(timeout=5000)
            if wait_sel:
                try:
                    page.wait_for_selector(wait_sel, state="visible", timeout=5000)
                    note = "+" + wait_sel
                except Exception as exc:
                    note = "WAIT_FAIL:" + wait_sel
                    print(name, note, exc)
            page.wait_for_timeout(700)   # 定位/内容渲染落定
        else:
            page.wait_for_timeout(300)
        if crop:
            rect = page.evaluate(UNION_JS)
            if rect:
                page.screenshot(path=str(out_dir / name), clip=rect)
                print(name, "ok", note, f"clip {rect['width']:.0f}x{rect['height']:.0f}")
                page.close()
                continue
            print(name, "UNION EMPTY -> full shot")
        page.screenshot(path=str(out_dir / name))
        print(name, "ok", note)
        page.close()
    browser.close()
print("done ->", OUT, "and", OUT / "en")
