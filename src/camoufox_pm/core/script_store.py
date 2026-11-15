"""Separate Studio database for generic scripts and sanitized run receipts."""

import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .script_models import (
    EVENT_KINDS,
    RUN_STATUSES,
    STAGE_ID,
    TERMINAL_STATUSES,
    ScriptDefinition,
    definition_from_record,
)

MAX_SCRIPTS = 250
MAX_EVENTS = 500
RUN_LIMIT = 100


class ScriptConflict(ValueError):
    """A script or run changed, or an operation would overlap active work."""


class ScriptNotFound(ValueError):
    """The selected script or run does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(value) -> dict:
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("Invalid script result")
    clean = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
            raise ValueError("Invalid script result")
        if type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("Invalid script result")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Invalid script result")
        if isinstance(item, str) and len(item) > 8192:
            raise ValueError("Invalid script result")
        clean[key] = item
    return clean


def public_run(value: dict) -> dict:
    """Allowlisted HTTP view; private source snapshots and runtime paths stay local."""
    names = (
        "id", "script_id", "name", "revision", "status", "stage", "error_code",
        "created_at", "started_at", "finished_at", "resume_of", "source_sha256", "events",
    )
    result = {key: value.get(key) for key in names}
    result["result"] = {
        key: item for key, item in value.get("result", {}).items()
        if not re.search(r"path|receipt|directory|filename", key, re.I)
        and not (isinstance(item, str) and (item.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:[\\/]", item)))
    }
    return result


class ScriptStore:
    """Open short-lived connections so API and worker callbacks can share safely."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise ScriptConflict("This script database needs a newer Studio version.")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                "CREATE TABLE IF NOT EXISTS studio_scripts ("
                "id TEXT PRIMARY KEY,revision INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0,body TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS studio_script_runs ("
                "id TEXT PRIMARY KEY,script_id TEXT NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL);"
                "CREATE INDEX IF NOT EXISTS studio_script_runs_script ON studio_script_runs(script_id);"
                "CREATE TABLE IF NOT EXISTS studio_script_events ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,body TEXT NOT NULL);"
                "PRAGMA user_version=1;"
            )
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def _connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def list_scripts(self) -> list[dict]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT body FROM studio_scripts WHERE archived=0 ORDER BY rowid DESC LIMIT ?",
                (MAX_SCRIPTS,),
            ).fetchall()
        return [{key: value for key, value in json.loads(row[0]).items() if key != "code"} for row in rows]

    def get_script(self, identifier: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT body FROM studio_scripts WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_script(self, definition, id=None, expected_revision=None) -> dict:
        definition = definition if isinstance(definition, ScriptDefinition) else ScriptDefinition.model_validate(definition)
        with self._connection(write=True) as db:
            row = db.execute("SELECT body,revision,archived FROM studio_scripts WHERE id=?", (id,)).fetchone() if id else None
            if id and (not row or row[1] != expected_revision or row[2]):
                raise ScriptConflict("Script changed. Reload before saving.")
            if not id and db.execute("SELECT COUNT(*) FROM studio_scripts WHERE archived=0").fetchone()[0] >= MAX_SCRIPTS:
                raise ScriptConflict("The script library is full. Archive an unused script first.")
            prior = json.loads(row[0]) if row else None
            value = {
                **definition.model_dump(), "id": id or str(uuid4()),
                "revision": prior["revision"] + 1 if prior else 1,
                "created_at": prior["created_at"] if prior else _now(),
                "updated_at": _now(), "archived": False,
            }
            if prior:
                db.execute("UPDATE studio_scripts SET revision=?,body=? WHERE id=?", (value["revision"], json.dumps(value), id))
            else:
                db.execute("INSERT INTO studio_scripts VALUES (?,?,0,?)", (value["id"], 1, json.dumps(value)))
        return value

    def archive_script(self, identifier: str, revision: int) -> None:
        with self._connection(write=True) as db:
            row = db.execute("SELECT body,revision,archived FROM studio_scripts WHERE id=?", (identifier,)).fetchone()
            if not row or row[1] != revision or row[2]:
                raise ScriptConflict("Script changed. Reload before archiving.")
            if db.execute("SELECT 1 FROM studio_script_runs WHERE script_id=? AND status IN ('queued','running','stopping')", (identifier,)).fetchone():
                raise ScriptConflict("Stop the active script run before archiving.")
            value = json.loads(row[0])
            value.update(archived=True, enabled=False, updated_at=_now(), revision=revision + 1)
            db.execute("UPDATE studio_scripts SET archived=1,revision=?,body=? WHERE id=?", (revision + 1, json.dumps(value), identifier))

    def create_run(self, script: dict, resume_of: str | None = None) -> dict:
        with self._connection(write=True) as db:
            row = db.execute("SELECT body FROM studio_scripts WHERE id=?", (script["id"],)).fetchone()
            if not row:
                raise ScriptNotFound("Script not found.")
            current = json.loads(row[0])
            if current["archived"] or not current["enabled"]:
                raise ScriptConflict("The script is disabled or archived.")
            if current["revision"] != script["revision"]:
                raise ScriptConflict("Script changed. Reload before running.")
            selected, revision, name = script, script["revision"], script["name"]
            if resume_of:
                previous = db.execute("SELECT body FROM studio_script_runs WHERE id=?", (resume_of,)).fetchone()
                previous = json.loads(previous[0]) if previous else None
                if not previous or previous["script_id"] != script["id"]:
                    raise ScriptNotFound("Script run not found.")
                if previous["status"] not in {"failed", "cancelled", "interrupted"}:
                    raise ScriptConflict("Only stopped or incomplete runs can be resumed.")
                if previous["revision"] != current["revision"]:
                    raise ScriptConflict("The script changed since that run. Review recovery before starting again.")
                selected, revision, name = previous["snapshot"], previous["revision"], previous["name"]
            snapshot = definition_from_record(selected).model_dump()
            value = {
                "id": str(uuid4()), "script_id": script["id"], "name": name,
                "revision": revision, "status": "queued", "stage": None,
                "error_code": None, "created_at": _now(), "started_at": None,
                "finished_at": None, "resume_of": resume_of, "result": {},
                "source_sha256": hashlib.sha256(snapshot["code"].encode()).hexdigest(),
                "snapshot": snapshot,
            }
            db.execute("INSERT INTO studio_script_runs VALUES (?,?,?,?)", (value["id"], script["id"], "queued", json.dumps(value)))
        return self.get_run(value["id"])

    def _events(self, db, identifier):
        return [
            {**json.loads(row[1]), "sequence": row[0]}
            for row in db.execute(
                "SELECT sequence,body FROM studio_script_events WHERE run_id=? ORDER BY sequence LIMIT ?",
                (identifier, MAX_EVENTS),
            )
        ]

    def get_run(self, identifier: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT body FROM studio_script_runs WHERE id=?", (identifier,)).fetchone()
            if not row:
                return None
            value = json.loads(row[0])
            value["events"] = self._events(db, identifier)
        return value

    def list_runs(self, script_id: str | None = None) -> list[dict]:
        with self._connection() as db:
            query = "SELECT body FROM studio_script_runs"
            args = []
            if script_id is not None:
                query += " WHERE script_id=?"
                args.append(script_id)
            query += " ORDER BY rowid DESC LIMIT ?"
            args.append(RUN_LIMIT)
            values = [json.loads(row[0]) for row in db.execute(query, args)]
            for value in values:
                value["events"] = self._events(db, value["id"])
        return values

    def update_run(self, identifier: str, **fields) -> dict:
        if set(fields) - {"status", "stage", "error_code", "started_at", "finished_at", "result"}:
            raise ValueError("Unknown script run field")
        with self._connection(write=True) as db:
            row = db.execute("SELECT body FROM studio_script_runs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ScriptNotFound("Script run not found.")
            value = json.loads(row[0])
            status = fields.get("status", value["status"])
            if status not in RUN_STATUSES:
                raise ValueError("Invalid script run status")
            if value["status"] in TERMINAL_STATUSES and status != value["status"]:
                raise ScriptConflict("The script run is already finished.")
            if "stage" in fields and fields["stage"] is not None and fields["stage"] not in {stage["id"] for stage in value["snapshot"]["stages"]}:
                raise ValueError("Unknown script stage")
            if "error_code" in fields and fields["error_code"] is not None and (not isinstance(fields["error_code"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", fields["error_code"])):
                raise ValueError("Invalid script error code")
            if "result" in fields:
                fields["result"] = _result(fields["result"])
            value.update(fields)
            if status == "running" and value["started_at"] is None:
                value["started_at"] = _now()
            if status in TERMINAL_STATUSES and value["finished_at"] is None:
                value["finished_at"] = _now()
            db.execute("UPDATE studio_script_runs SET status=?,body=? WHERE id=?", (status, json.dumps(value), identifier))
        return self.get_run(identifier)

    def append_event(self, identifier: str, kind: str, stage: str | None = None) -> dict:
        if kind not in EVENT_KINDS or (stage is not None and (not isinstance(stage, str) or not STAGE_ID.fullmatch(stage))):
            raise ValueError("Invalid script event")
        with self._connection(write=True) as db:
            row = db.execute("SELECT body FROM studio_script_runs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ScriptNotFound("Script run not found.")
            run = json.loads(row[0])
            if stage is not None and stage not in {item["id"] for item in run["snapshot"]["stages"]}:
                raise ValueError("Unknown script stage")
            if kind == "stage" and run["status"] in TERMINAL_STATUSES:
                raise ScriptConflict("The script run is already finished.")
            if db.execute("SELECT COUNT(*) FROM studio_script_events WHERE run_id=?", (identifier,)).fetchone()[0] >= MAX_EVENTS:
                raise ScriptConflict("The script event limit was reached.")
            event = {"kind": kind, "stage": stage, "created_at": _now()}
            cursor = db.execute("INSERT INTO studio_script_events(run_id,body) VALUES (?,?)", (identifier, json.dumps(event)))
            event["sequence"] = cursor.lastrowid
        return event

    def recover_active_runs(self) -> int:
        with self._connection(write=True) as db:
            rows = db.execute("SELECT id,body FROM studio_script_runs WHERE status IN ('queued','running','stopping')").fetchall()
            for identifier, body in rows:
                value = json.loads(body)
                value.update(status="interrupted", finished_at=_now(), error_code="interrupted")
                db.execute("UPDATE studio_script_runs SET status='interrupted',body=? WHERE id=?", (json.dumps(value), identifier))
                db.execute("INSERT INTO studio_script_events(run_id,body) VALUES (?,?)", (identifier, json.dumps({"kind": "interrupted", "stage": value["stage"], "created_at": _now()})))
        return len(rows)
