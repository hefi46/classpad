# Classpad — Claude Code Project Context

## What This Is

Classpad is a lightweight Linux kiosk launcher designed for 5-6 year old children in a primary school classroom. It runs on older Lenovo ThinkPad 11e hardware (3rd/5th gen, Celeron N-series, 4GB RAM) running Debian 13 (Trixie) with Openbox on X11.

Children auto-login to a fullscreen launcher with large colourful buttons. Each button launches an educational app, a website in kiosk mode, or a custom mini-app. There is no desktop, no file manager, no taskbar — just the launcher and a persistent bar.

A central server (Docker, local network) manages app configuration and deployment. Teachers access a web admin portal to update which apps appear on which machines.

---

## Architecture

### Persistent Bar (always visible)
- Built with **PyGTK** (python3-gi / GObject Introspection)
- Runs as a separate process, started before the launcher
- Uses EWMH strut hints (`_NET_WM_STRUT_PARTIAL`) to reserve screen space at the top
- Set `_NET_WM_STATE_ABOVE` (always-on-top)
- Height: ~55px at top of screen
- Contains: Home button, clock, "Call Teacher" button, current activity label
- Clicking Home kills all child processes and signals the launcher to return to the home screen
- All other apps (Pygame launcher, Chromium, TuxPaint etc.) run in the remaining screen area below the bar

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
- For `type: app`: launches the binary directly
- For `type: custom`: launches a local Python/HTML app from the plugin directory

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

### Staff Recovery
Two-layer recovery system:
1. **xbindkeys** daemon running at X session level — listens for `RShift+RCtrl+F12` regardless of which app is in front. When triggered, kills all known child processes by name and the systemd service auto-restarts the launcher.
2. **Admin portal** "Return to Home" button per machine — sets a flag in config response, machine detects it on next poll and clears all child processes.

Process kill list: `chromium`, `tuxpaint`, `tuxtype`, `tuxmath`, `gcompris`, plus any processes launched from plugin install.sh scripts.

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

---

## Key Design Decisions (Do Not Re-debate)

- **Pygame for the launcher, not a browser** — avoids browser artifacts, crash dialogs, and address bar exposure to children
- **PyGTK bar with EWMH struts** — persistent, always-on-top, works across all apps. The bar stays visible even when apps are running.
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
├── launcher/                  # Pygame home screen
│   ├── main.py
│   ├── config.py              # Config loading and server polling
│   ├── button.py              # Button rendering
│   ├── plugin_manager.py      # Reads installed plugins, builds button grid
│   ├── process_manager.py     # Subprocess launching and monitoring
│   └── assets/
│       └── fonts/
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
│   ├── gcompris/
│   ├── wordprocessor/         # Custom app
│   │   ├── manifest.json
│   │   ├── icon.png
│   │   └── app/               # Flask or HTML5 app
│   ├── memory-game/           # Custom app
│   ├── xylophone/             # Custom app
│   ├── storybook/             # Custom app (served from server)
│   └── emotion-checkin/       # Custom app
├── system/                    # OS configuration files
│   ├── lightdm/
│   │   └── lightdm.conf
│   ├── openbox/
│   │   └── autostart          # Starts bar.py then launcher service
│   ├── systemd/
│   │   └── classpad-launcher.service
│   └── xbindkeys/
│       └── xbindkeysrc        # RShift+RCtrl+F12 recovery combo
└── scripts/
    ├── install.sh             # First-time machine setup
    ├── plugin-install.sh      # Install/update a plugin from server
    └── recovery.sh            # Called by xbindkeys to kill child processes
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
