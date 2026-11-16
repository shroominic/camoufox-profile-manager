# Database scripts in Studio

**Scripts** stores executable Python definitions in the Studio database. A
definition includes its source, input form, named stages, runtime limit, and
revision. Adding another script is a data operation. It does not require a new
page, a dedicated API route, or an app rebuild.

The Studio store is separate from the shared profile database. Script runs refer
to the existing profile manager; they do not copy or import browser profiles.

## Add and run a script

1. Open **Scripts** in Studio.
2. Use **Import JSON** for a definition bundle, or create a new definition in the
   editor. Review its Python source and input form before saving it.
3. Select the definition and fill its run form.
4. Select **Run**. The run list shows its saved revision, current stage, and
   outcome. Use **Stop** to interrupt it.

The source runs locally with the Mac user's permissions. The runner is a
separate process, not a Python sandbox. Only an administrator can manage or run
definitions. Do not import code from an untrusted source.

Definition exports contain the code and form, not account input or run data.
Each run uses the saved source revision. Editing a definition does not change
code that is already running.

The same import can be done from the repository with the generic state-management
CLI. This command saves a definition; it does not run it:

```sh
PYTHONPATH=src .venv/bin/python scripts/manage_studio_scripts.py \
  --database "$HOME/Library/Application Support/Camoufox Profile Manager/studio-control.sqlite3" \
  import examples/studio-scripts/signup-script-2.json
```

Use `list` to find saved IDs, or `export SCRIPT_ID output.json` to export a
definition. An intentional update can use `import FILE --replace SCRIPT_ID
--revision N`; the revision check prevents an edit from overwriting a newer one.
There is no Signup-specific route or automatic seed on app startup.

## Signup Script (2)

Import [signup-script-2.json](../examples/studio-scripts/signup-script-2.json).
The name is retained from the original task. It **signs in to an existing X
account**; it does not create an X account.

Paste the account record into its hidden **Account record** field:

```text
username:password:email:emailpw:2fa-key
```

The email-password field is discarded. It can be omitted:

```text
username:password:email:2fa-key
```

The first fields cannot contain colons. The authenticator field accepts a Base32
key or a TOTP URI. A complete local `authenticator.html?c=...` account-record link
can be pasted into the same field; the parser reads it in memory and does not
open the link.

For a new browser profile, leave **Existing profile** empty. The script selects
a saved HTTP 2Captcha account from **Proxy library**, chooses a supported
California city, and generates a new sticky proxy session. Save the account in
Proxy library first. It remains available when a temporary browser profile goes
to Trash. A single saved account selects automatically; with several accounts,
select **Proxy library source** explicitly. A missing, removed, or incomplete
source stops the run with a fixed proxy error before profile creation. Studio
never asks for a proxy password in a Terminal prompt or reads one from the
environment.

The script then:

1. Validates account input before any manager changes.
2. Stops the separate session recorder and confirms that it stopped.
3. Creates fresh browser storage and a new fingerprint, with US English and
   `America/Los_Angeles` time.
4. Selects a new macOS desktop and launches the profile there.
5. Opens `https://x.com/`, waits for a loaded homepage and stable **Sign in**
   control, then selects it. It waits for the identifier form before entering
   email, then waits between **Use password**, the concealed password field,
   and the local authenticator challenge. The username is available for an extra
   account check. It does not enter by opening a login deep link.
6. Verifies a signed-in page, then restores recording only if it was known to be
   running and enabled before this run.

To use an existing browser profile, select **Existing profile** and leave
**New profile name** empty. **Proxy library source** is optional in this mode;
the existing profile keeps its proxy. An already running profile uses its current
window. This does not silently replace an existing signed-in account.

The sticky proxy duration defaults to 120 minutes. It is a requested proxy
session duration, not a permanent address. The optional seed makes mouse-path
variation reproducible. It does not change credential text.

The browser and saved session remain open after success. The script stops at
login; it does not post, follow accounts, or start a warmup plan. CAPTCHA,
rejected credentials, account locks, unexpected verification, or terms requiring
review stop the login for manual action.

## Private input and recovery

Login failures now have fixed categories for a changed target, wrong field focus
or type, page-load timeout, manual action, and unavailable browser state. Arbitrary
exception text, page content, and credential values are never shown as an error.

Run input is kept in memory and passed to the worker through a private pipe.
Proxy inputs contain only a saved library ID. The runner resolves selected
records privately and passes their credentials to the worker in memory; no
public credential-reading API is added. The form retains only proxy IDs, names,
and server addresses, and clears account inputs on submission.
The script definition, database run rows, URLs, process arguments, normal logs,
and progress events must not contain account values. Printable stdout and
stderr from the script are not used as a run log. Progress uses the definition's
fixed stage IDs. Result metadata contains only the profile ID, city, and status
booleans.

The existing login backend omits authentication screenshots and full page-state
recording. The authenticator secret stays local; only a current one-time code is
sent to X.

**Resume** is an explicit new attempt, not an automatic retry. Enter the account
record again. Leave **Existing profile** and **New profile name** empty; the
saved receipt owns those setup choices. If the receipt is only prepared and has
no profile ID, select the same Proxy library record saved in that receipt.
A different or missing selection
stops recovery. Once the profile exists, Resume uses its saved proxy and does not
require a library source. The adapter copies safe recovery metadata into the new
attempt's private run folder and uses Studio's current connection if its port
changed.
It reuses the saved profile and does not repeat an uncertain creation.

If desktop creation was interrupted before placement could be confirmed, select
the retained desktop and use **Use current desktop**. If a run stops during
private login, recording stays off. Do not restart recording while private
login screens remain visible. A completed login with failed recorder restoration
is recovered by checking the signed-in page, without submitting credentials again.

## Dependencies and adding other scripts

This bundle imports the existing `setup_login`, `account_input`, and profile-tool
modules. Their local import locations are configured for the worker rather than
stored as user-specific paths in the definition. Moving the external tools needs
a configuration update; it does not require copying profiles or credentials.

A script receives a `context` object:

| Field or method | Purpose |
| --- | --- |
| `inputs` | Validated input values for this attempt, in memory only |
| `manager_url` | This Studio process's actual local manager URL |
| `manager_request(path, payload=None, timeout=120)` | Use the current manager connection without exposing its auth headers |
| `proxy_config(id)` | Return a private copy of a proxy selected through a `proxy` input; reject any other ID |
| `run_dir` | Private working folder for this attempt's safe artifacts |
| `resume_dir` | Prior attempt's folder for an explicitly selected Resume, or `None` |
| `stage(id)` | Publish one stage ID declared by this definition |
| `set_result(values)` | Publish bounded result metadata; do not include secrets or paths |
| `fail(code)` | Stop with a supported fixed error code |

Use input type `secret` for account records and other private values. Do not
embed a credential in code, defaults, labels, profile names, stages, or output.
Use input type `proxy` for a Proxy library selector. Mark it optional when an
existing-profile path does not need a new proxy. Keep resolved credentials in
memory; never pass them to `set_result` or write them to recovery artifacts.
The metadata is a definition, not a secret store. Keep browser actions guarded
by live state and use the existing privacy and per-profile locks when adding
other account routines.

The original setup backend passed local dummy-account and desktop tests. A real
X account login remains a separate validation step. Viewing, importing, or saving
this definition does not execute it.

On 16 November 2026, the updated homepage-first flow also passed a real headless
local browser check with public dummy credentials, delayed page transitions,
a read-only email field beside the password field, a normal legal footer, and
a hidden background protection widget. The test completed password and local
authenticator submission without sending an authenticator secret.
