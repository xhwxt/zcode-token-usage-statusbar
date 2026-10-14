"use strict";
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { createUpdater, PLUGIN_ID } = require("../plugin-updater.cjs");

// 仅用于开发验收：临时安装记录和假的市场响应，不访问真实插件库存。
const root = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-gui-test-"));
const runtime = path.join(root, "runtime");
const inventory = path.join(root, ".zcode", "cli", "plugins");
fs.mkdirSync(runtime); fs.mkdirSync(inventory, { recursive: true });
fs.writeFileSync(path.join(runtime, "config.json"), '{"python_path":"python3"}');
let record = { id: PLUGIN_ID, version: "1.0.0", scope: "user", installPath: path.join(root, "plugin") };
const writeRecord = () => fs.writeFileSync(path.join(inventory, "installed_plugins.json"), JSON.stringify({ plugins: [record] }));
writeRecord();
fs.writeFileSync(path.join(inventory, "known_marketplaces.json"), JSON.stringify({ marketplaces: [
  { id: "zusage-marketplace", source: { source: "directory", path: root } },
] }));
let failure = false;
const calls = [];
const updater = createUpdater({ runtime, home: root, resourcesPath: root, executable: process.execPath,
  cli: async (args) => {
    calls.push(args);
    await new Promise((r) => setTimeout(r, 700));
    if (failure) throw new Error("测试市场网络不可用");
    if (args[0] === "list") return { installed: [{ ...record, enabled: true }], available: [{ id: PLUGIN_ID, version: "1.1.0" }], diagnostics: [] };
    if (args[0] === "update") { record.version = "1.1.0"; writeRecord(); return { ok: true, diagnostics: [] }; }
    return { diagnostics: [] };
  }, sync: async () => ({ changed: ["inject-main.cjs"], skipped: ["zusage.py"], preserved: ["zusage.py"] }) });
const html = `<!doctype html><html lang="zh"><meta charset="utf-8"><title>插件更新设置验收</title>
<style>:root{--ui-font-size:14px;--font-mono:Menlo,monospace;--color-background:#101318;--color-foreground:#e9edf4;--color-card:#191d25}body{margin:0;background:#101318;color:#e9edf4;font:14px system-ui}.fixture{padding:22px}.fixture button{padding:6px;margin-right:8px}.composer-card{position:fixed;bottom:50px;left:24px;right:24px;padding:15px;background:#191d25;border:1px solid #343b48;border-radius:14px}textarea{display:block;width:95%;height:65px;background:transparent;color:#e9edf4;border:0;resize:none}.composer-card footer{height:28px}</style>
<div class="fixture"><h2>插件更新设置验收（隔离测试市场）</h2><p>1.0.0 已安装，测试市场提供 1.1.0；不会操作真实插件。</p><button id="fail">模拟网络失败</button><button id="recover">恢复测试网络</button></div>
<div class="composer-card" data-session-id="sess_fixture"><textarea placeholder="输入消息…"></textarea><footer>会话 · 发送</footer></div>
<script src="/overlay.js"></script><script>
const payload={mine:'sess_fixture',recent:[{sid:'sess_fixture',total:10000,input:9000,output:1000,turns:1,requests:1,last:{tps:80},last_turn:{total:10000,requests:1},ctx:9000}],today:{total:10000}};
setInterval(()=>{if(window.__zusageUpdate)window.__zusageUpdate(payload)},500);
let sending=false;
setInterval(async()=>{if(sending)return;sending=true;try{const q=window.__zusagePluginRequests||[];while(q.length){const req=q.shift();fetch('/action',{method:'POST',body:JSON.stringify(req)})}const s=await(await fetch('/status')).json();if(window.__zusagePluginUpdate)window.__zusagePluginUpdate(s)}finally{sending=false}},200);
document.getElementById('fail').onclick=()=>fetch('/failure',{method:'POST',body:'true'});
document.getElementById('recover').onclick=()=>fetch('/failure',{method:'POST',body:'false'});
</script></html>`;
const server = http.createServer((req, res) => {
  if (req.url === "/") { res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(html); }
  else if (req.url === "/overlay.js") { res.setHeader("Content-Type", "application/javascript"); res.end(fs.readFileSync(path.join(__dirname, "..", "overlay.js"))); }
  else if (req.url === "/status") { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(updater.status())); }
  else if (req.url === "/evidence") { res.end(JSON.stringify({ calls, config: JSON.parse(fs.readFileSync(path.join(runtime, "config.json"))), state: updater.status() })); }
  else if (req.method === "POST" && ["/action", "/failure"].includes(req.url)) {
    let body = ""; req.on("data", (d) => { body += d; }); req.on("end", async () => {
      if (req.url === "/failure") failure = body === "true";
      else await updater.request(JSON.parse(body));
      res.end("ok");
    });
  } else { res.statusCode = 404; res.end(); }
});
server.listen(8799, "127.0.0.1", () => console.log("测试页面：http://127.0.0.1:8799/"));
function close() { server.close(() => { fs.rmSync(root, { recursive: true, force: true }); process.exit(); }); }
process.on("SIGTERM", close); process.on("SIGINT", close);
