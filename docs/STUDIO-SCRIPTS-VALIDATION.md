# Database script validation

Checked on 15 November 2026 on macOS.

## Completed code and state checks

- 161 focused Python tests passed with `--no-network` and local test ports.
- All 33 web regression tests passed, including typed run forms, secret-default
  exclusion, definition export, request headers, and Russian/Chinese coverage.
- TypeScript, targeted ESLint, Ruff, and whitespace checks passed.
- The production static export built successfully and bundles `/scripts/`.
- The installed launcher now supplies the configured local tool import roots.
- One **Signup Script (2)** definition was inserted through the generic import
  command into `studio-control.sqlite3`. Its code and input schema match the
  reviewed JSON bundle. No account inputs or account runs were inserted.

## Execution evidence

Tests use real child Python workers and temporary SQLite databases. Two unrelated
definitions were added and run through the same HTTP API to prove that executable
definitions are database state, without a new route or app implementation for
each script.

Checks cover immutable source revisions, private input through stdin, suppression
of stdout/stderr, fixed errors, transient manager-auth redaction, Stop during
spawn, shutdown, runtime limits, lock release after errors, explicit recovery,
and the absence of private values in temporary database and run files.

The Signup bundle passed ten fake-tool lifecycle tests. These check account
validation before effects, saved proxy selection, original recorder privacy,
fixed progress stages, existing-profile mode, the current app connection, and
safe recovery copies. They do not submit credentials to X.

Scripts API checks also cover administrator access, loopback host/port pinning,
same-origin mutation headers, bounded JSON bodies, and private validation errors.

## Native app check

After the Mac was unlocked, Studio was restarted normally against the shared
data store. The native Profiles page retained eight profiles, and the two
previously open browser profiles were running again after the restart.

The native **Scripts** page displayed exactly one saved **Signup Script (2)**,
revision 1, with its 900-second limit and ten declared progress stages. The Run
form displayed a secure account-record field, existing-profile and proxy-source
selectors, optional profile name and variation seed, sticky-session duration,
current-desktop option, and **Run script**. Its history said **No runs yet**.
The Scripts page was left open for the user.

No real account login or new browser profile was started by this feature's
validation. A real X login remains a separate check with user-supplied account
input in the app's private form.
