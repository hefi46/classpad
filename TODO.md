# Classpad — Build TODO

Work through these phases in order. Each phase should be functional and testable before moving to the next.

---

## Phase 1: Repo & Project Scaffold

- [x] Create directory structure as defined in CLAUDE.md
- [x] Create placeholder `__init__.py` and stub files for each module
- [x] Create `requirements.txt` for launcher and bar dependencies
- [x] Create `server/requirements.txt` for Flask server
- [x] Create `.gitignore` (Python, venv, SQLite files, `__pycache__`)
- [x] Verify the project structure matches CLAUDE.md before proceeding — `find . -not -path './.git*' | sort` matches the CLAUDE.md tree; non-module directories use `.gitkeep` pending their own phase

---

## Phase 2: PyGTK Persistent Bar

Build this first — it needs to be running before the launcher so you can verify strut behaviour.

- [x] Create `bar/bar.py` — GTK window, fixed height 55px, full screen width, positioned at top
- [x] Set `_NET_WM_STATE_ABOVE` (always-on-top)
- [x] Set `_NET_WM_STRUT_PARTIAL` to reserve 55px at top of screen so other windows do not overlap it — implemented via `python3-xlib` directly (`Gdk.Window.property_change` isn't exposed by GObject-Introspection in this binding)
- [x] Add Home button (left side) — placeholder action: print to stdout for now
- [x] Add live clock (centre) — updates every second
- [x] Add current activity label (between Home and clock) — reads from a shared state file `/tmp/classpad_activity`
- [x] Add volume control (bar is the only place it's reachable — not optional)
- [x] Test: open TuxPaint (non-fullscreen) — bar remains visible above it (verified on real hardware)
- [x] Test: use `xprop` on the bar window to verify strut and state hints are set correctly (verified: `_NET_WM_STRUT_PARTIAL`, `_NET_WM_STATE_ABOVE`, `_NET_WM_WINDOW_TYPE_DOCK` all correct; `_NET_WORKAREA` correctly reserves the top 55px)
- [x] **GATE — RUN, real hardware, 2026-07-30:** `gcompris-qt` and `tuxmath` (note: `gcompris` package no longer exists in trixie, only the Qt rewrite) both go genuinely `_NET_WM_STATE_FULLSCREEN` and stack above the bar, covering it. **Result: gate fails as suspected — struts/above-state do not survive real fullscreen.** Decision: accept this rather than force these apps out of fullscreen (see next gate — the hotkey covers it, and both apps have their own reachable exit). See CLAUDE.md "Persistent Bar" and "Recovery model."
- [x] **GATE — RUN, real hardware, 2026-07-30:** `xbindkeys` bound to `RShift+RCtrl+F12`, tested via both synthetic (`xdotool key`) and real physical keypress, with `gcompris-qt` and `tuxmath` each fullscreen and focused. **Result: passes both times, both apps.** Neither establishes a blocking active keyboard grab. This is what makes accepting the previous gate's failure safe.
- [x] Confirmed both `gcompris-qt` and `tuxmath` have their own reachable exit (Escape / on-screen close control) for a pre-reading child — the hotkey is an emergency fallback, not the routine way out.
- [x] **Architecture decision (2026-07-30):** the bar is guaranteed on the home screen and over non-fullscreen apps only, not over genuinely fullscreen ones. No Openbox rules forcing apps out of fullscreen — that fallback wasn't needed since the hotkey gate passed.
- [x] **"Call Teacher" removed from scope entirely** — no bar button, no debounce logic, no hardware-hotkey substitute. The teacher is physically present in the classroom. (Hardware volume/media keys were investigated as an unrelated volume-control mitigation and found not to generate any event on this hardware at all — see `pre-build-decisions.md` §1b — so that idea is dropped too, not because of this decision.)
- [x] Make bar auto-start via Openbox `autostart` file (assumes deployment path `/opt/classpad/`, matching the plugin directory convention; xbindkeys and the launcher systemd service are added to this same file in Phases 6/11, not yet)

---

## Phase 3: Plugin System (local)

- [x] Define and document the `manifest.json` schema (see CLAUDE.md) — must be finalised before building anything that reads it. Finalised 2026-07-30: field table (required/optional per type), `id` format `^[a-z0-9][a-z0-9-]*$`, `version` as `major.minor.patch`. `launch_command`/`chromium_flags` stay shell-like strings (matching the existing CLAUDE.md example and pre-build-decisions.md §3), split with `shlex.split()` and passed as argv with `shell=False` — no shell is ever invoked, so no rewrite to a JSON array was needed.
- [x] Create `launcher/plugin_manager.py`:
  - Scans `/opt/classpad/plugins/` for installed plugin directories
  - Reads and validates each `manifest.json`
  - Returns a list of plugin objects sorted by name
  - Invalid plugins (missing manifest, malformed JSON, missing fields/icon, bad id/version/type) are skipped with a stderr warning rather than crashing the scan
- [x] Create example manifests for: tuxpaint, tuxtype, tuxmath, a placeholder website button
  - TuxPaint's `launch_command` should use native fullscreen (`--fullscreen=yes`), not the windowed `--windowed --1366x713` variant tested during the Phase 2 gate — decided 2026-07-30, since TuxPaint has its own reachable exit (same reasoning as gcompris-qt/tuxmath in Phase 2), so it doesn't need the bar visible either. The windowed test was a real proof of concept (confirmed to size and stack correctly) but isn't the chosen approach.
  - `plugins/website-placeholder/` created with a placeholder `url` — not a real curated site, `URLAllowlist` decision is still open (see CLAUDE.md Open Risks)
- [x] Write unit tests for plugin_manager — valid manifest, missing fields, malformed JSON (`tests/test_plugin_manager.py`, 18 cases, run via `python3 -m pytest`; `python3-pytest` installed via apt since this image is externally-managed)
- [x] Create `scripts/plugin-install.sh` — accepts a plugin zip path, extracts to `/opt/classpad/plugins/<id>/`, runs `install.sh` if present. Plugins dir overridable via `CLASSPAD_PLUGINS_DIR` env var for testing.
- [x] Harden `plugin-install.sh` against path traversal — reject any archive entry containing `..` or an absolute path *before* extraction (validated in the same Python invocation that extracts, so a rejected zip never touches disk outside the staging dir). Also validates the manifest's `id` field against `^[a-z0-9][a-z0-9-]*$` *before* it's used to build the install/`rm -rf` target path — a malicious `id` (e.g. `../../etc`) is a second, independent traversal vector. `tests/test_plugin_install.py`, 8 cases including a malicious-entry zip (`../../etc/cron.d/x`), an absolute-path entry, and a malicious manifest `id` — all confirmed rejected with nothing created outside the scratch plugins dir.
- [x] Confirm no upload path exists for plugin zips outside the admin portal (Phase 8) — plugin-install.sh should only ever be invoked with a path the server already vetted. Confirmed: `server/routes/*.py` and `server/app.py` are still empty stubs, no route of any kind exists yet, so there is no upload path anywhere in the current codebase (upload lands in Phase 8).

---

## Phase 4: Pygame Launcher

- [x] Create `launcher/main.py` — Pygame init, fullscreen window positioned below the bar (y-offset = bar height, height = screen height minus bar height). Uses `SDL_VIDEO_WINDOW_POS` + `pygame.NOFRAME` (no built-in EWMH strut concept in pygame/SDL, unlike the GTK bar) to position/size the window; `pygame.init()` (not just `pygame.display.init()`) is required so `pygame.font` works — an earlier version only initialised `display`+`mixer` and would have crashed the moment a real button with a label was rendered, caught via real on-hardware testing before it shipped.
- [x] Create `launcher/button.py` — Button class: renders icon + label, handles hover and click states, plays a click sound on press. Label font size shrinks to fit the tile width (down to a floor) — found by testing a real 12-plugin grid on hardware: long names like "Website Placeholder" spilled out of their tile and into the neighbour at a fixed font size.
- [x] Create `launcher/config.py` — builds button grid layout (`main.py` loads the plugin list directly from `plugin_manager.scan_plugins()`; a `config.load_plugins()` pass-through wrapper was written then removed — nothing needed the indirection). `compute_grid_dimensions()` picks cols/rows by maximising resulting tile size for the actual screen aspect ratio rather than a fixed breakpoint table.
- [x] Render button grid from installed plugins — evenly spaced, large icons (128px, comfortably over the 96px minimum), readable labels (DejaVu Sans Bold — present on the target Debian image, no bundled font file needed)
- [x] Handle different numbers of plugins gracefully (1 button up to ~12) — verified via `tests/test_config.py` (layout math: no overlap, all tiles in-bounds, all tiles fit the real rendered icon size, for every count 1–12 at the real 1366x713 target resolution) plus a real 12-plugin run on hardware (see label-shrinking fix above, found by this test)
- [x] Add a background colour/image appropriate for young children — flat soft sky-blue; no image asset needed
- [x] Test: launcher fills the screen below the bar exactly, no overlap — **verified on real hardware (2026-07-30)**: ran the actual `python3 -m launcher.main` (not a stand-in script) against real plugins installed to `/opt/classpad/plugins`, alongside the real bar. `xwininfo` confirms the launcher window is exactly `1366x713+0+55`, and the screenshot shows real buttons rendered under the real bar with no overlap. Also resolved the open "how are website buttons visually distinguished from app buttons" question from `pre-build-decisions.md` §2 while testing this: they aren't — all button types render identically (see that file).
- [x] Add audio feedback on button click (short positive sound) — `launcher/assets/sounds/click.wav`, a short two-note chime generated via the stdlib `wave` module (no bundled binary asset, no numpy dependency); verified it plays through `pygame.mixer` on real hardware and fires on real `xdotool` clicks in the button grid

---

## Phase 5: Process Manager & App Launching

- [x] Create `launcher/process_manager.py`:
  - `launch(plugin)` — spawns the correct subprocess based on plugin type
  - `wait_for_exit()` — monitors the process and signals when it exits
  - `kill_all()` — kills all tracked child processes by name (for recovery)
- [x] `launch()` must invoke `launch_command` as an argv list with `shell=False` — never build a shell string. It comes from a server-controlled manifest; treat it as untrusted input.
- [x] For `type: app` — launch binary from `launch_command` in manifest
- [x] For `type: website` — launch Chromium with correct flags (see CLAUDE.md — use `--app`, NOT `--kiosk`). Each launch gets a dedicated `--user-data-dir` (`tempfile.mkdtemp`, cleaned up on exit/kill) — without one, Chromium hands off to an already-running instance on the same profile and the launched process exits immediately, so `wait()` would return while the browser is still on screen. **Verified on real hardware (2026-07-30)** — see gate below.
- [x] Create `system/chromium/policies/managed/classpad-policy.json` with `FullscreenAllowed: false` — **verified on real hardware (2026-07-30)**: F11 inside `--app` mode was escaping to real fullscreen and hiding the bar; this policy blocks it, confirmed F11 is now a no-op. Also confirmed `--app --start-maximized` alone (no `--kiosk`) already respects the strut correctly — window fills exactly the area below the bar, bar stays stacked on top.
- [ ] Add `URLAllowlist` to the same policy file once the curated site list is decided — still open, `--app --incognito` alone does not stop in-page navigation off a curated site to uncontrolled content. Don't add a guessed domain list.
- [ ] Install the policy file to `/etc/chromium/policies/managed/` as part of `scripts/install.sh` (Phase 11) — done manually on the dev machine for this test, not yet automated
- [x] When a child process exits, update `/tmp/classpad_activity` to empty and return launcher to home screen
- [x] Write current activity name to `/tmp/classpad_activity` on launch (bar reads this)
- [x] **GATE — RUN, real hardware, 2026-07-30:** full cycle click -> app launches -> app closes -> launcher returns, tested via the real `bar.bar` + `launcher.main` processes (not stand-ins) against the real installed plugins in `/opt/classpad/plugins`, driven by `xdotool` synthetic clicks against the real X server, with `xwininfo`/`xprop`/`scrot` used to verify window state at each step. Two cases: `tuxmath` (type `app`, real fullscreen) closed via its own on-screen close control (the red X, a genuinely reachable child-usable exit, not a kill) — launcher correctly returned and redrew the button grid; `website-placeholder` (type `website`) closed via its window controls — same result. `/tmp/classpad_activity` was observed transitioning correctly at each step (empty -> plugin name -> empty) in both cases.
- [x] **GATE — RUN, real hardware, 2026-07-30:** bar visibility during the cycle — confirmed **type-dependent, matching the Phase 2 finding, not a new regression**: for `website-placeholder` (Chromium `--app --start-maximized`, not genuinely fullscreen) the bar stayed visible throughout, confirmed via `_NET_WM_STATE` (`MAXIMIZED_VERT`/`MAXIMIZED_HORZ`, no `FULLSCREEN`), `_NET_WORKAREA` (still `0,55,1366,713`), and a screenshot showing the bar stacked above the Chromium window. For `tuxmath` (genuinely `_NET_WM_STATE_FULLSCREEN`) the bar was covered, exactly as documented in Phase 2 — expected, not a gate failure.
- [x] **GATE — RUN, real hardware, 2026-07-30:** Chromium `--user-data-dir` hand-off fix — with a stray `chromium` process already running under the *default* profile (simulating an imperfect post-recovery-kill leftover) and the launcher window raised/focused (the real idle-kiosk state), clicking `website-placeholder` spawned a genuinely new, independent Chromium process tree with its own `--user-data-dir`; `wait()` correctly blocked on that new process (not the stray) until *it* was closed, at which point only its profile dir was cleaned up and the stray was left untouched. First attempt at this test gave a false negative (no new process appeared) — traced to the stray window not being raised, so Openbox's click-to-focus semantics ate the synthetic click before it reached the un-focused launcher; not a process_manager bug (a standalone script confirmed `launch()` works correctly against a live stray even before this was diagnosed). Real-world relevance: the launcher is always the raised/focused window while idle, so this failure mode isn't reachable in actual kiosk operation.
- [x] Queued-click-causes-relaunch fix (`pygame.event.clear()` after `wait_for_exit()` returns, added during Phase 5 review) — partially exercised on hardware: sent 3 extra clicks at the launched tile's coordinates while `tuxmath` was running fullscreen; no relaunch occurred on close. Note this specific run didn't exercise the fix's actual code path — a genuinely fullscreen child occludes the launcher window at the X server level, so those synthetic clicks were delivered to `tuxmath` itself, not queued against the covered pygame window. The narrower race the fix targets (clicks landing in the brief window before a child's own window fully covers the screen, or events queued between the child's exit and pygame resuming its event pump) wasn't independently reproduced — left in place as defensive, reasoned-through hardening rather than a hardware-confirmed fix.

---

## Phase 6: Staff Recovery System

- [x] Create `scripts/recovery.sh` — kills all processes in the kill list (chromium, tuxpaint, tuxtype, tuxmath, gcompris-qt — corrected, matching `launcher/process_manager.py`'s `KILL_LIST`), clears `/tmp/classpad_activity`. Uses `pkill -9` (SIGKILL), not the `pkill` default (SIGTERM) — **found on real hardware** that TuxPaint catches SIGTERM and shows an "unsaved changes?" dialog instead of exiting, which defeats the point of an emergency recovery path since it can't depend on the stuck app cooperating with a graceful shutdown. `process_manager.kill_all()` (Phase 5) had the identical bug, fixed the same way. Activity file path overridable via `CLASSPAD_ACTIVITY_FILE` for testing, matching the `CLASSPAD_PLUGINS_DIR` pattern in `plugin-install.sh`. `tests/test_recovery.py`, 6 cases including one that specifically spawns a process that ignores SIGTERM (via `trap '' TERM`) to catch this exact class of bug in CI, not just on hardware.
- [x] Create `system/xbindkeys/xbindkeysrc` — binds `recovery.sh`. Bound to `Ctrl+Alt+Shift+Escape`, changed from the `RShift+RCtrl+F12`-style combo used for the Phase 2 gate: (1) X11's modifier state field has one bit for Shift and one for Control with no left/right distinction, so a single-keypress xbindkeys binding can never be right-side-exclusive regardless of syntax — confirmed empirically (`Shift_R + Control_R + F12` and a hand-built multikey `m:/c:` binding both silently collapsed to "any F12 press fires it," confirmed by `xbindkeys -s` and a live test); (2) mid-Phase-6, switched off F12 entirely to `Ctrl+Alt+Shift+Escape` since F12 needs the Fn key on a lot of laptop keyboards — exactly the kind of thing that trips up non-technical teaching staff in a real emergency. The final binding was verified empirically on real hardware: `xbindkeys -s` shows a real modifier mask (not `m:0x0`), a bare `Escape` press does not fire it, and the full combo does.
- [x] Create `system/systemd/classpad-launcher.service` — runs `launcher/main.py`, `Restart=always`, `RestartSec=2`, `Environment=DISPLAY=:0` (needed under `systemd --user` when the X session wasn't started by systemd itself — found while setting up the real hardware test below). `WorkingDirectory=/opt/classpad` matches the production deploy path (Phase 11); not yet installed there since the repo tree isn't copied to `/opt/classpad` until Phase 11 (same as the Chromium policy file in Phase 5).
- [x] Add xbindkeys to Openbox autostart (alongside bar)
- [x] Connect bar Home button to `recovery.sh` — `bar/bar.py`'s `_on_home_clicked` now runs `bash <repo>/scripts/recovery.sh` (path resolved relative to `bar.py`'s own location, so it works both in a dev checkout and once the whole tree is deployed to `/opt/classpad`, matching how `launcher/main.py` resolves its own asset paths). No extra "return to home" signal needed — killing the tracked child is enough, since `process_manager.wait_for_exit()`'s blocking `wait()` unblocks the moment the process dies and handles its own cleanup from there (Phase 5).
- [x] **GATE — RUN, real hardware, 2026-07-30:** recovery via hotkey — launched TuxPaint through the real `bar.bar` + `launcher.main` + `xbindkeys` processes (`xbindkeys` pointed at the real `scripts/recovery.sh`, not a placeholder), fired `Ctrl+Alt+Shift+Escape` via `xdotool`, confirmed TuxPaint died immediately (no save dialog — see the SIGKILL fix above, which this same test caught) and the launcher redrew the home screen with `/tmp/classpad_activity` cleared.
- [x] **GATE — RUN, real hardware, 2026-07-30:** recovery under the Phase 2 worst case — launched `tuxmath` (genuinely `_NET_WM_STATE_FULLSCREEN`, covering the bar, matching the Phase 2 finding), fired the same real-hotkey-to-real-`recovery.sh` path, confirmed it still fires and kills the fullscreen app instantly. Also incidentally found `tuxtype` is *also* genuinely fullscreen (not previously called out alongside GCompris-qt/TuxMath) while testing the bar's Home button against it — same accepted limitation, not a new gap, since the hotkey (not Home) is the documented safety net for this case.
- [x] Also verified the bar's Home button end to end against the case it's actually meant for (bar visibly reachable, i.e. a non-fullscreen child): launched `website-placeholder`, clicked Home, confirmed Chromium was killed and the launcher returned.
- [x] **GATE — RUN, real hardware, 2026-07-30:** systemd restart — installed a test copy of the unit as a `systemd --user` service (`WorkingDirectory` pointed at the dev checkout path; `/opt/classpad` doesn't exist until Phase 11, same handling as the Chromium policy file in Phase 5), started the real launcher through it, confirmed it renders on screen, then `kill -9`'d the tracked PID directly. `systemctl --user status` showed the service active under a new PID within the configured `RestartSec=2`, and the launcher was back on screen. Test unit removed after the run.

---

## Phase 7: Central Server (Flask)

**Handed off to a separate host (2026-07-30):** Phases 7-10 (server +
client-polling) are developed on a Windows machine via WSL2 + Docker, not on
the 11e — see CLAUDE.md "Development Environment." Client-side work (Phases
1-6, done) stays on the 11e. Both sides work against the same GitHub repo
(`origin`); push/pull is the handoff, so there's no other state to transfer
between hosts. Deployment target: Ubuntu Server on Hyper-V (x86_64, matches
WSL2 — no multi-arch build needed).

- [ ] Create `server/app.py` — Flask app factory, register blueprints
- [ ] Create `server/models.py` — SQLite models: Machine, Plugin, Assignment, Config
- [ ] Implement `GET /config/<machine_id>` — returns JSON: active plugins for this machine, layout order, any pending commands (e.g. `force_home: true`)
- [ ] Implement `POST /telemetry/<machine_id>` — accepts: last_seen timestamp, current_activity, any errors
- [ ] Implement `GET /plugins/` — returns plugin catalogue
- [ ] Implement `GET /plugins/<id>/download` — serves plugin zip file
- [ ] Create `server/Dockerfile` and `server/docker-compose.yml` — mounts SQLite as a volume
- [ ] Test all endpoints with curl before building the admin portal

---

## Phase 8: Admin Portal

- [ ] Create basic admin portal at `/admin` (single account, credentials in environment variable)
- [ ] Machine list view: shows all machines, last seen time, current activity
- [ ] Per-machine view: shows assigned plugins, button layout order, "Return to Home" button
- [ ] Plugin catalogue view: list all available plugins, upload new plugin zip
- [ ] Assignment UI: drag-and-drop or checkbox to assign plugins to a machine or group
- [ ] "Return to Home" action: sets `force_home: true` in config response for that machine, clears after client acknowledges
- [ ] Keep the portal simple — this is an internal tool, not a consumer product

---

## Phase 9: Server Polling on Client

- [ ] Update `launcher/config.py` to poll `GET /config/<machine_id>` on startup and every 30 seconds
- [ ] Machine ID = hostname (from `socket.gethostname()`)
- [ ] On config change: update button grid without requiring a restart if possible, or restart launcher gracefully
- [ ] Handle `force_home: true` in config response: call `recovery.sh`, then POST acknowledgement back to server
- [ ] On server unreachable: log warning, continue with last cached config (write to `/opt/classpad/config_cache.json`)
- [ ] Send telemetry POST on startup, on activity change, and every 5 minutes

---

## Phase 10: Plugin Deployment from Server

- [ ] Create a background daemon or cron job on the client that polls `GET /plugins/` periodically
- [ ] Compares installed plugin versions against catalogue
- [ ] Downloads and installs any new or updated plugins via `plugin-install.sh`
- [ ] Updates the launcher button grid after new plugins are installed
- [ ] Test: upload a new plugin zip to the server, verify it appears on the client within the poll interval

---

## Phase 11: LightDM Auto-Login & Openbox Integration

- [ ] Create `system/lightdm/lightdm.conf` — auto-login for the kiosk user, no password
- [ ] Create `system/openbox/autostart` — starts bar.py, enables xbindkeys, starts classpad-launcher systemd service
- [ ] Create `scripts/install.sh` — installs all apt dependencies (including `alsa-utils` — see CLAUDE.md, found missing from the required-packages list on real hardware), copies system files to correct locations, enables systemd service, sets hostname from serial number, sets up PEAP/TTLS WiFi config, unmutes and sets a default volume (`amixer sset Master 70% unmute`) — audio boots muted at 0% on this hardware with no visible error, found and fixed manually on the dev machine 2026-07-30, see CLAUDE.md "Development Environment"
- [ ] Test full boot-to-launcher flow: power on -> auto-login -> bar appears -> launcher appears
- [ ] Test reboot recovery: reboot machine, verify everything comes back correctly

---

## Phase 12: Custom Apps — Word Processor

- [ ] Create `plugins/wordprocessor/app/` — simple fullscreen HTML5 app (served locally via a tiny Python HTTP server bundled in the plugin)
- [ ] Large font, minimal toolbar (bold, font size)
- [ ] "Send to Teacher" button: POSTs content to central server, server emails via SMTP relay to configured teacher address
- [ ] Name picker on open: child selects their name from a large-button list (names configured per-machine in admin portal)
- [ ] Child name included in email subject line
- [ ] "Clear" button to start fresh
- [ ] Test email delivery via school SMTP relay

---

## Phase 13: Custom Apps — Memory Card Game

- [ ] Create `plugins/memory-game/app/` — HTML5 card matching game
- [ ] Card sets defined as JSON (animal set, colour set, letter set, number set)
- [ ] Admin portal allows selecting active card set per machine
- [ ] Card sets downloadable from server as part of plugin update mechanism
- [ ] Celebratory animation/sound on match and on completion

---

## Phase 14: Custom Apps — Xylophone

- [ ] Create `plugins/xylophone/app/` — HTML5 or Pygame app
- [ ] 8 large coloured keys (C major scale)
- [ ] Each key plays the correct note on click/tap
- [ ] Keys sized appropriately for small hands

---

## Phase 15: Custom Apps — Storybook Viewer

- [ ] Create `plugins/storybook/` — fetches story list from central server
- [ ] Stories stored on server as JSON (pages with image URL + text)
- [ ] Large text, illustrated panels, forward/back arrows only
- [ ] Text-to-speech: reads page text aloud on load using espeak or festival
- [ ] Admin portal: upload new stories, assign to machines

---

## Phase 16: Imaging & Deployment

- [ ] Document the full base image build process in `scripts/image-setup.sh`
- [ ] Test imaging one machine from scratch using Clonezilla or `dd`
- [ ] Verify: new machine boots, sets its own hostname from serial number, connects to WiFi, registers with server, downloads assigned plugins
- [ ] Document the teacher onboarding process (add machine to server, assign plugins)

---

## Deferred / Needs Scope Decision (not yet assigned a phase)

These are named in `CLAUDE.md` or `pre-build-decisions.md` but have no build phase above. Don't start building them until scoped as v1 or explicitly deferred to v2.

- [ ] `emotion-checkin` custom app — listed in CLAUDE.md's directory structure but has no phase here
- [ ] GCompris activity whitelist, Pysycache, KTuberling, Blinken, Childsplay — evaluate vs. tuxpaint/tuxtype/tuxmath, confirm v1 inclusion with teaching staff
- [ ] Mouse dexterity mini-games (if not already covered by the above)
- [ ] Which teacher receives "Send to Teacher" emails — per-machine config, selectable on screen, or fixed? Not addressed by Phase 12 as written (only the *child's* name is picked)
- [ ] Word processor email format — plain text, PDF, or screenshot
- [ ] Retention policy for child-generated content (TuxPaint artwork, word processor text, emails) — wipe on reboot, sync to server, or keep indefinitely? Needs a privacy/safeguarding decision before Phase 12 ships, since it handles a child's name and free text
