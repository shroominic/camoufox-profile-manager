# Camoufox Studio

This development fork uses the installed Camoufox Profile Manager's saved data.
It changes the manager interface; it does not fork the Firefox/Camoufox browser engine.

**Routine library** in the sidebar reads the external
recorded-routine catalog and shows counts, duration ranges, length, direction,
and review status. It does not execute routines or edit recordings.

**Scripts** stores Python source, input forms, and revisions in the separate
Studio database. Import or edit a definition, enter private input for one run,
and use the native run history and Stop controls. [Signup Script (2) and the
generic script format](docs/STUDIO-SCRIPTS.md) reuse the existing setup/login tools.

## Install the local development app

From this checkout:

```sh
uv sync --frozen --extra desktop --extra dev
.venv/bin/python scripts/build_webui.py --webpack
.venv/bin/python scripts/build_studio_app.py --routine-catalog /path/to/hybrid-control/routines/catalog.json
```

Open `~/Applications/Camoufox Studio.app`. The app uses this checkout and its own
Python environment. Keep the checkout in place. Rebuild the web UI after frontend
edits; restart Studio after Python edits. The original app and its environment
are separate.

The installed app has a compiled AppKit/WKWebView host. Python runs the local
backend, without opening a second Python GUI. The native host retains the app's
macOS identity and closes the backend through a private pipe. Normal Quit and
parent exit request browser cleanup before the shared-state lock is released.
Building this host requires Apple's command line tools.

Desktop setup needs **Camoufox Studio** access in Privacy & Security →
Accessibility, plus its System Events automation approval. This is app access;
do not enable a general Python interpreter as a substitute. A read-only check
runs inside the actual script worker before a new profile is created or the
recorder is stopped. Missing access produces a fixed desktop permission error.
Current-desktop mode and an already running profile do not need desktop control.

For a terminal launch, set the catalog path and use the guarded entry point:

```sh
PYTHONPATH=src CPM_ROUTINE_CATALOG_PATH=/path/to/hybrid-control/routines/catalog.json \
  .venv/bin/python -m camoufox_pm.studio
```

`--check` checks ownership without starting the API. `--data-dir /temporary/path`
uses a separate test data folder. An explicit `CPM_DB_PATH` is also respected by
the terminal entry point, including its filename. The Finder launcher pins the
installed app's standard macOS data location.

For a separate native preview, use `build_studio_app.py --destination /path/to/Preview.app
--data-dir /temporary/path`. The preview gets a distinct bundle ID. The main
launcher always uses the shared data folder unless you explicitly build it with
`--data-dir`.

## Shared data and switching apps

Both desktop apps use `~/Library/Application Support/Camoufox Profile Manager/`:
the same `profiles.db`, `secret.key`, and browser profile folders. No import or
profile copy is needed. Editing shared profiles in either app affects the same
data. This is development against the real saved state, not a disposable copy.

Close the current manager normally before opening the other. Studio acquires
the original `desktop.lock` before bootstrap or database startup. If another
desktop owns it, Studio displays a close-the-other-app message and refuses to
start. Crashes release the OS lock. It also checks the existing manager registry
and shared-root orphan browser processes. It never deletes browser locks or
adopts live browser sessions.

Legacy web/CLI managers do not all honor this desktop lock. Studio detects one
that is already registered at startup, but it cannot block an old CLI launched
later. Use the desktop launchers for shared-state testing. Do not run an old
CLI/web manager against the same folder during Studio use.

No shared-profile schema changes are part of these features. Script definitions
and run metadata use `studio-control.sqlite3` beside the profile database.
Before an upstream version change,
check shared schema compatibility. Close Studio before switching back.

Studio logs go to `studio-control/studio.log` under the shared data directory.
Credentials, recordings, and routine clips stay outside the source repository.
The catalog endpoint returns allowlisted aggregate metadata only.

## Plans

- [Feature integration plan](docs/STUDIO-PLAN.md)
- [Acceptance criteria](docs/STUDIO-ACCEPTANCE.md)
- [Validation evidence](docs/STUDIO-VALIDATION.md)
- [Database scripts and Signup Script (2)](docs/STUDIO-SCRIPTS.md)
