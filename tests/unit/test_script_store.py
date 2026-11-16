"""Script definitions are revisioned data; runtime input never enters receipts."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from camoufox_pm.core.script_models import ScriptDefinition, validate_run_inputs
from camoufox_pm.core.script_store import ScriptConflict, ScriptStore, public_run


def definition(**updates):
    value = {
        "name": "Sample script", "code": "context.stage('working')\n",
        "inputs": [
            {"name": "account", "label": "Account", "type": "secret", "required": True},
            {"name": "count", "label": "Count", "type": "integer", "default": 2},
            {"name": "selected", "label": "Profile", "type": "profile"},
            {"name": "mode", "label": "Mode", "type": "select", "default": "slow", "choices": [{"value": "slow", "label": "Slow"}]},
        ],
        "stages": [{"id": "working", "label": "Working"}],
    }
    value.update(updates)
    return ScriptDefinition.model_validate(value)


def test_store_is_separate_and_definitions_survive_restart(tmp_path):
    profile_db = tmp_path / "profiles.db"
    profile_db.write_bytes(b"existing-profile-state")
    path = tmp_path / "studio-control.sqlite3"
    store = ScriptStore(path)
    script = store.save_script(definition())
    assert script["revision"] == 1
    assert ScriptStore(path).get_script(script["id"])["code"] == script["code"]
    assert "code" not in store.list_scripts()[0]
    assert profile_db.read_bytes() == b"existing-profile-state"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("code", ["def broken(", "return 2", "await action()", "\x00"])
def test_invalid_python_is_refused_without_execution(code):
    with pytest.raises(ValidationError):
        definition(code=code)


def test_secret_defaults_and_duplicate_names_are_rejected():
    with pytest.raises(ValidationError, match="Secret inputs"):
        definition(inputs=[{"name": "secret", "label": "Secret", "type": "secret", "default": "do-not-store"}])
    with pytest.raises(ValidationError, match="unique"):
        definition(stages=[{"id": "work", "label": "One"}, {"id": "work", "label": "Two"}])
    with pytest.raises(ValidationError, match="unique"):
        definition(inputs=[{"name": "same", "label": "One", "type": "text"}, {"name": "same", "label": "Two", "type": "text"}])


def test_private_input_validation_is_exact_and_has_no_value_in_errors():
    script = definition()
    private = "private-account-sentinel"
    values = validate_run_inputs(script, {"account": private})
    assert values == {"account": private, "count": 2, "selected": None, "mode": "slow"}
    for bad in ({"account": private, "count": True}, {"account": private, "unknown": private}, {"account": private, "selected": private}, {"account": private, "mode": private}):
        with pytest.raises(ValueError) as exc:
            validate_run_inputs(script, bad)
        assert private not in str(exc.value)


def test_required_and_oversized_inputs_are_refused():
    with pytest.raises(ValueError, match="required"):
        validate_run_inputs(definition(), {})
    with pytest.raises(ValueError):
        validate_run_inputs(definition(), {"account": "x" * 65537})


def test_proxy_inputs_accept_native_library_ids_defaults_and_optional_blanks():
    script = definition(inputs=[
        {"name": "proxy", "label": "Saved proxy", "type": "proxy", "default": "abCD1234"},
        {"name": "optional", "label": "Optional proxy", "type": "proxy"},
    ])
    assert validate_run_inputs(script, {}) == {"proxy": "abCD1234", "optional": None}
    assert validate_run_inputs(script, {"optional": ""})["optional"] is None
    for value in (True, "short", "proxy.example:8080:user:private", "../abCD1234"):
        with pytest.raises(ValueError, match="valid saved proxy|bounded text"):
            validate_run_inputs(script, {"proxy": value})
    with pytest.raises(ValidationError, match="valid saved proxy"):
        definition(inputs=[{"name": "proxy", "label": "Proxy", "type": "proxy", "default": "invalid"}])
    with pytest.raises(ValueError, match="required"):
        validate_run_inputs(definition(inputs=[{
            "name": "proxy", "label": "Proxy", "type": "proxy", "required": True,
        }]), {"proxy": ""})


def test_revision_compare_and_swap_prevents_lost_updates(tmp_path):
    store = ScriptStore(tmp_path / "scripts.db")
    script = store.save_script(definition())

    def update(name):
        try:
            return store.save_script(definition(name=name), id=script["id"], expected_revision=1)
        except ScriptConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["First", "Second"]))
    assert sum(result is not None for result in results) == 1
    assert store.get_script(script["id"])["revision"] == 2


def test_run_pins_snapshot_and_public_receipt_excludes_source(tmp_path):
    store = ScriptStore(tmp_path / "scripts.db")
    script = store.save_script(definition())
    run = store.create_run(script)
    store.save_script(definition(code="print('new code')"), id=script["id"], expected_revision=1)
    restored = ScriptStore(store.path).get_run(run["id"])
    assert restored["snapshot"]["code"] == script["code"]
    public = public_run(restored)
    assert "snapshot" not in public and "code" not in public
    assert public["source_sha256"] == run["source_sha256"]


def test_events_are_only_fixed_kinds_and_declared_stages(tmp_path):
    store = ScriptStore(tmp_path / "scripts.db")
    run = store.create_run(store.save_script(definition()))
    event = store.append_event(run["id"], "stage", "working")
    assert store.get_run(run["id"])["events"][0]["sequence"] == event["sequence"]
    with pytest.raises(ValueError):
        store.append_event(run["id"], "private-message")
    with pytest.raises(ValueError):
        store.append_event(run["id"], "stage", "undeclared")
    with pytest.raises(ValueError):
        store.update_run(run["id"], stage="undeclared")


def test_terminal_runs_cannot_restart_or_receive_late_stage(tmp_path):
    store = ScriptStore(tmp_path / "scripts.db")
    run = store.create_run(store.save_script(definition()))
    store.update_run(run["id"], status="running")
    final = store.update_run(run["id"], status="succeeded", result={"count": 3})
    assert final["started_at"] and final["finished_at"]
    with pytest.raises(ScriptConflict):
        store.update_run(run["id"], status="running")
    with pytest.raises(ScriptConflict):
        store.append_event(run["id"], "stage", "working")


def test_recovery_marks_active_runs_interrupted_and_does_not_restart(tmp_path):
    store = ScriptStore(tmp_path / "scripts.db")
    script = store.save_script(definition())
    active = store.create_run(script)
    completed = store.create_run(script)
    store.update_run(completed["id"], status="succeeded")
    assert ScriptStore(store.path).recover_active_runs() == 1
    recovered = store.get_run(active["id"])
    assert recovered["status"] == "interrupted"
    assert recovered["error_code"] == "interrupted"
    assert recovered["events"][-1]["kind"] == "interrupted"
    assert store.get_run(completed["id"])["status"] == "succeeded"
    assert store.recover_active_runs() == 0


def test_resume_pins_same_revision_and_rejects_completed_or_changed_script(tmp_path):
    store = ScriptStore(tmp_path / "scripts.db")
    script = store.save_script(definition())
    prior = store.create_run(script)
    store.update_run(prior["id"], status="failed")
    resumed = store.create_run(script, resume_of=prior["id"])
    assert resumed["resume_of"] == prior["id"]
    assert resumed["snapshot"] == prior["snapshot"]
    with pytest.raises(ScriptConflict):
        store.create_run(script, resume_of=resumed["id"])
    current = store.save_script(definition(name="Changed"), id=script["id"], expected_revision=1)
    with pytest.raises(ScriptConflict):
        store.create_run(current, resume_of=prior["id"])


def test_archive_retains_history_and_refuses_active_runs(tmp_path):
    store = ScriptStore(tmp_path / "scripts.db")
    script = store.save_script(definition())
    run = store.create_run(script)
    with pytest.raises(ScriptConflict):
        store.archive_script(script["id"], 1)
    store.update_run(run["id"], status="cancelled")
    store.archive_script(script["id"], 1)
    assert store.list_scripts() == []
    assert store.get_script(script["id"])["archived"]
    assert store.get_run(run["id"])["status"] == "cancelled"
    with pytest.raises(ScriptConflict):
        store.create_run(store.get_script(script["id"]))


def test_public_results_do_not_expose_local_recovery_paths(tmp_path):
    store = ScriptStore(tmp_path / "scripts.db")
    run = store.create_run(store.save_script(definition()))
    run = store.update_run(run["id"], result={"profile_id": "abcdefgh", "receipt": "/private/local.json", "extra": "~/private.txt", "count": 4})
    assert public_run(run)["result"] == {"profile_id": "abcdefgh", "count": 4}


def test_newer_store_version_is_not_downgraded(tmp_path):
    path = tmp_path / "scripts.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=9")
    with pytest.raises(ScriptConflict, match="newer"):
        ScriptStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
