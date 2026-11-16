#!/usr/bin/env python3
"""Install a distinct local .app without changing the original app or its Python env."""

import argparse
import hashlib
import json
import platform
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path.home() / "Applications" / "Camoufox Studio.app")
    parser.add_argument("--routine-catalog", type=Path, default=ROOT.parent / "hybrid-control" / "routines" / "catalog.json")
    parser.add_argument("--data-dir", type=Path, help="Use an explicit data folder for a separate test app")
    parser.add_argument("--script-import-path", type=Path, action="append", help="Local module root for database scripts (repeatable)")
    args = parser.parse_args()
    python = ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        parser.error("Create the fork environment first: uv sync --frozen --extra desktop --extra dev")
    destination = args.destination.expanduser().resolve()
    identity = "local.camoufox.studio"
    display_name = "Camoufox Studio"
    if args.data_dir:
        suffix = hashlib.sha256(str(args.data_dir.expanduser().resolve()).encode()).hexdigest()[:12]
        identity += f".preview.{suffix}"
        display_name += " Preview"
    if destination.exists():
        info_path = destination / "Contents" / "Info.plist"
        if not info_path.is_file() or plistlib.loads(info_path.read_bytes()).get("CFBundleIdentifier") not in (identity, "local.camoufox.studio"):
            parser.error("Destination belongs to another app; choose a new path")
    import_paths = args.script_import_path
    if import_paths is None:
        import_paths = [path for path in (ROOT.parent / "hybrid-control", ROOT.parent / "proxy-tools") if path.is_dir()]
    import_paths = [str(path.expanduser().resolve()) for path in import_paths]
    configuration = {
        "version": 1,
        "root": str(ROOT),
        "python": str(python),
        "database": str(Path.home() / "Library/Application Support/Camoufox Profile Manager/profiles.db"),
        "routineCatalog": str(args.routine_catalog.expanduser().resolve()),
        "scriptImportPaths": import_paths,
        "dataDirectory": str(args.data_dir.expanduser().resolve()) if args.data_dir else None,
    }
    info = {
        "CFBundleName": display_name,
        "CFBundleDisplayName": display_name,
        "CFBundleIdentifier": identity,
        "CFBundleExecutable": "CamoufoxStudio",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "2",
        "CFBundleShortVersionString": "0.7.0-studio.2",
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": (
            "Studio uses System Events to create and select the browser profile's macOS desktop."
        ),
    }
    if platform.system() != "Darwin":
        parser.error("The native Studio app must be built on macOS")
    compiler = shutil.which("swiftc")
    if compiler is None:
        parser.error("Install Apple's command line tools before building the native Studio app")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Compile and sign the entire replacement before changing the installed app.
    with tempfile.TemporaryDirectory(prefix=".camoufox-studio-build-", dir=destination.parent) as temporary:
        staging = Path(temporary) / destination.name
        macos = staging / "Contents" / "MacOS"
        resources = staging / "Contents" / "Resources"
        macos.mkdir(parents=True)
        resources.mkdir(parents=True)
        executable = macos / "CamoufoxStudio"
        subprocess.run([
            compiler, "-parse-as-library", "-O", "-module-name", "CamoufoxStudioLauncher",
            "-target", f"{platform.machine()}-apple-macos14.0",
            str(ROOT / "packaging" / "studio_launcher.swift"), "-o", str(executable),
            "-framework", "AppKit", "-framework", "WebKit",
        ], check=True)
        (resources / "studio-runtime.json").write_text(json.dumps(configuration, indent=2) + "\n")
        icon = ROOT / "packaging" / "camoufox-pm.icns"
        if icon.is_file():
            shutil.copy2(icon, resources / "studio.icns")
            info["CFBundleIconFile"] = "studio.icns"
        (staging / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(staging)], check=True)
        previous = Path(temporary) / "previous.app"
        if destination.exists():
            destination.rename(previous)
        try:
            staging.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
    print(destination)


if __name__ == "__main__":
    main()
