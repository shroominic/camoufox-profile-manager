"""Execute the imported bundle against fake tools, with no browser or account effects."""

import argparse
import json
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

BUNDLE = Path(__file__).resolve().parents[2] / "examples/studio-scripts/signup-script-2.json"
ACCOUNT_INPUT = "private-test-input-not-for-persistence"
MANAGER = "http://127.0.0.1:41234"
PROFILE = "test0001"
PROXY_SOURCE = "base0001"
PRIVATE_PROXY = "private-proxy-value"
PROXY_CONFIG = {"type": "http", "server": "na.proxy.2captcha.com:2333",
                "username": "private-proxy-user", "password": "private-proxy-password"}


class ControlledFailure(Exception):
    pass


class FakeContext:
    def __init__(self, root, inputs=None, resume_dir=None):
        self.inputs = {"account_record": ACCOUNT_INPUT, "minutes": "120",
                       "proxy_library_id": PROXY_SOURCE, **(inputs or {})}
        self.manager_url = MANAGER
        self.run_dir = root / "current"
        self.run_dir.mkdir()
        self.resume_dir = resume_dir
        self.stages = []
        self.requests = []
        self.result = None
        self.proxy_calls = []
        self.private_proxies = {PROXY_SOURCE: dict(PROXY_CONFIG)}

    def stage(self, identifier):
        allowed = {item["id"] for item in json.loads(BUNDLE.read_text())["stages"]}
        assert identifier in allowed
        self.stages.append(identifier)

    def manager_request(self, path, payload=None, timeout=120):
        self.requests.append((path, payload, timeout))
        return {"authenticated": True, "role": "administrator"}

    def set_result(self, result):
        self.result = result

    def proxy_config(self, identifier):
        self.proxy_calls.append(identifier)
        if identifier not in self.private_proxies:
            raise ValueError("not a selected proxy")
        return dict(self.private_proxies[identifier])

    def fail(self, code):
        raise ControlledFailure(code)


class FakeSetup(ModuleType):
    def __init__(self):
        super().__init__("setup_login")
        self.PROFILE_ID = re.compile(r"[A-Za-z0-9]{8}")
        self.proxy_calls = []
        self.executions = []
        self.proxy_result = (PRIVATE_PROXY, PROXY_SOURCE)
        self.proxy_conversions = []
        self.execution_error = None
        self.other_manager = None
        self.hidden_proxy = lambda: pytest.fail("a Terminal proxy prompt was used")

        def original_request(*args, **kwargs):
            pytest.fail("the original API fallback was used")

        class Receipt:
            def __init__(self, path, data):
                self.path, self.data = Path(path), data

            @classmethod
            def load(cls, path):
                return cls(path, json.loads(Path(path).read_text()))

            def update(self, phase, **fields):
                self.data.update(fields)
                self.data["phase"] = phase
                self.path.write_text(json.dumps(self.data))

        self.profiles = SimpleNamespace(
            RunReceipt=Receipt,
            manager_request=original_request,
            validate_manager_url=lambda value: value.rstrip("/"),
            proxy_credential_from_profile=self.proxy_credential_from_profile,
        )

    def proxy_credential_from_profile(self, profile):
        self.proxy_conversions.append(profile)
        if profile != {"proxy_config": PROXY_CONFIG}:
            raise ValueError("invalid proxy provider or login")
        return PRIVATE_PROXY

    def parser(self):
        parser = argparse.ArgumentParser()
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--resume", type=Path)
        group.add_argument("--profile-id")
        parser.add_argument("--manager-url")
        parser.add_argument("--no-start-manager", action="store_true")
        parser.add_argument("--no-controls", action="store_true")
        parser.add_argument("--receipt", type=Path)
        parser.add_argument("--proxy-from-profile")
        parser.add_argument("--minutes", type=int, choices=[1, 5, 10, 15, 30, 60, 120])
        parser.add_argument("--name")
        parser.add_argument("--seed", type=int)
        parser.add_argument("--current-desktop", action="store_true")
        return parser

    def saved_proxy(self, manager, selected=None):
        self.proxy_calls.append((manager, selected))
        return self.proxy_result

    def resume_receipt(self, path, lab=False):
        assert lab is False
        receipt = self.profiles.RunReceipt.load(path)
        if receipt.data.get("phase") == "complete":
            raise ValueError("already completed")
        return receipt

    def execute(self, account, options, **private_options):
        self.executions.append((account, options, private_options))
        self.profiles.manager_request(self.other_manager or options.manager_url, "/auth/session")
        if options.resume:
            receipt = self.profiles.RunReceipt.load(options.resume)
        else:
            receipt = self.profiles.RunReceipt(options.receipt, {"manager": options.manager_url})
        receipt.update("prepared")
        receipt.update("prepared", recorderPauseRequested=True)
        receipt.update("profile_created", profileId=PROFILE)
        receipt.update("desktop_selected")
        receipt.update("browser_running")
        receipt.update("login_requested")
        if self.execution_error is not None:
            raise self.execution_error
        receipt.update("authenticated")
        receipt.update("complete")
        return {
            "profileId": PROFILE,
            "city": "testcity",
            "authenticated": True,
            "totpUsed": True,
            "recorderRestored": False,
            "receipt": str(receipt.path),
            "extra_private_value": ACCOUNT_INPUT,
        }


@pytest.fixture
def fake_tools(monkeypatch):
    setup = FakeSetup()
    account = ModuleType("account_input")
    parsed = []

    def parse(value):
        parsed.append(value)
        if value == "invalid":
            raise ValueError("invalid account input: " + value)
        return SimpleNamespace(private_value=value)

    account.parse_account = parse
    monkeypatch.setitem(sys.modules, "setup_login", setup)
    monkeypatch.setitem(sys.modules, "account_input", account)
    return setup, parsed


def execute_bundle(context):
    code = json.loads(BUNDLE.read_text())["code"]
    exec(compile(code, "<database-signup-script>", "exec"), {"context": context})


def test_bundle_has_private_input_and_declared_stages():
    bundle = json.loads(BUNDLE.read_text())
    compile(bundle["code"], "<database-signup-script>", "exec")
    field = next(item for item in bundle["inputs"] if item["name"] == "account_record")
    assert field["type"] == "secret"
    assert "default" not in field
    assert bundle["language"] == "python"
    assert "existing X account" in bundle["description"]
    assert len({item["id"] for item in bundle["stages"]}) == len(bundle["stages"])
    proxy = next(item for item in bundle["inputs"] if item["name"] == "proxy_library_id")
    assert proxy["type"] == "proxy" and proxy["required"] is False
    assert not any(item["name"] == "proxy_from_profile" for item in bundle["inputs"])


def test_saved_proxy_is_selected_and_account_values_stay_out_of_metadata(tmp_path, fake_tools, monkeypatch):
    setup, parsed = fake_tools
    monkeypatch.setenv("TWOCAPTCHA_PROXY", "an-environment-fallback-must-not-be-used")
    context = FakeContext(tmp_path, {"seed": 123})
    original_request, original_update = setup.profiles.manager_request, setup.profiles.RunReceipt.update
    execute_bundle(context)
    assert parsed == [ACCOUNT_INPUT]
    assert setup.proxy_calls == []
    assert context.proxy_calls == [PROXY_SOURCE]
    account, options, private_options = setup.executions[0]
    assert account.private_value == ACCOUNT_INPUT
    assert options.proxy_from_profile is None
    assert private_options == {"proxy_credential": PRIVATE_PROXY, "proxy_library_id": PROXY_SOURCE}
    assert options.no_start_manager is True and options.no_controls is True
    assert options.manager_url == MANAGER and options.seed == 123
    assert ACCOUNT_INPUT not in repr(vars(options))
    assert PRIVATE_PROXY not in repr(vars(options))
    assert PRIVATE_PROXY not in repr(context.result)
    assert context.result == {
        "profile_id": PROFILE,
        "authenticated": True,
        "totp_used": True,
        "recorder_restored": False,
        "city": "testcity",
    }
    assert "pause_recorder" in context.stages and "login" in context.stages
    assert context.stages[-1] == "complete"
    assert ACCOUNT_INPUT not in (context.run_dir / "setup-receipt.json").read_text()
    for private in (PRIVATE_PROXY, PROXY_CONFIG["username"], PROXY_CONFIG["password"]):
        assert private not in (context.run_dir / "setup-receipt.json").read_text()
    assert setup.profiles.manager_request is original_request
    assert setup.profiles.RunReceipt.update is original_update


def test_existing_profile_needs_no_proxy_or_profile_creation_settings(tmp_path, fake_tools):
    setup, _ = fake_tools
    context = FakeContext(tmp_path, {"profile_id": PROFILE})
    execute_bundle(context)
    assert setup.proxy_calls == []
    assert context.proxy_calls == []
    assert setup.executions[0][2] == {}
    options = setup.executions[0][1]
    assert options.profile_id == PROFILE
    assert options.proxy_from_profile is None


def test_missing_library_selection_fails_before_execution(tmp_path, fake_tools):
    setup, _ = fake_tools
    context = FakeContext(tmp_path, {"proxy_library_id": ""})
    with pytest.raises(ControlledFailure, match="missing_proxy_source"):
        execute_bundle(context)
    assert setup.executions == []
    assert context.proxy_calls == setup.proxy_calls == []
    assert not (context.run_dir / "setup-receipt.json").exists()


@pytest.mark.parametrize("source,config", [("not-an-id", PROXY_CONFIG), (PROXY_SOURCE, {"type": "socks5"})])
def test_invalid_library_source_never_falls_back_or_starts(tmp_path, fake_tools, source, config):
    setup, _ = fake_tools
    context = FakeContext(tmp_path, {"proxy_library_id": source})
    context.private_proxies[PROXY_SOURCE] = config
    with pytest.raises(ControlledFailure, match="proxy_source_invalid"):
        execute_bundle(context)
    assert setup.executions == setup.proxy_calls == []
    assert not (context.run_dir / "setup-receipt.json").exists()


def test_invalid_account_fails_before_manager_access(tmp_path, fake_tools):
    setup, parsed = fake_tools
    context = FakeContext(tmp_path, {"account_record": "invalid"})
    with pytest.raises(ControlledFailure, match="preflight_failed"):
        execute_bundle(context)
    assert parsed == ["invalid"]
    assert setup.proxy_calls == setup.executions == context.requests == []
    assert context.proxy_calls == []


def test_conflicting_existing_profile_options_are_rejected(tmp_path, fake_tools):
    setup, _ = fake_tools
    context = FakeContext(tmp_path, {"profile_id": PROFILE, "name": "new-name"})
    with pytest.raises(ControlledFailure, match="preflight_failed"):
        execute_bundle(context)
    assert setup.proxy_calls == setup.executions == []


def test_resume_copies_receipt_and_uses_current_manager_connection(tmp_path, fake_tools):
    setup, _ = fake_tools
    prior = tmp_path / "prior"
    prior.mkdir()
    original = json.dumps({"phase": "login_requested", "manager": "http://127.0.0.1:40000", "profileId": PROFILE})
    (prior / "setup-receipt.json").write_text(original)
    context = FakeContext(tmp_path, resume_dir=prior)
    execute_bundle(context)
    assert (prior / "setup-receipt.json").read_text() == original
    options = setup.executions[0][1]
    assert options.resume == context.run_dir / "setup-receipt.json"
    assert options.receipt is None and options.profile_id is None
    assert json.loads(options.resume.read_text())["manager"] == MANAGER
    assert options.resume.stat().st_mode & 0o777 == 0o600
    assert setup.proxy_calls == []


def test_prepared_resume_uses_saved_library_source(tmp_path, fake_tools):
    setup, _ = fake_tools
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "setup-receipt.json").write_text(json.dumps({
        "phase": "prepared", "manager": MANAGER, "profileId": None,
        "proxySourceLibrary": PROXY_SOURCE,
    }))
    context = FakeContext(tmp_path, resume_dir=prior)
    execute_bundle(context)
    assert setup.proxy_calls == []
    assert context.proxy_calls == [PROXY_SOURCE]
    assert setup.executions[0][2] == {"proxy_credential": PRIVATE_PROXY, "proxy_library_id": PROXY_SOURCE}


def test_prepared_resume_rejects_another_library_source_before_copy(tmp_path, fake_tools):
    setup, _ = fake_tools
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "setup-receipt.json").write_text(json.dumps({
        "phase": "prepared", "manager": MANAGER, "profileId": None,
        "proxySourceLibrary": "other001",
    }))
    context = FakeContext(tmp_path, resume_dir=prior)
    with pytest.raises(ControlledFailure, match="proxy_source_invalid"):
        execute_bundle(context)
    assert setup.executions == context.proxy_calls == []
    assert not (context.run_dir / "setup-receipt.json").exists()


def test_interruption_restores_tool_hooks_and_does_not_report_success(tmp_path, fake_tools):
    setup, _ = fake_tools
    setup.execution_error = KeyboardInterrupt()
    context = FakeContext(tmp_path)
    originals = setup.profiles.manager_request, setup.profiles.RunReceipt.update, setup.hidden_proxy
    with pytest.raises(KeyboardInterrupt):
        execute_bundle(context)
    assert (setup.profiles.manager_request, setup.profiles.RunReceipt.update, setup.hidden_proxy) == originals
    assert context.result is None
    assert context.stages[-1] == "login"


def test_manager_bridge_refuses_a_different_origin(tmp_path, fake_tools):
    setup, _ = fake_tools
    setup.other_manager = "http://127.0.0.1:49999"
    context = FakeContext(tmp_path)
    with pytest.raises(ControlledFailure, match="preflight_failed"):
        execute_bundle(context)
    assert context.requests == []
