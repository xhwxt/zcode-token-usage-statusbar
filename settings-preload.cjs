/* ZCode Token 用量 —— 设置窗口 preload。
 *
 * 只做一件事：把主进程提供的配置读写能力挂到 window.__zusageApi，
 * 让 settings.html 无需 nodeIntegration 也能读写 config.json。
 * 走 ipcRenderer.invoke/sendSync，通道由 inject-main.cjs 注册。
 */
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

const api = {
  /* 同步读取：页面首屏就要值，异步会让表单先闪一下空值 */
  get() {
    try {
      return ipcRenderer.sendSync("zusage:cfg:get-sync");
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  },
  /* 保存：主进程做白名单校验 + 原子替换，返回 {ok, changed, cfg} */
  set(patch) {
    try {
      return ipcRenderer.sendSync("zusage:cfg:set-sync", patch || {});
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  },
};

try {
  contextBridge.exposeInMainWorld("__zusageApi", api);
} catch (e) {
  /* contextIsolation 关闭时 contextBridge 不可用，直接挂到 window */
  window.__zusageApi = api;
}
