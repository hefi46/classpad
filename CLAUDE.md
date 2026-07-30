# Classpad — Claude Code Project Context

## What This Is

Classpad is a lightweight Linux kiosk launcher designed for 5-6 year old children in a primary school classroom. It runs on older Lenovo ThinkPad 11e hardware (3rd/5th gen, Celeron N-series, 4GB RAM) running Debian 13 (Trixie) with Openbox on X11.

Children auto-login to a fullscreen launcher with large colourful buttons. Each button launches an educational app, a website in kiosk mode, or a custom mini-app. There is no desktop, no file manager, no taskbar — just the launcher and a persistent bar.

A central server (Docker, local network) manages app configuration and deployment. Teachers access a web admin portal to update which apps appear on which machines.

---

## Architecture

### Persistent Bar
- Built with **PyGTK** (python3-gi / GObject Introspection)
- Runs as a separate process, started before the launcher
- Uses EWMH strut hints (`_NET_WM_STRUT_PARTIAL`) to reserve screen space at the top, and `_NET_WM_STATE_ABOVE` to stay on top
- Height: 55px at top of screen
- Contains: Home button, current activity label, volume control, clock
- Volume control is not optional — classroom audio complaints are the most common real-world support request for this kind of deployment. No "Call Teacher" button — cut from scope; the teacher is physically present in the classroom.
- Clicking Home kills all child processes and signals the launcher to return to the home screen
- All other apps (Pygame launcher, Chromium, TuxPaint etc.) run in the remaining screen area below the bar
- **Verified on real 11e hardware (2026-07-30):** struts/above-state reliably keep the bar visible over the launcher and any non-fullscreen window (`_NET_WORKAREA` correctly reserves the top 55px). They do **not** survive a genuinely fullscreen child — GCompris-qt and TuxMath both set `_NET_WM_STATE_FULLSCREEN` and Openbox stacks them above the bar, covering it entirely. This is a known, accepted limitation, not a bug to fix: see "Recovery model" below for why it's still safe.
- Hardware volume keys were investigated as a bar-independent volume path (relevant since volume is unreachable while the bar is covered) but don't currently generate any input event on this hardware at the kernel level, despite `thinkpad_acpi` advertising `KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` as capabilities. Not pursued further — the on-screen slider is the only volume control, and it's unavailable during fullscreen apps.

### Pygame Launcher (home screen)
- **Pygame** fullscreen window occupying the non-bar area
- Reads installed plugins from `/opt/classpad/plugins/` and renders a button grid
- Buttons have: large icon, label, and a type (`app`, `website`, or `custom`)
- On button click: launches the target as a subprocess, then waits for it to exit
- Polls the central server for config updates on a configurable interval
- Falls back to locally cached config if server is unreachable
- Runs as a systemd service with `Restart=always`

### Plugin System
Each app is a plugin — a zip archive containing:
```
my-plugin/
  manifest.json      # metadata, launch command, icon path, version
  icon.png           # 256x256 icon
  [app files]        # optional: scripts, HTML, assets
  install.sh         # optional: run once on install
```

`manifest.json` schema:
```json
{
  "id": "tuxpaint",
  "name": "TuxPaint",
  "version": "1.0.0",
  "type": "app",
  "launch_command": "/usr/bin/tuxpaint",
  "icon": "icon.png",
  "description": "Drawing and painting app"
}
```

**Fields (all types):**

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Lowercase, matches `^[a-z0-9][a-z0-9-]*$`. Used as the install directory name and in the process kill list. |
| `name` | yes | Display name shown on the button. |
| `version` | yes | `major.minor.patch`, e.g. `1.0.0`. |
| `type` | yes | One of `app`, `website`, `custom`. |
| `icon` | yes | Path to a 256x256 PNG, relative to the plugin directory. |
| `description` | yes | Short human-readable description. |

**Type-specific fields:**

| Field | Required for | Notes |
|---|---|---|
| `launch_command` | `app`, `custom` | Shell-like string, e.g. `"/usr/bin/tuxpaint --fullscreen=yes"`. Split with `shlex.split()` and passed as an argv list with `shell=False` — no shell is ever invoked, so this is safe to author with normal-looking flags. |
| `url` | `website` | Target URL. |
| `chromium_flags` | `website` (optional) | Shell-like string of extra flags appended to the base Chromium flag set, e.g. `"--force-device-scale-factor=1.25"`. Same `shlex.split()` rule as `launch_command`. |

**Security:** plugin zips are trusted-admin input, not arbitrary uploads — `install.sh` runs with the same privileges as the install process (expected: root), so anyone who can push a plugin owns every machine. `plugin-install.sh` must reject archive entries containing `..` or absolute paths before extraction (path traversal), regardless of whether extraction uses `unzip` or Python's `zipfile`. It must also validate the manifest's `id` field against the `^[a-z0-9][a-z0-9-]*$` pattern *before* using it to build any filesystem path (e.g. the install target or an `rm -rf` target) — a malicious or malformed `id` (`../../etc`, empty string, `a/b`) is a second, independent path-traversal vector alongside archive entry names. Plugin upload is admin-portal-only (see Central Server / Admin Portal below) — there is no public or unauthenticated upload path.

For website buttons:
```json
{
  "id": "phonics-site",
  "name": "Phonics Game",
  "version": "1.0.0",
  "type": "website",
  "url": "https://example.com/phonics",
  "icon": "icon.png",
  "chromium_flags": "--force-device-scale-factor=1.25"
}
```

### App Launching
- All apps launched via `subprocess.Popen`
- The launcher tracks the process and detects when it exits
- For `type: website`: launches Chromium with `--app=URL --start-maximized --no-first-run --disable-session-crashed-bubble --disable-features=TranslateUI --overscroll-history-navigation=0 --disable-pinch --incognito`
- Do NOT use `--kiosk` for websites — it overrides EWMH struts and covers the bar
- **Verified on real hardware (2026-07-30):** `--app --start-maximized` (no `--kiosk`) correctly respects the strut — Chromium comes up `MAXIMIZED_VERT`/`MAXIMIZED_HORZ`, filling exactly the area below the bar, with the bar stacked on top. Chromium is the one app type that genuinely needs the bar: unlike TuxPaint/GCompris/TuxMath, a curated website has no built-in "back to menu" affordance, so Home has to come from the bar, not the page.
- **F11 fullscreen-escape — found and fixed.** A child pressing F11 inside `--app` mode does escape to real `_NET_WM_STATE_FULLSCREEN` (confirmed on real hardware) and hides the bar — visually subtle since `--app` already hides the address bar/tabs, so nothing else looks different. Fixed with the Chromium managed policy `FullscreenAllowed: false` in `system/chromium/policies/managed/classpad-policy.json` — verified on real hardware that F11 then does nothing (state stays `MAXIMIZED_VERT`/`MAXIMIZED_HORZ`, bar stays visible).
- Website navigation containment (in-page links off the curated site) is still open — the same managed-policy file needs a `URLAllowlist` once the curated site list is decided (see `pre-build-decisions.md` §3). Don't invent a placeholder domain list; a wrong allowlist is worse than none because it looks done.
- For `type: app`: launches the binary directly. Apps with their own reachable exit affordance (TuxPaint, GCompris-qt, TuxMath all confirmed to have one — Escape and/or an on-screen close control) may run in native fullscreen mode; the recovery hotkey and the app's own exit cover getting back to the launcher, and the bar is allowed to be covered while they run (see Persistent Bar above).
- For `type: custom`: launches a local Python/HTML app from the plugin directory
- **Security:** `launch_command` (and any per-plugin flags) comes from a server-controlled manifest. `process_manager.py` must invoke it as an argv list with `shell=False` — never interpolate it into a shell string. Treat it as untrusted input from the same trust boundary as the plugin zip itself.

### Central Server
- **Docker container** on local school network server
- **Python Flask** web app
- **SQLite** database (mounted as Docker volume for persistence)
- Exposes:
  - `GET /config/<machine_id>` — returns JSON config for a machine (active plugins, layout)
  - `GET /plugins/` — plugin catalogue
  - `GET /plugins/<id>/download` — download a plugin zip
  - `POST /telemetry/<machine_id>` — receives last-seen, current activity, errors
  - Web admin portal at `/admin`

### Staff Recovery / Recovery Model
Two-layer recovery system:
1. **xbindkeys** daemon running at X session level — listens for `Ctrl+Alt+Shift+Escape` regardless of which app is in front. When triggered, kills all known child processes by name and the systemd service auto-restarts the launcher.
2. **Admin portal** "Return to Home" button per machine — sets a flag in config response, machine detects it on next poll and clears all child processes.

This hotkey is the actual safety net for a child stuck in a fullscreen app (the bar itself is covered in that case — see Persistent Bar above). **Verified on real 11e hardware:** the Phase 2 gate (2026-07-30) confirmed the underlying mechanism — a generic modifier+key xbindkeys binding (tested as `Ctrl+Shift+F12`), both via synthetic (`xdotool key`) and a real physical keypress, fires correctly through GCompris-qt and TuxMath while each is genuinely fullscreen and focused, since neither establishes a blocking active keyboard grab. The final combo was changed in Phase 6 (2026-07-30) from an F-key to `Ctrl+Alt+Shift+Escape` — F12 requires the Fn key on a lot of laptop keyboards, which is exactly the kind of thing that trips up non-technical teaching staff in an actual emergency — and re-verified on real hardware against TuxMath fullscreen with the real `recovery.sh` wired up. Both TuxPaint/TuxMath-style apps also have a clean exit path reachable by the target age group (Escape repeatedly, or an on-screen X/close affordance), so the hotkey is an emergency fallback, not the routine way out.

Note `recovery.sh` sends `SIGKILL` (`pkill -9`), not the default `SIGTERM` — confirmed on real hardware that TuxPaint catches `SIGTERM` and shows an "unsaved changes?" save dialog instead of exiting. An emergency recovery path can't depend on the stuck app cooperating with a graceful shutdown.

Process kill list: `chromium`, `tuxpaint`, `tuxtype`, `tuxmath`, `gcompris-qt`, plus any processes launched from plugin install.sh scripts. (Corrected from `gcompris` — Debian trixie only ships the Qt/QML rewrite, `gcompris-qt`; the classic GTK `gcompris` package no longer exists.) Each plugin manifest that spawns a long-running process outside `launch_command` itself (e.g. a bundled local server) must declare its process name explicitly — the kill list cannot infer names for arbitrary future plugins.

---

## Open Risks — Verify Before Building Further

Gated verification steps live in `TODO.md` Phase 2. Two of the three original risks here have been verified on real hardware and are resolved below; the rest are still open.

- **Struts vs. real fullscreen — RESOLVED, design updated.** Confirmed on real hardware: GCompris-qt and TuxMath both go genuinely fullscreen (`_NET_WM_STATE_FULLSCREEN`) and Openbox stacks them above the bar, covering it — struts/above-state do not survive this. Rather than forcing these apps out of fullscreen (which would fight their own layout), the design now accepts this: the bar is guaranteed on the home screen and over non-fullscreen windows only, and the recovery hotkey (next item) covers the fullscreen case.
- **Recovery hotkey vs. keyboard grabs — RESOLVED, verified working.** Confirmed on real hardware with both a synthetic and a real physical keypress (Phase 2 gate, then re-verified in Phase 6 with the final `Ctrl+Alt+Shift+Escape` combo and the real `recovery.sh`): the combo reaches xbindkeys through GCompris-qt/TuxMath while each is fullscreen and focused. Neither app establishes a blocking active keyboard grab. This is what makes the fullscreen-bar limitation above acceptable rather than a blocker.
- **Chromium F11 fullscreen-escape — RESOLVED, verified working.** Confirmed on real hardware: F11 inside `--app` mode escaped to real fullscreen and hid the bar. Fixed with `FullscreenAllowed: false` in the Chromium managed policy (`system/chromium/policies/managed/classpad-policy.json`); re-verified on real hardware that F11 is now a no-op.
- **Website in-page navigation containment is still undecided.** `--app --incognito` plus Zscaler does not stop a child following a link off a curated site to uncontrolled content. The managed policy needs a `URLAllowlist` once the curated site list exists — not yet decided, see `pre-build-decisions.md` §3.
- **Plugin trust model.** Plugin zips can carry an `install.sh` that runs with elevated privileges via `plugin-install.sh`. Treat plugin upload as an admin-only, trusted-input operation (enforced in the admin portal, Phase 8) — there is no sandboxing of plugin code in this design.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| OS | Debian 13 (Trixie) |
| Window manager | Openbox (X11 only — no Wayland) |
| Display manager | LightDM (auto-login, no password) |
| Persistent bar | PyGTK (python3-gi, GObject Introspection) |
| Launcher | Pygame |
| Server | Python Flask in Docker |
| Database | SQLite |
| Browser (kiosk) | Chromium (`--app` mode, not `--kiosk`) |
| Service management | systemd |
| Key binding | xbindkeys |
| Email relay | Unauthenticated SMTP relay (inside school WAN) |
| Content filtering | Zscaler (school network, pre-existing) |
| Python version | 3.11+ |
| Audio | Plain ALSA (`amixer`) — confirmed no PulseAudio/PipeWire on the target image |

---

## Key Design Decisions (Do Not Re-debate)

- **Pygame for the launcher, not a browser** — avoids browser artifacts, crash dialogs, and address bar exposure to children
- **PyGTK bar with EWMH struts** — always-on-top on the home screen and over non-fullscreen apps. Does not survive genuinely fullscreen children (verified on real hardware); the recovery hotkey is the safety net for those, not the bar. See "Recovery model" under Staff Recovery.
- **No "Call Teacher" feature** — cut from scope. The teacher is physically present in the classroom; a dedicated hardware hotkey for this was considered and rejected since there's no way to reach it during a fullscreen app anyway (same limitation as the bar).
- **Chromium `--app` not `--kiosk`** — `--kiosk` overrides struts and hides the bar. `--app` mode removes address bar/tabs without going truly fullscreen, so the bar remains visible.
- **No login** — single shared OS user per machine, auto-login via LightDM
- **Plugin system** — apps are zip bundles with a manifest. The launcher is generic and does not hardcode any app names.
- **Local install, central deploy** — apps run locally for performance and offline resilience. The server manages which plugins are assigned to which machines.
- **SQLite** — sufficient for this deployment scale
- **Server is local network only** — never cloud-hosted. Student data stays on-premise.

---

## Directory Structure

```
classpad/
├── CLAUDE.md
├── TODO.md
├── conftest.py                 # Puts repo root on sys.path so `tests/` can `import launcher...`/`import server...`
├── launcher/                  # Pygame home screen
│   ├── main.py
│   ├── config.py              # Config loading and server polling
│   ├── button.py              # Button rendering
│   ├── plugin_manager.py      # Reads installed plugins, builds button grid
│   ├── process_manager.py     # Subprocess launching and monitoring
│   └── assets/
│       ├── fonts/
│       └── sounds/
│           └── click.wav      # Button click feedback, generated via stdlib `wave` (no bundled binary asset)
├── bar/                       # PyGTK persistent bar
│   ├── bar.py                 # Main bar window, EWMH struts, buttons
│   └── assets/
├── server/                    # Central admin server
│   ├── app.py                 # Flask app
│   ├── models.py              # SQLite models
│   ├── routes/
│   │   ├── config.py          # /config/<machine_id> endpoint
│   │   ├── plugins.py         # Plugin catalogue and download
│   │   └── admin.py           # Admin portal
│   ├── templates/             # Jinja2 HTML for admin portal
│   ├── static/
│   ├── Dockerfile
│   └── docker-compose.yml
├── plugins/                   # Plugin bundles
│   ├── tuxpaint/
│   │   ├── manifest.json
│   │   └── icon.png
│   ├── tuxtype/
│   │   ├── manifest.json
│   │   └── icon.png
│   ├── tuxmath/
│   ├── website-placeholder/   # Example website-type button — url is a placeholder, not a real curated site
│   │   ├── manifest.json
│   │   └── icon.png
│   ├── gcompris/
│   ├── wordprocessor/         # Custom app
│   │   ├── manifest.json
│   │   ├── icon.png
│   │   └── app/               # Flask or HTML5 app
│   ├── memory-game/           # Custom app
│   ├── xylophone/             # Custom app
│   ├── storybook/             # Custom app (served from server)
│   └── emotion-checkin/       # Custom app — v1/v2 scope not yet confirmed, no TODO phase assigned
├── system/                    # OS configuration files
│   ├── lightdm/
│   │   └── lightdm.conf
│   ├── openbox/
│   │   └── autostart          # Starts bar.py then launcher service
│   ├── systemd/
│   │   └── classpad-launcher.service
│   ├── xbindkeys/
│   │   └── xbindkeysrc        # Ctrl+Alt+Shift+Escape recovery combo
│   └── chromium/
│       └── policies/
│           └── managed/
│               └── classpad-policy.json   # URLAllowlist — website navigation containment
├── scripts/
│   ├── install.sh             # First-time machine setup
│   ├── plugin-install.sh      # Install/update a plugin from server
│   └── recovery.sh            # Called by xbindkeys to kill child processes
└── tests/                     # pytest unit tests (run via python3-pytest, apt-installed)
    ├── test_plugin_manager.py
    ├── test_plugin_install.py
    └── test_config.py         # Button grid layout math (no display needed)
```

---

## Development Environment

- **Dev machine**: Lenovo ThinkPad 11e running Debian 13 + Openbox (matches target hardware)
- **Editor**: VS Code via Remote SSH from Windows if preferred
- **Required packages** (install with `sudo apt install`):
  ```
  openbox obconf lightdm lightdm-gtk-greeter
  python3 python3-pip python3-pygame
  python3-gi python3-gi-cairo gir1.2-gtk-3.0
  chromium git curl xbindkeys x11-utils xdotool
  ```
- **X11 debugging tools**: `xprop` and `xwininfo` (in x11-utils) — use these to verify EWMH struts and window states

---

## Network & Deployment Context

- School network: DHCP, WPA2-Enterprise (PEAP/TTLS), Zscaler content filtering
- Machine hostnames: `11e-<serialnumber>` (from `dmidecode -s system-serial-number`)
- Central server: local network, Docker
- SMTP: unauthenticated relay inside WAN, accepts mail for @education.vic.gov.au and @edumail.vic.gov.au
- Target deployment: Lenovo ThinkPad 11e 3rd gen (20G9S05P00) and 5th gen (20LRS04R00)
