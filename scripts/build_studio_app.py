#!/usr/bin/env python3
"""Install a distinct local .app without changing the original app or its Python env."""

import argparse
import hashlib
import plistlib
import shlex
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path.home() / "Applications" / "Camoufox Studio.app")
    parser.add_argument("--routine-catalog", type=Path, default=ROOT.parent / "hybrid-control" / "routines" / "catalog.json")
    parser.add_argument("--data-dir", type=Path, help="Use an explicit data folder for a separate test app")
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
    macos = destination / "Contents" / "MacOS"
    resources = destination / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    quote = shlex.quote
    arguments = ""
    if args.data_dir:
        arguments = f" --data-dir {quote(str(args.data_dir.expanduser().resolve()))}"
    launcher = (
        "#!/bin/zsh\n"
        f"cd {quote(str(ROOT))} || exit 1\n"
        # CPython 3.13 skips Finder-hidden editable .pth files on macOS.
        # Pin imports to this checkout even when that metadata is inherited.
        f"export PYTHONPATH={quote(str(ROOT / 'src'))}\n"
        # An app opened by Finder must not inherit an unrelated shell data override.
        "unset CPM_SECRET_KEY CPM_WEBUI_DIR\n"
        'export CPM_DB_PATH="$HOME/Library/Application Support/Camoufox Profile Manager/profiles.db"\n'
        f"export CPM_ROUTINE_CATALOG_PATH={quote(str(args.routine_catalog.expanduser().resolve()))}\n"
        f"exec {quote(str(python))} -m camoufox_pm.studio{arguments}\n"
    )
    executable = macos / "CamoufoxStudio"
    executable.write_text(launcher)
    executable.chmod(0o755)
    info = {
        "CFBundleName": display_name,
        "CFBundleDisplayName": display_name,
        "CFBundleIdentifier": identity,
        "CFBundleExecutable": "CamoufoxStudio",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "0.7.0-studio.1",
        "NSHighResolutionCapable": True,
    }
    icon = ROOT / "packaging" / "camoufox-pm.icns"
    if icon.is_file():
        shutil.copy2(icon, resources / "studio.icns")
        info["CFBundleIconFile"] = "studio.icns"
    (destination / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    print(destination)


if __name__ == "__main__":
    main()
