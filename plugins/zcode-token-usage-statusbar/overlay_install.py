# -*- coding: utf-8 -*-
"""悬浮状态条的插件侧安装器（CLI + SessionStart hook 共用）。

CLI 用法（/zcode-token-usage-statusbar:install-overlay 与 :uninstall-overlay 背后就是它）：
  python3 overlay_install.py sync      # 仅同步运行时文件，stdout 输出 JSON（不读取 asar）
  python3 overlay_install.py install   # 同步运行时文件 + 写入 asar 注入行（幂等）
  python3 overlay_install.py remove    # 剥离注入行，并写豁免标记（hook 不再提示/自动安装）
  python3 overlay_install.py check     # 查看注入状态、运行时目录与开关

hook 用法：hooks/session-start.py 调 run_hook()，stdout 只输出 hook 协议 JSON。

与仓库根 install.py 的关系：根目录文件是正本，本插件目录里的运行时文件是
sync_plugin.py 生成的副本；本脚本把副本同步进稳定运行时目录
~/.zcode/zcode-token-usage-statusbar/（mtime 保护较新手改，升级时以记录 hash 识别托管文件），
再对运行时目录执行 patch_install.set_runtime() + install()——注入行指向稳定目录而非
插件缓存目录，插件升级换缓存目录不会让浮层失联。

测试接缝（环境变量）：ZUSAGE_PLUGIN_RUNTIME / ZUSAGE_PLUGIN_ASAR 可把运行时目录和
目标 asar 指到别处（自测用临时副本，不碰真机）。

自动安装开关（默认关，开(enable)后 SessionStart hook 自动执行 install）：
  运行时目录 config.json 写 "plugin_auto_install_overlay": true，
  或环境变量 ZUSAGE_PLUGIN_AUTO_INSTALL=1（临时/测试用，优先级更高）。

提示豁免：运行时目录写 .plugin-overlay-optout 后 hook 永久静默（remove 自动写入）；
.plugin-overlay-nudge 是"只提示一次"的标记，安装成功后清除。
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.resolve()
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
from runtime_lock import file_lock, update_config
# 会同步进运行时目录的插件副本（usage_mcp.py 不在其中：MCP 直接从插件目录跑）
RUNTIME_FILES = [
    "inject-main.cjs",
    "overlay.js",
    "zusage.py",
    "patch_install.py",
    "install_monitor.py",
    "plugin-updater.cjs",
    "plugin-release.cjs",
    "plugin-storage.cjs",
    "release_unpack.py",
    "runtime_lock.py",
]


def default_runtime():
    return Path.home() / ".zcode" / "zcode-token-usage-statusbar"


def load_patch_install():
    if str(PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT))
    import patch_install
    return patch_install


def load_runtime_config(runtime):
    """缺失配置可创建；损坏或非对象配置不能当空配置覆盖。"""
    try:
        config = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return config if isinstance(config, dict) else None


def asar_candidates():
    """仅枚举当前平台常见位置；候选仍须通过包身份校验。"""
    if sys.platform == "darwin":
        return [Path("/Applications/ZCode.app/Contents/Resources/app.asar"),
                Path.home() / "Applications/ZCode.app/Contents/Resources/app.asar"]
    if sys.platform.startswith("linux"):
        return [Path(root) / "resources/app.asar" for root in
                ("/opt/ZCode", "/opt/zcode", "/usr/lib/zcode", "/usr/local/ZCode")]
    candidates = [Path(r"D:\ZCode\resources\app.asar"),
                  Path(r"C:\ZCode\resources\app.asar")]
    for variable, suffix in (("LOCALAPPDATA", "Programs/ZCode"),
                             ("LOCALAPPDATA", "ZCode"), ("ProgramFiles", "ZCode")):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / suffix / "resources/app.asar")
    if os.environ.get("LOCALAPPDATA"):
        programs = Path(os.environ["LOCALAPPDATA"]) / "Programs"
        candidates.extend(sorted(programs.glob("*/resources/app.asar")))
    return candidates


def is_zcode_asar(pi, asar):
    """生产目标必须由 asar 内 package.json 的 name 确认为 ZCode。"""
    try:
        with asar.open("rb") as stream:
            header, base = pi.read_header(stream)
            node = header["files"]["package.json"]
            if node.get("unpacked"):
                return False
            stream.seek(base + int(node["offset"]))
            package = json.loads(stream.read(int(node["size"])).decode("utf-8"))
        name = package.get("name")
        return isinstance(name, str) and "zcode" in name.lower()
    except Exception:
        return False


def resolve_paths():
    """返回 (runtime_dir, patch_install 模块)；无有效目标时 ASAR 为 None。"""
    pi = load_patch_install()
    runtime = Path(os.environ.get("ZUSAGE_PLUGIN_RUNTIME") or default_runtime()).expanduser().resolve()
    pi.set_runtime(runtime)
    test_asar = os.environ.get("ZUSAGE_PLUGIN_ASAR")
    if test_asar:
        pi.set_target(Path(test_asar).expanduser().resolve())
        return runtime, pi
    config = load_runtime_config(runtime)
    candidates = [os.environ.get("ZCODE_ASAR"), (config or {}).get("asar_path")]
    candidates.extend(asar_candidates())
    for candidate in candidates:
        if not isinstance(candidate, (str, Path)) or not candidate:
            continue
        asar = Path(os.path.expandvars(str(candidate))).expanduser().resolve()
        if is_zcode_asar(pi, asar):
            pi.set_target(asar)
            return runtime, pi
    # patch_install 是缓存模块；上次会话的目标绝不能作为本次探测兜底。
    pi.ASAR = pi.BAK = pi.TMP = pi.ZCODE_EXE = None
    return runtime, pi


def optout_marker(runtime):
    return runtime / ".plugin-overlay-optout"


def nudge_marker(runtime):
    return runtime / ".plugin-overlay-nudge"


def clear_markers(runtime):
    for m in (optout_marker(runtime), nudge_marker(runtime)):
        try:
            m.unlink()
        except FileNotFoundError:
            pass


def plugin_version():
    """优先读 ZCode manifest，兼容只有 Claude manifest 的插件副本。"""
    for folder in (".zcode-plugin", ".claude-plugin"):
        manifest = PLUGIN_ROOT / folder / "plugin.json"
        if manifest.is_file():
            return json.loads(manifest.read_text(encoding="utf-8"))["version"]
    raise FileNotFoundError("插件 manifest 不存在")


def _version_key(version):
    """SemVer 排序；忽略 build metadata，预发布版本低于正式版。"""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?"
                         r"(?:\+[0-9A-Za-z.-]+)?", version)
    if not match:
        raise ValueError("无效插件版本：{}".format(version))
    prerelease = match.group(4)
    parts = tuple((0, int(part)) if part.isdigit() else (1, part)
                  for part in prerelease.split(".")) if prerelease else ()
    return tuple(int(match.group(i)) for i in (1, 2, 3)), prerelease is None, parts


def _atomic_write(dst, data, src=None):
    """同目录原子替换；已有运行时内容改变必须产生不同 mtime 供 loader 检测。"""
    previous_stat = dst.stat() if src is not None and dst.exists() else None
    fd, temporary = tempfile.mkstemp(prefix="." + dst.name + ".", dir=str(dst.parent))
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        if src is not None:
            if previous_stat is None:
                shutil.copystat(src, temporary)
            else:
                shutil.copymode(src, temporary)
                stamp = max(time.time_ns(), previous_stat.st_mtime_ns + 1000000)
                os.utime(temporary, ns=(temporary.stat().st_atime_ns, stamp))
        os.replace(temporary, dst)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def _sync_verifier(runtime):
    pending_path = runtime / "plugin-update.json"
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        if not isinstance(pending, dict):
            raise ValueError("plugin-update.json 必须是对象")
    except FileNotFoundError:
        pending = {}
    active = (pending.get("pendingSync") is True or pending.get("pendingRelease") is not None
              or pending.get("pendingPid"))
    base = runtime if (runtime / "plugin-updater.cjs").is_file() else PLUGIN_ROOT
    if active and base != runtime:
        raise ValueError("待恢复安装缺少可信运行时验收器，停止同步")
    executable = shutil.which("node")
    config = load_runtime_config(runtime) or {}
    asar = os.environ.get("ZCODE_ASAR") or config.get("asar_path")
    if asar:
        target = Path(asar)
        candidates = ([target.parent.parent / "MacOS" / "ZCode"] if sys.platform == "darwin"
                      else [target.parent.parent / ("ZCode.exe" if sys.platform == "win32" else "zcode")])
        executable = next((str(item) for item in candidates if item.is_file()), executable)
    if not executable:
        raise ValueError("未找到运行时验收所需 Node/ZCode 可执行文件")
    names = ("plugin-updater.cjs", "plugin-release.cjs", "plugin-storage.cjs")
    with tempfile.TemporaryDirectory(prefix="zusage-sync-verifier-") as directory:
        snapshot = Path(directory)
        original = {}
        for name in names:
            original[name] = (base / name).read_bytes()
            (snapshot / name).write_bytes(original[name])
        if any((base / name).read_bytes() != data for name, data in original.items()):
            raise ValueError("可信验收器在读取期间变化，停止同步")
        # The migration contract belongs to this installer, never to the new
        # cache helper. Require only the frozen runtime modules; newer runtimes
        # retain their own verifier, while HEAD runtimes use this fixed adapter.
        verifier_script = r'''
const fs = require("fs"), path = require("path"), os = require("os");
const { isDeepStrictEqual } = require("util");
const [snapshot, runtime, pluginRoot] = process.argv.slice(1);
const updater = require(path.join(snapshot, "plugin-updater.cjs"));
function migrate() {
  const release = require(path.join(snapshot, "plugin-release.cjs"));
  const { resolvePluginStorage } = require(path.join(snapshot, "plugin-storage.cjs"));
  const readObject = (file, missing) => {
    let value;
    try { value = JSON.parse(fs.readFileSync(file, "utf8")); }
    catch (error) { if (error.code === "ENOENT") return missing; throw error; }
    if (!value || typeof value !== "object" || Array.isArray(value)) throw Error("同步验收记录不是 JSON 对象: " + file);
    return value;
  };
  const root = fs.realpathSync(pluginRoot), id = "zcode-token-usage-statusbar@zusage-marketplace";
  const manifest = readObject(path.join(root, ".zcode-plugin", "plugin.json"), null);
  if (!manifest || manifest.name !== id.split("@")[0]) throw Error("同步插件清单身份无效");
  updater.compareVersions(manifest.version, manifest.version);
  const pending = readObject(path.join(runtime, "plugin-update.json"), null);
  const hasPending = pending && (pending.pendingSync === true || pending.pendingRelease != null || pending.pendingPid);
  const live = pid => {
    try { process.kill(pid, 0); return true; }
    catch (error) { return error.code !== "ESRCH"; }
  };
  const legacy = path.join(runtime, "plugin-update.lock");
  try {
    let owner;
    try { owner = JSON.parse(fs.readFileSync(legacy, "utf8")); } catch (error) { owner = {}; }
    if ((owner && Number.isSafeInteger(owner.pid) && owner.pid > 0)
        ? live(owner.pid) : Date.now() - fs.statSync(legacy).mtimeMs < 600000) throw Error("旧更新进程仍在运行，停止运行时同步");
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  if (pending && pending.pendingPid) {
    if (!Number.isSafeInteger(pending.pendingPid) || pending.pendingPid <= 0) throw Error("安装进程记录无效");
    if (live(pending.pendingPid)) throw Error("插件安装进程仍在运行，停止运行时同步");
  }
  const storage = resolvePluginStorage(os.homedir(), process.env);
  if (fs.existsSync(path.join(storage, "marketplaces", ".zusage-marketplace.transaction.json"))) throw Error("官方市场事务尚未完成，停止运行时同步");
  const inventory = readObject(path.join(storage, "installed_plugins.json"), null);
  if (inventory && !Array.isArray(inventory.plugins)) throw Error("官方安装记录形状无效");
  const records = inventory ? inventory.plugins.filter(row => row && row.id === id && row.scope === "user") : [];
  if (!records.length) {
    if (hasPending || inventory) throw Error("同步所需用户插件安装记录不存在");
    return { ok: true, pluginRoot: root, identity: { record: null, channel: null, pending, manifest } };
  }
  if (records.length !== 1) throw Error("同步所需用户插件安装记录不唯一");
  const record = records[0];
  if (record.enabled === false || typeof record.installPath !== "string" || fs.realpathSync(record.installPath) !== root || record.version !== manifest.version) throw Error("同步插件与官方安装记录不一致或已禁用");
  const selected = release.readChannel(os.homedir(), storage);
  const channel = { kind: selected.kind, repository: selected.repository || "", source: selected.marketplace ? selected.marketplace.source : null };
  if (hasPending) {
    if (pending.pendingSync !== true) throw Error("待同步意图不完整，停止运行时同步");
    if (pending.pendingVersion && updater.compareVersions(record.version, pending.pendingVersion) <= 0) throw Error("插件安装尚未提交新版，停止运行时同步");
    if (pending.pendingRelease != null) {
      const expected = pending.pendingRelease, r = expected.release, m = expected.manifest;
      if (!r || !m || !expected.channel || !isDeepStrictEqual(channel, expected.channel)) throw Error("Release 更新意图无效或市场来源已变化，停止运行时同步");
      const validated = release.validateRelease({ tag_name: r.tag, draft: false, prerelease: false, assets: [r.archive, r.manifest] }, r.repository);
      if (!validated || validated.version !== r.version || channel.kind !== "release" || channel.repository !== r.repository) throw Error("Release 更新意图无效，停止运行时同步");
      release.validateManifest(m, validated);
      const source = record.source;
      if (record.version !== r.version || !source || source.url !== r.archive.browser_download_url || source.sha256 !== m.asset.sha256 || source.source !== "url" || source.type !== "zip" || source.path !== "plugins/zcode-token-usage-statusbar" || (source.stripRoot !== undefined && source.stripRoot !== false) || source.headers !== undefined) throw Error("官方安装结果与选定 Release 不一致，停止运行时同步");
    } else if (channel.kind === "release") throw Error("历史更新意图缺少 Release 校验信息，停止运行时同步");
  }
  return { ok: true, pluginRoot: root, identity: { record, channel, pending, manifest } };
}
try {
  const result = typeof updater.verifyRuntimeSync === "function"
    ? updater.verifyRuntimeSync({ runtime, pluginRoot }) : migrate();
  process.stdout.write(JSON.stringify(result) + "\n");
} catch (error) { console.error("[zusage-plugin] 同步验收失败:", error.message); process.exitCode = 1; }
'''
        def verify():
            result = subprocess.run([executable, "-e", verifier_script, str(snapshot),
                                     str(runtime), str(PLUGIN_ROOT)],
                                    capture_output=True, text=True, timeout=10,
                                    env=dict(os.environ, ELECTRON_RUN_AS_NODE="1"))
            if result.returncode:
                raise ValueError("运行时同步验收失败: " + result.stderr.strip()[:1000])
            data = json.loads(result.stdout)
            if data.get("ok") is not True or data.get("pluginRoot") != str(PLUGIN_ROOT.resolve()):
                raise ValueError("运行时同步验收结果无效")
            return data["identity"]
        identity = verify()
        def recheck():
            if verify() != identity:
                raise ValueError("运行时同步期间安装身份发生变化")
        yield recheck


def sync_runtime(runtime):
    """All entry points verify installed identity before the shared transaction."""
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    with file_lock(runtime / "plugin-runtime.lock"):
        with _sync_verifier(runtime) as verify:
            return _sync_runtime_locked(runtime, verify)


def _sync_runtime_locked(runtime, verify=lambda: None):
    """可信 hash 持续保护手改；无基线迁移用 mtime，旧插件不能回滚新版。"""
    version = plugin_version()
    current_version = _version_key(version)
    plugin_root = str(PLUGIN_ROOT.resolve())
    record_path = runtime / "plugin-runtime.json"
    try:
        previous = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous = {}
    if not isinstance(previous, dict):
        raise ValueError("plugin-runtime.json 必须是对象")
    previous_version = _version_key(previous["version"]) if previous else None
    journal_path = runtime / "plugin-runtime-pending.json"
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        journal = None
    if journal is not None:
        if not isinstance(journal, dict) or not isinstance(journal.get("targets"), dict) or not isinstance(journal.get("base"), dict):
            raise ValueError("运行时待同步记录损坏")
        if previous == journal["base"]:
            recovered = dict(previous)
            recovered_hashes = dict(previous.get("hashes", {}))
            for name, digest in journal["targets"].items():
                if name not in RUNTIME_FILES or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                    raise ValueError("运行时待同步 hash 无效")
                target = runtime / name
                if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                    recovered_hashes[name] = digest
            recovered["hashes"] = recovered_hashes
            if not previous and recovered_hashes:
                recovered.update(version="0.0.0", plugin_root=str(PLUGIN_ROOT.resolve()))
            if recovered != previous:
                verify()
                _atomic_write(record_path, (json.dumps(recovered) + "\n").encode("utf-8"))
                previous = recovered
        journal_path.unlink()
    downgrade = previous_version is not None and current_version < previous_version
    recorded_hashes = previous.get("hashes", {})
    if not isinstance(recorded_hashes, dict):
        raise ValueError("plugin-runtime.json hashes 必须是对象")
    hashes = {}
    for name, digest in recorded_hashes.items():
        if name not in RUNTIME_FILES:
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("plugin-runtime.json 的 {} hash 损坏".format(name))
        hashes[name] = digest.lower()
    changed, skipped, preserved = [], [], []
    writes = []
    planned_targets = {}
    for name in RUNTIME_FILES:
        src, dst = PLUGIN_ROOT / name, runtime / name
        if not src.is_file() or downgrade:
            skipped.append(name)
            continue
        data = src.read_bytes()
        src_hash = hashlib.sha256(data).hexdigest()
        planned_targets[name] = hashlib.sha256(dst.read_bytes()).hexdigest() if dst.is_file() else None
        if dst.is_file():
            dst_hash = hashlib.sha256(dst.read_bytes()).hexdigest()
            if dst_hash == src_hash:
                hashes[name] = src_hash
                skipped.append(name)
                continue
            if name in hashes:
                protect = dst_hash != hashes[name]
                reason = "运行时 hash 与记录不同（可能含手改热修）"
            else:
                protect = src.stat().st_mtime_ns <= dst.stat().st_mtime_ns
                reason = "运行时副本更新（可能含手改热修）"
            if protect:
                print(f"[zusage-plugin] 跳过 {name}：{reason}，"
                      f"如需覆盖请手动替换 {dst}", file=sys.stderr)
                skipped.append(name)
                preserved.append(name)
                continue
        hashes[name] = src_hash
        changed.append(name)
        writes.append((dst, data, src))
    result = {"changed": changed, "skipped": skipped, "preserved": preserved,
              "version": version, "plugin_root": plugin_root, "hashes": hashes,
              "restartRecorded": True}
    cold_files = {"inject-main.cjs", "plugin-updater.cjs", "plugin-release.cjs", "plugin-storage.cjs"}
    if (load_runtime_config(runtime) or {}).get("hot_reload") is False:
        cold_files.update(("overlay.js", "zusage.py"))
    restart_hashes = {name: hashes[name] for name in changed if name in cold_files}
    verify()
    if writes:
        journal = {"base": previous, "targets": {dst.name: hashes[dst.name] for dst, _, _ in writes}}
        _atomic_write(journal_path, (json.dumps(journal) + "\n").encode("utf-8"))
    if restart_hashes:
        from restart_notice import record_restart
        record_restart(runtime, {"hashes": restart_hashes, "changed": changed,
                                 "version": version, "state": "pending-sync"})
    for dst, data, src in writes:
        verify()
        current_hash = hashlib.sha256(dst.read_bytes()).hexdigest() if dst.is_file() else None
        if current_hash != planned_targets[dst.name]:
            raise ValueError("运行时文件在同步规划后发生变化，保留手改并停止同步: " + dst.name)
        _atomic_write(dst, data, src)
    verify()
    record = dict(result)
    if downgrade:
        record["version"] = previous["version"]
        record["plugin_root"] = previous["plugin_root"]
    _atomic_write(record_path, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    if journal_path.exists():
        journal_path.unlink()
    return result


def injected_status(pi):
    """True=当前运行时的唯一注入行 False=需修复 None=asar 不可读。"""
    try:
        entry = pi.entry_bytes_of(pi.ASAR)
        line = pi.INJECT_LINE
        if isinstance(line, str):
            line = line.encode("utf-8")
        return bool(line and entry.count(line) == 1 and
                    all(match.group() == line for match in pi.ZUSAGE_LINE_RE.finditer(entry)))
    except Exception:
        return None


def save_runtime_config(runtime, pi):
    """调用方已确认目标有效/安装成功；仅补 Python 路径与实际 asar 位置。"""
    if pi.ASAR is None:
        return False
    try:
        update_config(runtime, {"asar_path": str(pi.ASAR)}, {"python_path": sys.executable})
    except (OSError, ValueError, TimeoutError):
        print("[zusage-plugin] config.json 损坏、不可读或锁忙，保留原文件。", file=sys.stderr)
        return False
    return True


def auto_enabled(runtime):
    v = os.environ.get("ZUSAGE_PLUGIN_AUTO_INSTALL")
    if v is not None:
        return v.strip().lower() in ("1", "true", "yes", "on")
    return (load_runtime_config(runtime) or {}).get("plugin_auto_install_overlay") is True


def write_marker(marker):
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
        return True
    except OSError:
        return False


def _nudge(runtime, message):
    write_marker(nudge_marker(runtime))
    return message


def run_hook():
    """SessionStart hook 主体：返回 hook 协议 JSON dict。任何异常都转成合法输出，
    绝不影响会话；patch_install 的人类可读输出重定向到 stderr，保证 stdout 纯 JSON。"""
    out = {"suppressOutput": True}
    try:
        runtime, pi = resolve_paths()
        changed = sync_runtime(runtime)["changed"]
        try:
            from plugin_auto_update import start_auto_update
            start_auto_update(runtime, pi)
        except Exception as e:
            print(f"[zusage-plugin] 后台更新启动失败（已忽略）：{e}", file=sys.stderr)
        st = injected_status(pi)
        if st is None:
            return out  # 本机没有可定位的桌面客户端（远程会话等），静默
        save_runtime_config(runtime, pi)
        if st:
            from restart_notice import MESSAGE, reminder_context
            context = reminder_context(runtime)
            if context:
                cold_files = {"inject-main.cjs", "plugin-updater.cjs", "plugin-release.cjs", "plugin-storage.cjs"}
                if (load_runtime_config(runtime) or {}).get("hot_reload") is False:
                    cold_files.update(("overlay.js", "zusage.py"))
                restart_files = [name for name in changed if name in cold_files]
                out["systemMessage"] = (("zusage 悬浮条：" + " / ".join(restart_files) +
                                         " 已更新，重启 ZCode 后生效。") if restart_files else MESSAGE)
                out["hookSpecificOutput"] = {"hookEventName": "SessionStart",
                                             "additionalContext": context}
            return out
        if optout_marker(runtime).exists():
            return out
        if auto_enabled(runtime):
            monitor_env = {name: os.environ.get(name) for name in
                           ("ZUSAGE_NO_MONITOR", "ZUSAGE_MONITOR_QUIET")}
            try:
                os.environ["ZUSAGE_NO_MONITOR"] = "1"
                os.environ["ZUSAGE_MONITOR_QUIET"] = "1"  # 无窗口后台收尾
                with contextlib.redirect_stdout(sys.stderr):
                    ok = pi.install()
            finally:
                for name, value in monitor_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
            if ok:
                clear_markers(runtime)
                out["systemMessage"] = "悬浮状态条已自动安装，重启 ZCode 后生效。"
                from restart_notice import session_notice
                session_notice(runtime, out)
            else:
                out["systemMessage"] = _nudge(runtime, (
                    "悬浮状态条自动安装未完成（详见客户端 hook 日志）。"
                    "可稍后运行 /zcode-token-usage-statusbar:install-overlay 重试；本提示只出现一次。"))
            return out
        if not nudge_marker(runtime).exists():
            out["systemMessage"] = _nudge(runtime, (
                "zcode-token-usage-statusbar 可为本机 ZCode 客户端安装悬浮用量状态条"
                "（修改 app.asar，重启客户端生效）。需要请运行 /zcode-token-usage-statusbar:install-overlay；"
                "不需要请运行 /zcode-token-usage-statusbar:uninstall-overlay，之后不再提示。"))
        return out
    except Exception as e:  # noqa: BLE001 — hook 绝不能抛
        out["systemMessage"] = f"zusage 插件自检失败（已忽略）：{e}"
        return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "sync":
        runtime = Path(os.environ.get("ZUSAGE_PLUGIN_RUNTIME") or default_runtime())
        try:
            result = sync_runtime(runtime)
        except Exception as e:
            print(f"[zusage-plugin] 同步失败：{e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, ensure_ascii=False))
        return
    runtime, pi = resolve_paths()
    if cmd == "install":
        sync_runtime(runtime)
    if pi.ASAR is None:
        print("[zusage-plugin] 未找到有效 ZCode app.asar；请设置 ZCODE_ASAR。", file=sys.stderr)
        if cmd in ("install", "remove"):
            sys.exit(1)
        return
    if cmd == "install":
        clear_markers(runtime)  # 主动安装 = 重新参与提示逻辑
        previously_injected = injected_status(pi)
        ok = pi.install()
        if ok and previously_injected is False:
            from restart_notice import record_restart
            record_restart(runtime)
        if ok:
            from restart_notice import MESSAGE, reminder_context
            if reminder_context(runtime):
                print(MESSAGE)
        if ok or injected_status(pi) is not None:
            save_runtime_config(runtime, pi)
    elif cmd == "remove":
        ok = pi.remove()
        if ok and not write_marker(optout_marker(runtime)):
            print("（豁免标记写入失败，hook 可能继续提示）")
    else:
        st = injected_status(pi)
        print(f"runtime: {runtime}")
        print(f"asar:    {pi.ASAR}")
        print("injected:", {True: "是", False: "否", None: "asar 不可读"}[st])
        print(f"auto install: {auto_enabled(runtime)}"
              f"（config.json 的 plugin_auto_install_overlay 或环境变量 ZUSAGE_PLUGIN_AUTO_INSTALL）")
        print(f"optout: {optout_marker(runtime).exists()}  nudge: {nudge_marker(runtime).exists()}")
        with contextlib.redirect_stdout(sys.stdout):
            pi.check()
        return
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
