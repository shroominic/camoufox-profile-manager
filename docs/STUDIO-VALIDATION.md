# Studio proof validation

Checked on 15 November 2026 on macOS. These are completed checks. The broader
future requirements are in [STUDIO-ACCEPTANCE.md](STUDIO-ACCEPTANCE.md).

## Shared state

- The installed Studio launcher selects the installed manager's exact standard
  macOS `profiles.db`, its original encryption key, and its profile storage root.
- A local SQLite backup and a private copy of the original key were saved under
  the shared data directory's `studio-control/backups/` before the launch checks.
- The real original desktop manager's lock rejected Studio's startup check.
  Bootstrap and the Studio API did not start against the occupied data folder.
- The original key still matches that backup, and all profile IDs from the
  baseline remain present. Another manager was active during the later checks;
  its own profile updates were allowed to continue.
- Automated checks cover original/fork mutual exclusion, crash lock release,
  active/stale/reused manager PIDs, recovery state, unreadable status, missing
  keys, custom database filenames, orphan browser profile arguments, startup
  failures, and uncertain shutdown ownership.

## Native interface

The original manager reopened with a running browser during validation. It was
left running. The native UI proof therefore used a separate empty temporary
data folder, with the same fork code and the real external routine catalog.
The preview has a different bundle identity from the installed shared-state app.

Observed in the native window:

- The **Studio** badge and **Routine library** sidebar item.
- 40 routine types, 334 recorded variants, 175 ready variants, 159 review variants,
  and 2,035 source candidates.
- Search for `scroll` reduced the table to the scroll routine.
- Selecting **Long** showed 34 matching variants, with recorded durations from
  8.02 to 31.9 seconds, including separate ready/review pools.
- The page is read-only. No replay, login, profile creation, or account actions
  were started from Studio.

A successful GUI launch against the occupied real store is intentionally not
claimed. Close the original manager normally, then open the installed Studio
app to use that shared state. The original CLI/web manager limitation is explained
in [STUDIO.md](../STUDIO.md).

## Code checks

- 98 focused Python tests passed with `--no-network` and loopback access.
- All 29 web regression tests passed, including translation coverage in Russian
  and Chinese.
- TypeScript checking and targeted ESLint/Ruff passed.
- The production static export built successfully with Webpack and includes
  `/routine-library/`; its output is bundled into the fork's Python package.
- Turbopack could not bind a worker port in this environment. The documented
  build command supports `--webpack` as a working alternative.
- The native launcher explicitly selects this checkout's source. This avoids
  CPython 3.13 skipping an editable `.pth` file if it gets Finder's hidden flag.

No shared profile schema changes or browser engine changes are part of this proof.

The development commit is unsigned because the configured 1Password signing
agent was unavailable. No persistent Git signing settings were changed.
