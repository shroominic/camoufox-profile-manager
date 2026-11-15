#!/usr/bin/env python3
"""Manage script definition records. This command never executes script code."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

def main():
    from camoufox_pm.config import desktop_data_dir
    from camoufox_pm.core.script_models import ScriptDefinition, definition_from_record
    from camoufox_pm.core.script_store import ScriptStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=desktop_data_dir() / "studio-control.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Validate and insert a JSON script definition")
    importer.add_argument("file", type=Path)
    importer.add_argument("--replace", metavar="SCRIPT_ID")
    importer.add_argument("--revision", type=int)
    commands.add_parser("list", help="List script records without source or runtime inputs")
    exporter = commands.add_parser("export", help="Export a definition without any run input")
    exporter.add_argument("script_id")
    exporter.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "import":
            if bool(args.replace) != (args.revision is not None):
                parser.error("--replace and --revision must be supplied together")
            if args.file.stat().st_size > 512 * 1024:
                parser.error("Script bundle exceeds the size limit")
            definition = ScriptDefinition.model_validate_json(args.file.read_bytes())
            store = ScriptStore(args.database)
            record = store.save_script(definition, id=args.replace, expected_revision=args.revision)
            print(json.dumps({"id": record["id"], "name": record["name"], "revision": record["revision"]}))
        elif args.command == "list":
            records = ScriptStore(args.database).list_scripts()
            print(json.dumps({"scripts": [{key: item[key] for key in ("id", "name", "revision", "enabled")} for item in records]}, indent=2))
        else:
            record = ScriptStore(args.database).get_script(args.script_id)
            if not record:
                parser.error("Script not found")
            payload = definition_from_record(record).model_dump()
            with args.file.open("x", encoding="utf-8") as target:
                json.dump(payload, target, indent=2, ensure_ascii=False)
                target.write("\n")
            print("Script definition exported")
    except Exception:
        print("Script state could not be changed. Check the file, revision, and database location.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
