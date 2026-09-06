# -*- coding: utf-8 -*-
"""README 功能导览图 · 逐张截图脚本
对 docs/tour/tour.html 的每个迷你窗口（真实 overlay.js 渲染）独立截图：
  hero 主视觉 1 张（完整状态条 + 编号标注）+ 单项特写 8 张（聚焦高亮）。
device_scale_factor=2 直出 2 倍高清，无需 CSS zoom。
用法：先在仓库根起 HTTP 服务（python -m http.server 8799 --bind 127.0.0.1），
再 python docs/tour/shoot.py，产物在 docs/tour/shots/。
"""
from playwright.sync_api import sync_playwright
import pathlib

BASE = "http://127.0.0.1:8799/docs/tour/tour.html"
OUT = pathlib.Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

# (文件名, URL 参数, 视口宽, 视口高, 就绪条件：对应元素出现才截)
JOBS = [
    ("hero.png",   "?embed=1&hero=1", 1148, 450,
     "document.querySelectorAll('.mark').length >= 8"),
    ("item-1.png", "?embed=1&hl=0",    640, 170,
     "document.querySelectorAll('.it.hl').length >= 1"),
    ("item-2.png", "?embed=1&hl=1",    640, 170,
     "document.querySelectorAll('.it.hl').length >= 1"),
    ("item-3.png", "?embed=1&hl=2",    640, 170,
     "document.querySelectorAll('.it.hl').length >= 1"),
    ("item-4.png", "?embed=1&hl=3",    640, 170,
     "document.querySelectorAll('.it.hl').length >= 1"),
    ("item-5.png", "?embed=1&hl=4",    640, 170,
     "document.querySelectorAll('.it.hl').length >= 1"),
    ("item-6.png", "?embed=1&hl=5",    640, 170,
     "document.querySelectorAll('.it.hl').length >= 1"),
    ("item-7.png", "?embed=1&hl=6",    640, 170,
     "document.querySelectorAll('.it.hl').length >= 1"),
    ("item-8.png", "?embed=1&hl=gear", 640, 170,
     "document.querySelectorAll('.btn.hl').length >= 1"),
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, q, w, h, ready in JOBS:
        page = browser.new_page(
            viewport={"width": w, "height": h},
            device_scale_factor=2,
        )
        page.goto(BASE + q, wait_until="load")
        try:
            page.wait_for_function(ready, timeout=15000)
        except Exception as exc:
            print(name, "READY TIMEOUT:", exc)
        page.wait_for_timeout(700)   # 高亮/标注落定后的余量
        page.screenshot(path=str(OUT / name))
        print(name, "ok")
        page.close()
    browser.close()
print("done ->", OUT)
