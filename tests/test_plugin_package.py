# -*- coding: utf-8 -*-
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "zcode-token-usage-statusbar"


class PluginPackageTests(unittest.TestCase):
    def test_manifest_identity_version_and_resources_agree(self):
        manifest = json.loads((PLUGIN / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))
        compatibility = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / "marketplace.json").read_text(encoding="utf-8"))
        entry = next(p for p in marketplace["plugins"] if p["name"] == manifest["name"])
        self.assertEqual(manifest, compatibility)
        self.assertEqual(manifest["name"], PLUGIN.name)
        self.assertEqual(manifest["version"], entry["version"])
        self.assertEqual((ROOT / entry["source"]).resolve(), PLUGIN)
        for component in ("commands", "skills"):
            self.assertTrue((PLUGIN / manifest[component]).is_dir())
        self.assertTrue((PLUGIN / ".mcp.json").is_file())
        self.assertTrue((PLUGIN / "hooks" / "hooks.json").is_file())
        self.assertTrue((PLUGIN / "restart_notice.py").is_file())

    def test_default_update_setting_and_bilingual_panel_copy_agree(self):
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertIs(config["plugin_auto_update"], True)
        self.assertIs(config["plugin_auto_install_overlay"], False)
        overlay = (ROOT / "overlay.js").read_text(encoding="utf-8")
        self.assertIn("每 24 小时检测并安装新版（默认开启，可关闭）", overlay)
        self.assertIn("Check and install every 24 hours (on by default, can be disabled)", overlay)
        self.assertNotIn("Check and install every 24 hours (off by default)", overlay)
        self.assertIn("cb.disabled = !active;", overlay)
        self.assertIn("Update source: stable GitHub Release (validated ZIP and SHA256)", overlay)
        self.assertIn("Update source: local directory marketplace", overlay)
        self.assertIn("Release version:", overlay)
        self.assertIn("Turning automatic updates off stops future installs; an install already started will finish", overlay)

    def test_updater_is_packaged_and_runtime_sync_never_includes_config(self):
        spec = importlib.util.spec_from_file_location("plugin_installer_package_test", PLUGIN / "overlay_install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        self.assertIn("plugin-updater.cjs", installer.RUNTIME_FILES)
        self.assertIn("runtime_lock.py", installer.RUNTIME_FILES)
        self.assertNotIn("config.json", installer.RUNTIME_FILES)
        self.assertNotIn("plugin-update.json", installer.RUNTIME_FILES)
        for name in ("inject-main.cjs", "plugin-updater.cjs", "plugin-release.cjs", "plugin-storage.cjs", "release_unpack.py", "runtime_lock.py", "overlay.js"):
            self.assertIn(name, installer.RUNTIME_FILES)
            self.assertEqual((ROOT / name).read_bytes(), (PLUGIN / name).read_bytes())

    def test_release_skill_is_project_only_and_uses_root_tools(self):
        skill = ROOT / ".zcode" / "skills" / "zcode-github-release"
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: zcode-github-release", text)
        self.assertIn("--repo OWNER/REPO --out", text)
        self.assertIn("plugin-releases", text)
        self.assertIn("--publish", text)
        self.assertIn("仅供所在仓库使用", text)
        self.assertFalse((skill / "scripts").exists())
        self.assertFalse((PLUGIN / "skills" / "github-release").exists())
        self.assertTrue((skill / "references" / "release-contract.md").is_file())
        for name in ("release_tool.py", "release_unpack.py"):
            self.assertTrue((ROOT / name).is_file())


if __name__ == "__main__":
    unittest.main()
