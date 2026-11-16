"""The app builder stages a native host instead of exec-replacing it with Python."""

import importlib.util
import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def builder(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "scripts" / "build_studio_app.py"
    spec = importlib.util.spec_from_file_location("build_studio_app_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "fork"
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").touch()
    (root / "packaging").mkdir()
    (root / "packaging/camoufox-pm.icns").write_bytes(b"icon")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/swiftc")
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == "/usr/bin/swiftc":
            executable = Path(arguments[arguments.index("-o") + 1])
            executable.write_bytes(b"\xcf\xfa\xed\xfe")
            executable.chmod(0o755)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    return module, root, calls


@pytest.mark.parametrize("preview", [False, True])
def test_native_host_has_nonsecret_config_and_own_identity(builder, tmp_path, monkeypatch, preview):
    module, root, calls = builder
    destination = tmp_path / "Camoufox Studio.app"
    arguments = ["build", "--destination", str(destination), "--script-import-path", str(tmp_path / "tools")]
    if preview:
        arguments += ["--data-dir", str(tmp_path / "preview")]
    monkeypatch.setattr(sys, "argv", arguments)
    module.main()
    executable = destination / "Contents/MacOS/CamoufoxStudio"
    assert executable.read_bytes() == b"\xcf\xfa\xed\xfe"
    config = json.loads((destination / "Contents/Resources/studio-runtime.json").read_text())
    assert config["root"] == str(root)
    assert config["scriptImportPaths"] == [str(tmp_path / "tools")]
    assert config["dataDirectory"] == (str(tmp_path / "preview") if preview else None)
    assert set(config) == {
        "version", "root", "python", "database", "routineCatalog", "scriptImportPaths", "dataDirectory",
    }
    info = plistlib.loads((destination / "Contents/Info.plist").read_bytes())
    assert info["CFBundleIdentifier"].startswith("local.camoufox.studio")
    assert (".preview." in info["CFBundleIdentifier"]) is preview
    assert "System Events" in info["NSAppleEventsUsageDescription"]
    assert calls[0][0] == "/usr/bin/swiftc" and "-no_uuid" not in calls[0]
    assert calls[-1][:4] == ["/usr/bin/codesign", "--force", "--sign", "-"]


def test_failed_native_compile_preserves_installed_app(builder, tmp_path, monkeypatch):
    module, _, _ = builder
    destination = tmp_path / "Camoufox Studio.app"
    (destination / "Contents/MacOS").mkdir(parents=True)
    info = {"CFBundleIdentifier": "local.camoufox.studio"}
    (destination / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    original = destination / "Contents/MacOS/CamoufoxStudio"
    original.write_text("original launcher")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        subprocess.CalledProcessError(1, "swiftc"),
    ))
    monkeypatch.setattr(sys, "argv", ["build", "--destination", str(destination)])
    with pytest.raises(subprocess.CalledProcessError):
        module.main()
    assert original.read_text() == "original launcher"
