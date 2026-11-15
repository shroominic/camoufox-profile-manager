# Studio acceptance criteria

This is the validation checklist for [STUDIO-PLAN.md](STUDIO-PLAN.md). An item in this file is a requirement, not a test result. Record actual commands, outcomes, platform versions, and relevant limitations in a separate validation report. Do not include account credentials or private recording content in that report.

## Release 1: fork, shared state, and read-only library

### Launch and state ownership

- [ ] The fork has a distinct launcher and visible Studio identity.
- [ ] The launcher points to the existing manager data directory and profile database.
- [ ] The encryption key and profile storage paths resolve to the same existing resources. No profile export/import occurs.
- [ ] With the original desktop manager open, starting Studio shows a clear close-other-manager hint before any database initialization or browser cleanup.
- [ ] A second Studio launch receives the same blocked outcome.
- [ ] A detected manager that does not honor the desktop lock blocks Studio too. Unknown ownership is reported; it does not trigger process termination.
- [ ] Concurrent starts have at most one manager owner. A lock file is never deleted to force ownership.
- [ ] Closing the owning manager releases ownership. A later launch can proceed.
- [ ] An interrupted manager leaves no permanent false block. Recovery checks OS lock/process ownership rather than treating the mere presence of `desktop.lock` as proof of an owner.
- [ ] A compatible original manager can open the shared state after Studio closes.
- [ ] Shared profile IDs, stored proxy configuration, cookies, and fingerprint/storage locations remain intact through a normal app switch.
- [ ] The fork does not auto-upgrade a shared schema to add its first feature.

Use a temporary data directory for race, crash, and invalid-state tests. A production-data check should inspect only the minimal metadata required and must not expose credentials.

### Routine library

- [ ] **Routine library** appears in the sidebar and opens `/routine-library`.
- [ ] The page uses the existing application shell and works in the desktop webview.
- [ ] It reads the configured source catalog in place and does not rewrite it.
- [ ] Totals match the catalog snapshot. Parameterized login is identified separately from recorded variants.
- [ ] Search, length, direction, and readiness filters show matching pools and duration ranges.
- [ ] Empty results have a readable empty state.
- [ ] Unconfigured, missing, unreadable, oversized, malformed, and unsupported catalogs have distinct safe handling.
- [ ] Refresh reads a new snapshot. No playback begins as a result of page load, filtering, or refresh.
- [ ] The API requires administrator access and sends `Cache-Control: no-store`.
- [ ] Responses contain only the documented taxonomy and numeric summaries. Tests inject unexpected labels, profile identifiers, paths, and sensitive-looking values and confirm that none reach the response.
- [ ] Unexpected or duplicate taxonomy keys, non-finite durations, invalid counts, and invalid pool metadata are rejected.
- [ ] API errors and page messages do not reveal source paths or raw catalog data.
- [ ] There is no run, review-write, profile-write, or generic file-read operation on the proof page.
- [ ] Backend checks, frontend type/lint/build checks, and a visible UI review pass. Record any check that could not run.

## Release 2: recording browser and helper bridge

- [ ] Sessions link to stable profile IDs across renames. Ambiguous routing does not assign a session to a guessed profile.
- [ ] New sessions appear without a manager restart.
- [ ] Status distinguishes recording, idle, manual pause, missing permission, stopped helper, stale heartbeat, and unidentified window.
- [ ] An open inactive browser does not show as actively recording.
- [ ] One foreground interaction resumes the correct session; leaving the window stops its active recording state.
- [ ] After 1.5 seconds idle, at most one screenshot is produced for that idle interval. Longer stillness adds none; identical consecutive pixels are discarded.
- [ ] Meaningful actions expose their state reference, request/observation timing, target match, and truncation/unavailable status.
- [ ] Printable typing, clipboard contents, and field values are absent from saved event data.
- [ ] Secure-field detection pauses capture; the UI explains that manual pause is still needed for other private content.
- [ ] Session and screenshot serving is restricted to validated configured roots. Traversal, symlink escape, arbitrary file URLs, and cross-user access are tested.
- [ ] Usage totals are correct for the indexed files and do not read image contents unnecessarily.
- [ ] The first viewer release preserves the installed helper identity and permissions.
- [ ] A later command bridge authenticates its local owner, validates protocol versions, acknowledges state changes, and handles helper restart/disconnection.
- [ ] Deletion previews the selected sessions and dependencies. Referenced source evidence is not silently removed.

## Release 3: run manager and routine composer

### Lifecycle and controls

- [ ] Only typed, known tool entry points can be launched. Paths and parameters cannot become shell commands.
- [ ] A manifest records the profile, reviewed workflow version/hash, seed, selected variants, and safe stage/outcome metadata.
- [ ] Login, routines, scripts, and external CLI runs share a per-profile input lock.
- [ ] A second run for the same profile fails before input. Separate profiles are tested for independent control before parallel runs are enabled.
- [ ] Pause reaches a held-gesture boundary; resume rechecks current state. Stop interrupts and releases held keys/buttons.
- [ ] Manager or worker crashes produce a clear unknown/needs-attention state. Restart does not repeat partial actions automatically.
- [ ] Reconnection verifies worker identity; a reused PID or stale control file cannot attach to a different run.
- [ ] A test on a separate desktop confirms that the physical pointer, active desktop, and foreground app stay unchanged.
- [ ] The selected profile, initial tab, exact origin, geometry, and live target remain guarded for each applicable input.
- [ ] A changed tab, moved/ambiguous control, missing precondition, stale source hash, or late delivery stops safely.
- [ ] Delivery and application postcondition appear as separate outcomes.

### Composition and variants

- [ ] Plans combine ready routines, reading pauses, lengths, directions, repeats, and a seed.
- [ ] Missing length/direction pools are reported before a run. The composer does not invent a longer source clip.
- [ ] Preview/compile performs no input and does not advance live history.
- [ ] Each matching variant is used before that profile's pool repeats. A multi-variant pool avoids a boundary repeat.
- [ ] Independent routine/length/direction/filter pools retain independent histories.
- [ ] History commits only after a completed call and successful input release. A failed call is shown as potentially partial.
- [ ] Review-only assets cannot enter live playback by changing a UI label or client request.
- [ ] Source hashes, complete input pairing, provenance, and target guards survive import and compilation.
- [ ] Recorded movement and click holds are preserved within configured bounds; credential characters remain exact.

Use local fixtures and a designated test profile for live control checks. Do not use an account session for generic engine tests.

## Release 4: setup and private login

- [ ] The GUI reuses the combined setup backend and its recovery receipts.
- [ ] New profiles get fresh browser storage/identity, a supported city request, matching timezone/language, and a new sticky proxy session.
- [ ] Existing-profile selection does not create a duplicate profile or reset its browser state.
- [ ] Requested proxy location and observed exit location are shown separately when both are available.
- [ ] An uncertain create result never triggers an automatic second creation.
- [ ] Desktop placement is verified when platform evidence permits it; unknown and mismatch are not labeled verified.
- [ ] Credential fields use private process IPC and stay out of URLs, process arguments, traces, screenshots, model calls, and crash reports.
- [ ] TOTP generation stays local and uses a fresh valid period near entry.
- [ ] Recorder stop is confirmed before auth. A restarted recorder stops further credential entry.
- [ ] Successful auth may restore only the known prior recorder state. Failed/interrupted auth leaves capture stopped and states that result.
- [ ] CAPTCHA, terms, locks, unexpected verification, or invalid credentials stop for manual action. There is no automatic credential retry.
- [ ] Local auth fixtures pass before a separately authorized real-account check. Fixture success and real-account success have different labels.
- [ ] Secret-store persistence, if added, is tested separately. Memory-only input remains possible.

## Release 5: Content ideas

- [ ] Ideas support title, notes, source, tags, drafts, media references, status, and optional profile links.
- [ ] Search, filters, draft versions, and import/export preserve data.
- [ ] Profile rename/deletion does not erase an idea or attach it to a different profile.
- [ ] Duplicate sources are flagged as hints; distinct drafts are not discarded automatically.
- [ ] Missing media links are visible and do not expose arbitrary filesystem content.
- [ ] Ideas and drafts are stored in Studio metadata, separate from proxy/auth secrets and run traces.
- [ ] Creating, editing, marking ready, or scheduling a draft does not itself publish content.

## Release 6: branches and scheduling

- [ ] Local conditions filter predefined branches against current available state.
- [ ] A provider receives only the declared compact context and allowed choice set.
- [ ] Invalid responses, unavailable branches, provider failure, or changed UI state stop or take only an authored fallback.
- [ ] Provider keys use the approved secret boundary and never enter a workflow or trace.
- [ ] Runs expose the selected branch and safe timing/outcome evidence without saving credentials or arbitrary page content.
- [ ] Studio run schedules use the existing scheduler with a typed run reference.
- [ ] Timezone, overlap, sleep/wake, skipped run, and maximum-duration behavior are tested.
- [ ] Missed work does not burst after wake. Partial account actions are not silently resumed.

## Data compatibility and release checks

- [ ] Studio metadata migrations have explicit versions, backups, and failure recovery.
- [ ] Profile references remain stable; no Studio feature requires experimental fields in the upstream profile schema.
- [ ] Upstream and fork schema requirements are checked before switching versions. An incompatible downgrade is blocked with a clear explanation.
- [ ] Local tool paths come from configuration. Repository source contains no private workspace paths, recordings, account identifiers, passwords, TOTP secrets, or API keys.
- [ ] Existing tool CLIs and recording/catalog formats continue to work during packaging changes.
- [ ] Validation states exact tested scope. Untested native dialogs, inaccessible UI, live account flows, and delivery-only checks remain labeled as limits.

## Evidence to retain

For each release, retain a sanitized validation report with the source revision, checks run, results, UI review, fixtures used, and unresolved limits. Use hashes and safe IDs where provenance is required. Keep private screenshots and full recording data outside the repository.
