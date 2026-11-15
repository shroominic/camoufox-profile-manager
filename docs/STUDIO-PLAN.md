# Camoufox Studio integration plan

Studio is a local fork of Camoufox Profile Manager. It will bring the existing recording, profile setup, and recorded-input tools into one interface. It uses the existing profiles and browser storage. No profile import is required.

This document separates existing tools from planned Studio features. It is a design plan, not a claim that all integrations have passed tests. Release checks are in [STUDIO-ACCEPTANCE.md](STUDIO-ACCEPTANCE.md).

## Scope and shared state

The fork changes the profile manager application. It does not build a separate Camoufox browser engine.

The installed manager and Studio must use the same data directory, profile database, encryption key, profile storage paths, and browser cache. Existing profile IDs remain the link between these resources. On macOS, the normal data directory is `~/Library/Application Support/Camoufox Profile Manager/`.

Only one manager may own this data directory at a time. The desktop launcher uses its existing `desktop.lock` boundary. When another manager owns the data, Studio must explain that the other manager must close before Studio can start. It must not delete the lock or stop the other app automatically. A process that does not honor this lock also needs detection before the fork can open shared state.

Fork-only metadata will use a separate, planned `studio-control.sqlite3` file in the shared data directory. This avoids adding experimental tables or fields to the upstream profile schema. The first read-only feature needs no Studio database.

The same data means changes made in either manager are visible after switching apps. Existing cookies, storage, fingerprints, proxy settings, and encryption material stay in their existing locations. A backup is required before a later integration adds a shared-schema migration. Database backups must use a consistent SQLite snapshot; copying a live database file alone is insufficient.

## Existing components

These tools currently live outside this repository. The paths below are relative to their existing tool roots. Studio must resolve those roots from local configuration. It must not assume a developer's absolute workspace path.

| Component | Existing work | Studio work still required |
| --- | --- | --- |
| Recorder | `CamoufoxSessionRecorder/Sources/`: native macOS event capture, per-profile session routing, action-triggered Accessibility state, idle capture, deduplication, status file | Recording browser, signed helper bridge, retention controls, service ownership |
| Routine library | `hybrid-control/routine_catalog.py`, `routines.py`, `routines/catalog.json`: source-linked clips, length/direction pools, review state, per-profile variant cycles | Integrated catalog, previews, review flow, composition and run controls |
| Input controller | `hybrid-control/hybrid.py`, `gecko.py`, `controls.py`: recorded paths, bounded variation, live target checks, virtual pointer, Pause/Resume/Stop | Supervised run service, typed API, live run status and trace viewer |
| Profile setup | `proxy-tools/create_california_profile.py`, `california_proxy.py`, Space helpers: supported cities, fresh identity, timezone, desktop placement, recovery receipts | Setup wizard and configuration adapters |
| Login and combined setup | `hybrid-control/x_login.py`, `setup_login.py`, `account_input.py`, `recorder_privacy.py`: existing-account login and combined create/desktop/login sequence | Private credential UI, process IPC, recovery UI, credential-store option |
| Decisions | `hybrid-control/choices.py`: local rules and optional OpenRouter/Jev choices from a fixed branch set | Branch editor, provider settings, cost/latency display, run-service integration |
| Content ideas | No reusable content idea library was found in the reviewed tool roots | New local data model and interface |

The source catalog reviewed for this plan contains 40 routine types and 334 selected variants. Of these, 175 recorded variants are ready and 159 require review. These counts describe a source snapshot. Studio must read current values rather than hardcode them.

## Phase 1: shared-state fork and Routine library

This is the proof feature for the fork.

- Add **Routine library** to the existing sidebar at `/routine-library`.
- Read the configured existing catalog in place. Do not copy its clips or recordings into the repository.
- Show routine counts, candidate counts, ready/review counts, duration ranges, length bands, and direction pools.
- Support search, length, direction, and readiness filters.
- Show distinct missing, invalid, and unconfigured states. Refresh reads a new snapshot.
- Expose only fixed taxonomy labels and numeric aggregates through the first API. Keep source paths, recorded labels, page content, profile IDs, and raw inputs out of its response.
- Use administrator access and `Cache-Control: no-store` for the summary API.
- Keep the page read-only. It does not launch playback, change review state, or write profile data.

Implementation references: `src/camoufox_pm/core/routine_library.py`, `src/camoufox_pm/api/routes/routine_library.py`, and `web/src/app/routine-library/page.tsx`.

The private login entry is parameterized, not a recorded clip. The page must identify this distinction.

## Phase 2: Recordings

Add **Recordings** with a profile filter, session list, timeline, action context, screenshot viewer, recording status, and storage totals.

Use stable profile IDs for linkage. A profile rename must not split its history or reassign an old recording. Read existing sessions from the configured recordings root, normally `~/Movies/Camoufox Sessions/`. Index metadata in the Studio database; keep source files in place.

Status must distinguish active recording, idle, manual pause, unavailable permissions, stopped helper, stale heartbeat, and an unidentified browser. An open browser alone is not evidence of active recording.

Keep these recorder rules:

- Record only interaction with a foreground Camoufox window that resolves to one profile.
- After 1.5 seconds without input, capture one screenshot, then wait for new input.
- Drop identical consecutive screenshot pixels. A long idle interval creates no extra screenshots.
- Request UI context for meaningful actions or gesture starts. Do not poll every page change.
- Exclude printable key values, clipboard data, and field values. Pause when a secure field is detected.
- Keep observation timing, missing state, truncation, and target-match evidence visible in the viewer.

The recorder is a native signed helper. First integrate status and viewing without rebuilding or replacing it. Changing its signature or bundle identity can invalidate macOS permissions. A later signed helper bridge should use a small versioned local IPC contract for status, manual pause/resume, and session rotation. Commands must come from the local manager owner and report acknowledgment. Do not infer pause success from a UI button alone.

Retention starts with usage totals and a reviewable delete selection. Deleting a session must account for routines that refer to its source. Never silently remove source evidence needed by a published routine version.

## Phase 3: Runs, scripts, and routine composition

Add **Scripts** and a persistent run panel. Reuse the existing Python engine before moving code into a shared package.

The manager starts only known tool entry points through an argument array. It must not execute arbitrary shell text. Use a versioned IPC protocol with run ID, profile ID, stage, outcome, and typed control commands. Credentials use a separate private channel.

One profile has one active input owner. The same profile lock must cover login, standalone routines, compiled plans, and scripts, including runs started outside the GUI. Different profiles can have independent runs after isolation tests pass.

Run states are queued, validating, running, pause requested, paused, stopping, completed, stopped, failed, and needs attention. A manager restart must reconcile a known worker's identity before offering control. It must not restart a partially completed script automatically. Stop releases held inputs; pause waits for a held gesture to end.

The routine composer provides:

- Ordered routines, reading pauses, short/medium/long choices, direction, repeat count, and optional seed.
- Duration ranges and pool availability before execution.
- Per-profile shuffle cycles with no repeat until the chosen pool is exhausted. Avoid an immediate repeat across cycle boundaries when the pool has multiple variants.
- Separate histories for routine/length/direction/filter pools.
- A side-effect-free compile/validate preview. Preview does not advance live history.
- Immutable run manifests with source hashes. History advances only after a routine completes and inputs are released.
- A separate review queue. Missing text, unclear targets, partial gestures, and unsupported native actions remain unavailable for playback.

Use Gecko virtual input for recorded playback. It has passed earlier separate-desktop tests without moving the physical pointer. Read-only UI inventories supply conditions and targets. Do not replace this with the experimental native CGEvent route, which did not reliably control off-Space Firefox windows.

Keep profile, selected-tab, exact-origin, viewport, target position, hash, and timing guards. Small path/timing variation must not alter credentials or change the intended control. A successful input delivery is separate from a successful page postcondition.

## Phase 4: profile and private-login wizard

The combined setup backend already exists in `hybrid-control/setup_login.py`. Studio needs an interface and service adapter, rather than a new setup algorithm.

The wizard should allow an existing selected profile or a new profile. New setup selects an allowed saved proxy source, a supported city, a fresh sticky session, a fresh browser identity, matching timezone/language, and a new desktop. Show requested location separately from measured proxy location. A supported city does not guarantee an available city IP.

Use the existing recovery receipt after each confirmed stage. If creation or desktop placement has an uncertain result, show recovery instructions and retain the saved profile. Do not repeat the operation automatically.

Existing-account login follows identifier, password, and local authenticator TOTP. Use a private dialog and process channel, never URL parameters, process arguments, normal logs, screenshots, or a decision-model prompt. Optional saved credentials need a separate OS secret-store design. The first integration can use memory-only input.

Stop the recorder and confirm its stopped state before authentication. Refuse further auth input if it restarts. Restore only the recorder state that was known before login, and only after a safe signed-in page is verified. Failed or interrupted login leaves recording stopped and shows a clear status.

CAPTCHA, account locks, invalid credentials, unexpected verification, and terms requiring review stop the script for manual action. Do not auto-accept agreements or retry failed credentials.

## Phase 5: Content ideas

This is new work. Start with a local library rather than a posting engine.

Suggested fields: idea ID, title, notes, source URL, tags, intended profile links, platform, draft text, media references, status, and timestamps. Initial states are inbox, selected, drafting, ready, used, and archived. Profile links use stable IDs and can be removed without deleting an idea.

Provide search, tags, status/profile filters, duplicate source hints, draft versions, and import/export. Store media by reference first; report missing files. Add explicit attachment management later if needed.

Keep ideas/drafts separate from action traces and account secrets. Generation, scheduling, and publishing require separate typed operations with visible targets and results. A saved idea or a scheduled draft must not cause a post to be sent by itself.

## Phase 6: decisions and scheduling

Add local branch conditions first. A branch chooses among supplied actions whose live preconditions pass. Optional OpenRouter/Jev remains an adapter, and uses a secret-store reference or process environment for its key.

Send compact allowed-choice context to a provider. Keep credentials, screenshots, full page trees, and arbitrary recorded text out of decision requests. Invalid choices, provider errors, or changed UI state stop or follow an explicitly authored fallback. They must not invent a new action.

The upstream manager already has schedules. Extend its scheduler with typed Studio run references after checking compatibility; do not add a second independent timer service. Support bounded duration, chosen profile, time zone, overlap policy, and clear run history. Do not burst missed runs after sleep or resume a partially completed account action silently.

## Data and migration strategy

1. Start with the existing profile store unchanged and an external, configured read-only catalog.
2. Add `studio-control.sqlite3` with its own schema version and migration journal. Back up this file before upgrades.
3. Link profiles by stable ID. Store foreign profile references without adding foreign keys or columns to the upstream database.
4. Introduce adapter contracts for the external tools. Validate their version/capabilities and report unavailable tools before starting work.
5. Package proven adapters as shared libraries incrementally. Preserve CLI entry points and existing file formats during the transition.
6. Keep session/clip files out of Git. Keep local source locations in user configuration, not tracked defaults.
7. Before an upstream upgrade, compare shared schema and browser-storage requirements. Block an incompatible downgrade. Do not migrate the shared profile store just to support a Studio-only feature.
8. Make switching back to the original manager possible by closing Studio and reopening the original app. Studio-only metadata can remain unused by the original app.

## Known limits

- A macOS desktop does not itself isolate input. The working Gecko virtual-input backend supplies the current independent pointer behavior.
- The recorder observes macOS Accessibility state after an action request. A fast transition can change the page before observation completes. This is not an atomic DOM snapshot.
- Some original recordings omit typed values or complete button/key pairs. Those gaps cannot be reconstructed as exact actions.
- Closed shadow roots, inaccessible frames, canvas controls, and native dialogs may not appear in the control inventory.
- Scroll checks prove target continuity and input delivery, not which feed or nested panel moved.
- Long variants use real recorded bursts and pauses. Pool coverage differs by direction; a missing long/up variant is not synthesized by slowing a short clip.
- Existing-account login has local fixture evidence. A real-account flow must be validated separately before the GUI labels it live-verified.
- Prior standalone validation does not prove the integrated manager lifecycle. Each integration needs the checks in the acceptance document.

## Recommended next step

After the shared-state launch and Routine library proof pass, implement the Recordings viewer and a read-only helper status bridge. Then add supervised runs. This order makes recorded evidence and service state visible before Studio gains input controls.
