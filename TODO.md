# Classpad — Build TODO

Work through these phases in order. Each phase should be functional and testable before moving to the next.

---

## Phase 1: Repo & Project Scaffold

- [ ] Create directory structure as defined in CLAUDE.md
- [ ] Create placeholder `__init__.py` and stub files for each module
- [ ] Create `requirements.txt` for launcher and bar dependencies
- [ ] Create `server/requirements.txt` for Flask server
- [ ] Create `.gitignore` (Python, venv, SQLite files, `__pycache__`)
- [ ] Verify the project structure matches CLAUDE.md before proceeding

---

## Phase 2: PyGTK Persistent Bar

Build this first — it needs to be running before the launcher so you can verify strut behaviour.

- [ ] Create `bar/bar.py` — GTK window, fixed height 55px, full screen width, positioned at top
- [ ] Set `_NET_WM_STATE_ABOVE` (always-on-top)
- [ ] Set `_NET_WM_STRUT_PARTIAL` to reserve 55px at top of screen so other windows do not overlap it
- [ ] Add Home button (left side) — placeholder action: print to stdout for now
- [ ] Add live clock (centre) — updates every second
- [ ] Add "Call Teacher" button (right side) — placeholder for now
- [ ] Add current activity label (between Home and clock) — reads from a shared state file `/tmp/classpad_activity`
- [ ] Test: open TuxPaint or any maximised window — bar must remain visible above it
- [ ] Test: use `xprop` on the bar window to verify strut and state hints are set correctly
- [ ] Make bar auto-start via Openbox `autostart` file

---

## Phase 3: Plugin System (local)

- [ ] Define and document the `manifest.json` schema (see CLAUDE.md) — must be finalised before building anything that reads it
- [ ] Create `launcher/plugin_manager.py`:
  - Scans `/opt/classpad/plugins/` for installed plugin directories
  - Reads and validates each `manifest.json`
  - Returns a list of plugin objects sorted by name
- [ ] Create example manifests for: tuxpaint, tuxtype, tuxmath, a placeholder website button
- [ ] Write unit tests for plugin_manager — valid manifest, missing fields, malformed JSON
- [ ] Create `scripts/plugin-install.sh` — accepts a plugin zip path, extracts to `/opt/classpad/plugins/<id>/`, runs `install.sh` if present

---

## Phase 4: Pygame Launcher

- [ ] Create `launcher/main.py` — Pygame init, fullscreen window positioned below the bar (y-offset = bar height, height = screen height minus bar height)
- [ ] Create `launcher/button.py` — Button class: renders icon + label, handles hover and click states, plays a click sound on press
- [ ] Create `launcher/config.py` — loads plugin list from plugin_manager, builds button grid layout
- [ ] Render button grid from installed plugins — evenly spaced, large icons (minimum 96px), readable labels
- [ ] Handle different numbers of plugins gracefully (1 button up to ~12)
- [ ] Add a background colour/image appropriate for young children
- [ ] Test: launcher fills the screen below the bar exactly, no overlap
- [ ] Add audio feedback on button click (short positive sound)

---

## Phase 5: Process Manager & App Launching

- [ ] Create `launcher/process_manager.py`:
  - `launch(plugin)` — spawns the correct subprocess based on plugin type
  - `wait_for_exit()` — monitors the process and signals when it exits
  - `kill_all()` — kills all tracked child processes by name (for recovery)
- [ ] For `type: app` — launch binary from `launch_command` in manifest
- [ ] For `type: website` — launch Chromium with correct flags (see CLAUDE.md — use `--app`, NOT `--kiosk`)
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
