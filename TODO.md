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

- [ ] Create `launcher/process_manager.py`:
  - `launch(plugin)` — spawns the correct subprocess based on plugin type
  - `wait_for_exit()` — monitors the process and signals when it exits
  - `kill_all()` — kills all tracked child processes by name (for recovery)
- [ ] `launch()` must invoke `launch_command` as an argv list with `shell=False` — never build a shell string. It comes from a server-controlled manifest; treat it as untrusted input.
- [ ] For `type: app` — launch binary from `launch_command` in manifest
- [ ] For `type: website` — launch Chromium with correct flags (see CLAUDE.md — use `--app`, NOT `--kiosk`)
- [x] Create `system/chromium/policies/managed/classpad-policy.json` with `FullscreenAllowed: false` — **verified on real hardware (2026-07-30)**: F11 inside `--app` mode was escaping to real fullscreen and hiding the bar; this policy blocks it, confirmed F11 is now a no-op. Also confirmed `--app --start-maximized` alone (no `--kiosk`) already respects the strut correctly — window fills exactly the area below the bar, bar stays stacked on top.
- [ ] Add `URLAllowlist` to the same policy file once the curated site list is decided — still open, `--app --incognito` alone does not stop in-page navigation off a curated site to uncontrolled content. Don't add a guessed domain list.
- [ ] Install the policy file to `/etc/chromium/policies/managed/` as part of `scripts/install.sh` (Phase 11) — done manually on the dev machine for this test, not yet automated
- [ ] When a child process exits, update `/tmp/classpad_activity` to empty and return launcher to home screen
- [ ] Write current activity name to `/tmp/classpad_activity` on launch (bar reads this)
- [ ] Test full cycle: click button -> app launches -> app closes -> launcher returns
- [ ] Test bar remains visible throughout the full cycle

---

## Phase 6: Staff Recovery System

- [ ] Create `scripts/recovery.sh` — kills all processes in the kill list (chromium, tuxpaint, tuxtype, tuxmath, gcompris), clears `/tmp/classpad_activity`
- [ ] Create `system/xbindkeys/xbindkeysrc` — binds `RShift+RCtrl+F12` to `recovery.sh`
- [ ] Create `system/systemd/classpad-launcher.service` — runs launcher/main.py, `Restart=always`, `RestartSec=2`
- [ ] Add xbindkeys to Openbox autostart (alongside bar)
- [ ] Connect bar Home button to `recovery.sh`
- [ ] Test recovery: launch TuxPaint, hit key combo, verify TuxPaint dies and launcher returns
- [ ] Test recovery under the Phase 2 worst case too: launch gcompris/tuxmath fullscreen, hit key combo, verify it still fires with the real `recovery.sh` wired up (not just the Phase 2 placeholder binding)
- [ ] Test systemd restart: kill launcher process manually, verify it restarts within 2 seconds

---

## Phase 7: Central Server (Flask)

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
- [ ] Create `scripts/install.sh` — installs all apt dependencies, copies system files to correct locations, enables systemd service, sets hostname from serial number, sets up PEAP/TTLS WiFi config
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
