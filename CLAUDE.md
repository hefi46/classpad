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
- Hardware volume keys were investigated as a bar-independent volume path (relevant since volume is unreachable while the bar is covered) but don't currently generate any input event on this hardware at the kernel level, despite `thinkpad_acpi` advertising `KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` as capabilities. **Re-checked on real hardware (2026-07-31)** with `evtest` against `/dev/input/event8` ("ThinkPad Extra Buttons") across two separate real-time capture windows while physically pressing the volume keys: zero `EV_KEY` events of any kind, confirming this isn't a fluke — the capability is advertised but genuinely never fires from this firmware/kernel combination. Not pursued further — the on-screen slider is the only volume control, and it's unavailable during fullscreen apps.

### Pygame Launcher (home screen)
- **Pygame** fullscreen window occupying the non-bar area
- Reads installed plugins from `/opt/classpad/plugins/` and renders a button grid
- Buttons have: large icon, label, and a type (`app`, `website`, or `custom`)
- On button click: launches the target as a subprocess, then waits for it to exit
- Polls the central server for config updates on a configurable interval
- Falls back to locally cached config if server is unreachable
- Runs as a systemd service with `Restart=always`
- **Server polling (Phase 9), implemented 2026-07-31.** `launcher/config.py`'s `run_poller()` runs on its own daemon thread, started by `main.py` — required because the main thread blocks synchronously in `process_manager.wait_for_exit()` for however long a child app is open (potentially the whole school day), so polling/telemetry can't live in the render loop. The poller never touches pygame or constructs `Button` objects (SDL surface work is main-thread-only); it hands the main thread already-locally-installed `Plugin` objects via a `queue.Queue(maxsize=1)`, which the render loop drains non-blockingly each frame. Server base URL: `CLASSPAD_SERVER_URL` env var if set (test/dev override), else `/opt/classpad/server_url` (written once by `install.sh` from the same env var, mirroring the `machine_id` file pattern) — never a hardcoded address, matching the "Network & Deployment Context" requirement below. **The button grid is never blanked by a poll result** — an enabled-plugins list that matches nothing installed locally (a fresh/misconfigured server profile, or a stale cache) is deliberately left un-applied rather than clearing the current grid; on a 5-year-old's kiosk with a teacher who can't debug it, a stale-but-populated grid is far preferable to an empty screen. Verified on real hardware against a stub HTTP server (not the real Flask server, which wasn't reachable from this host in this session): config-change reordering with no restart, `force_home` killing the running child and returning to the launcher with a `force_home_ack` telemetry POST observed, cache fallback when the stub was killed, and the empty-reconciliation case leaving the grid untouched — including a fresh-process-start with the server down *and* a cache that reconciles to nothing, which still shows the full local plugin set rather than blanking (the initial grid build always comes from a direct local scan, never from cache).
- `process_manager.kill_all()` (called directly from the poller on `force_home`, not by shelling out to `scripts/recovery.sh`) and `recovery.sh` are only equivalent because their kill lists are kept in sync by hand — both files already carry a "must match" comment; a future plugin added to one and not the other silently reintroduces the same un-killable-process bug the Xylophone plugin hit (see "Process kill list" below).

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
- `launcher/main.py` launches on `pygame.MOUSEBUTTONUP` (release), not `MOUSEBUTTONDOWN` (press) — **found and fixed on real hardware (2026-07-31)** after a reboot surfaced a bug that every earlier synthetic-click (`xdotool`) test had missed: launching on press let the child window take over the screen while a real physical click was still mid-press, which desyncs Openbox's synchronous click-to-focus grab (`XGrabButton`, `GrabModeSync` expects the press and release to resolve against the same window) and freezes all pointer input system-wide (clicks and hover dead everywhere — launcher, bar, launched app — while keyboard kept working and nothing crashed) until the frozen process is killed. Synthetic clicks send press+release near-instantaneously and never hit this timing window, which is why the Phase 5 click-cycle gate passed despite the bug existing. See TODO.md Phase 5 for the full diagnosis.
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
  - `GET /config/<machine_id>` — returns JSON config for a machine (the shared plugin profile, layout order, `force_home`)
  - `GET /plugins/` — plugin catalogue
  - `GET /plugins/<id>/download` — download a plugin zip
  - `POST /telemetry/<machine_id>` — receives last-seen, current activity, errors, and the client's ack of a handled `force_home`
  - Web admin portal at `/admin`
- **One shared plugin profile, not per-machine assignment — decided 2026-07-31**, revisited while reviewing an admin-portal design mockup. Every machine's `/config` returns the identical enabled/ordered plugin list; there is no per-machine plugin layout and no plan to add one. This matches the actual use case (one classroom, one shared app set) and keeps the admin portal to a single "edit the profile" screen instead of a per-machine assignment UI. `force_home` stays per-machine — it's a real command aimed at one box, not part of the profile.
- **Machines have an admin-set friendly `display_name`, separate from their hostname.** The hostname (`11e-<serialnumber>`, see Network & Deployment Context) stays the machine's real identity server-side — free, unique, needs no coordination at image time — but isn't something a teacher should have to read off a screen. `display_name` (e.g. `blue-3`) is optional, admin-editable in the portal, shown in place of the hostname there, and meant to match a physical sticker on the machine. The client never sees or sends its own `display_name`.

### Staff Recovery / Recovery Model
Two-layer recovery system:
1. **xbindkeys** daemon running at X session level — listens for `Ctrl+Alt+Shift+Escape` regardless of which app is in front. When triggered, kills all known child processes by name and the systemd service auto-restarts the launcher.
2. **Admin portal** "Return to Home" button per machine — sets a flag in config response, machine detects it on next poll and clears all child processes.

This hotkey is the actual safety net for a child stuck in a fullscreen app (the bar itself is covered in that case — see Persistent Bar above). **Verified on real 11e hardware:** the Phase 2 gate (2026-07-30) confirmed the underlying mechanism — a generic modifier+key xbindkeys binding (tested as `Ctrl+Shift+F12`), both via synthetic (`xdotool key`) and a real physical keypress, fires correctly through GCompris-qt and TuxMath while each is genuinely fullscreen and focused, since neither establishes a blocking active keyboard grab. The final combo was changed in Phase 6 (2026-07-30) from an F-key to `Ctrl+Alt+Shift+Escape` — F12 requires the Fn key on a lot of laptop keyboards, which is exactly the kind of thing that trips up non-technical teaching staff in an actual emergency — and re-verified on real hardware against TuxMath fullscreen with the real `recovery.sh` wired up. Both TuxPaint/TuxMath-style apps also have a clean exit path reachable by the target age group (Escape repeatedly, or an on-screen X/close affordance), so the hotkey is an emergency fallback, not the routine way out.

Note `recovery.sh` sends `SIGKILL` (`pkill -9`), not the default `SIGTERM` — confirmed on real hardware that TuxPaint catches `SIGTERM` and shows an "unsaved changes?" save dialog instead of exiting. An emergency recovery path can't depend on the stuck app cooperating with a graceful shutdown.

Process kill list: `chromium`, `tuxpaint`, `tuxtype`, `tuxmath`, `gcompris-qt`, `xylophone`, plus any processes launched from plugin install.sh scripts. (Corrected from `gcompris` — Debian trixie only ships the Qt/QML rewrite, `gcompris-qt`; the classic GTK `gcompris` package no longer exists.) Each plugin manifest that spawns a long-running process outside `launch_command` itself (e.g. a bundled local server) must declare its process name explicitly — the kill list cannot infer names for arbitrary future plugins. **Found on real hardware (2026-07-31), building the Xylophone plugin (Phase 14):** any `custom`-type plugin invoked as `python3 <script>` shows up in `ps`/`pkill` as `python3` — indistinguishable from the launcher's and bar's own processes (both are also literally `python3`), so it can't just be added to the kill list by its interpreter name without also matching (and killing) the launcher/bar. `plugins/xylophone/app/xylophone.py` renames itself via `ctypes`/`prctl(PR_SET_NAME)` at startup so it gets its own kill-list entry (`xylophone`) instead. Any future Python-based `custom` plugin needs the same treatment.

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
- **Local install, central deploy** — apps run locally for performance and offline resilience. The server manages one shared plugin profile that every machine receives — not per-machine assignment (see "Central Server" above).
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
    ├── test_process_manager.py
    ├── test_recovery.py
    ├── test_config.py         # Button grid layout math + Phase 9 server polling
    └── server/                # Flask-dependent tests, isolated from the client suite
        ├── conftest.py         # `flask = pytest.importorskip("flask")` at the top —
        │                       # this client host (11e) doesn't have Flask installed
        │                       # by design (server deps live on the WSL2 host); this
        │                       # guard must live in tests/server/conftest.py
        │                       # specifically, not the top-level tests/conftest.py —
        │                       # importorskip in a directory that also collects
        │                       # non-Flask tests turns a graceful skip into a hard
        │                       # collection error for the whole run (verified 2026-07-31)
        ├── test_server_admin.py
        ├── test_server_config.py
        └── test_server_plugins.py
```

---

## Development Environment

Client (launcher/bar, Phases 1-6) and server (Phases 7-10) are developed on
different hosts — decided 2026-07-30, since the server doesn't need or
benefit from running on the 11e hardware, and the Windows machine is
significantly more capable for it. Both work against this same git repo
(`origin` on GitHub); `git push`/`pull` is the handoff between them, so keep
CLAUDE.md/TODO.md up to date and pushed before switching machines — a fresh
Claude Code session on either host reads its project context from these
files plus `git log`, not from any state carried over from the other host.

**Client dev machine**: Lenovo ThinkPad 11e running Debian 13 + Openbox
(matches target hardware)
- **Editor**: VS Code via Remote SSH from Windows if preferred
- **Required packages** (install with `sudo apt install`):
  ```
  openbox obconf lightdm lightdm-gtk-greeter
  python3 python3-pip python3-pygame
  python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-xlib
  chromium git curl rsync xbindkeys x11-utils xdotool
  alsa-utils network-manager
  ```
  `alsa-utils` (provides `amixer`, used by the bar's volume slider) was
  missing from this list until found on real hardware — the ALSA kernel
  stack was present and correctly loaded, but with no `amixer` binary
  installed, every volume-slider drag silently failed (`Popen` on a
  nonexistent binary). Worth checking for on any fresh image; there's no
  visible symptom of this beyond "the slider doesn't seem to do anything."
  `python3-xlib` (imported as `Xlib` by `bar/bar.py` for the strut hints)
  and `rsync` (used by `scripts/install.sh` to deploy the repo to
  `/opt/classpad` — not present on this image by default, unlike the rest
  of this list) were two more such gaps, both found while actually running
  `scripts/install.sh` (Phase 11) on real hardware — same failure shape as
  `alsa-utils`, silent/absent until something tries to use them.
  `network-manager` is present by default on this image but listed
  explicitly since Phase 11's WiFi setup depends on `nmcli`.
- **X11 debugging tools**: `xprop` and `xwininfo` (in x11-utils) — use these to verify EWMH struts and window states
- **Audio boots muted on this hardware.** `Master` and `Capture` are muted
  and at 0% out of the box (confirmed via the `platform::mute`/
  `platform::micmute` LEDs, which mirror the real ALSA switch state via the
  kernel's `snd_ctl_led` module — not a separate/stuck indicator). `Speaker`/
  `Headphone`/`PCM` are already unmuted at 100%, so `Master` is the only
  gate that matters. `scripts/install.sh` (Phase 11) needs to unmute and set
  a sane default (`amixer sset Master 70% unmute`, matching the bar's
  default slider position) as part of first-boot setup, or a freshly imaged
  machine will be silent with no visible error.
- **Recurring ALSA underruns from the launcher — found on real hardware (2026-07-31), partially mitigated, not fully root-caused.** The systemd journal showed `ALSA lib pcm.c:(snd_pcm_recover) underrun occurred` from `launcher.main` recurring every 1-7 minutes, unrelated to actual button clicks (it happened while idle, not just when `click.wav` plays — SDL keeps the mixer's ALSA stream open for the whole process lifetime, not just while a sound plays). One suspect: `click.wav` is 44100Hz, pygame's mixer defaults to 44100Hz, but this hardware's HDA Intel PCH codec only runs natively at 48000Hz, so ALSA was continuously resampling — added `pygame.mixer.pre_init(frequency=48000, size=-16, channels=2, buffer=4096)` in `launcher/main.py` before `pygame.init()` to request the native rate directly and give a larger buffer. **This did not fully fix it**: verified on real hardware, one underrun still recurred ~4.5 minutes after restart (vs. every 1-7 min before), suggesting the resample wasn't the whole story — the launcher's own render loop runs at ~54-63% CPU on this Celeron N, and periodic scheduling contention for the audio thread is a more likely root cause than sample-rate mismatch alone. `snd_pcm_recover` self-heals the stream automatically each time, so this has not been observed to cause an audible glitch (just a defensive-recovery log line) — treat as a known, low-severity open item, not a fixed bug. If it needs to go away entirely, next step would be profiling whether the render loop itself can be cheaper (or given real-time scheduling priority for the audio thread) rather than further mixer tuning.

**Server dev machine**: Windows, via WSL2 + Docker — decided 2026-07-30. Keep
the repo checkout on the native WSL2 filesystem (e.g. `~/classpad`), not
under `/mnt/c/...` — bind mounts from the Windows drive are noticeably
slower and SQLite (Phase 7) is sensitive to slow/networked filesystem I/O
for its locking. Target deployment is Ubuntu Server on Hyper-V on real
server hardware — x86_64, so no multi-arch build concerns; a Docker image
built in WSL2 (also x86_64) runs unmodified there.

---

## Network & Deployment Context

- School network: DHCP, WPA2-Enterprise, PEAP/MSCHAPv2 specifically (confirmed 2026-07-31 — corrected from the earlier generic "PEAP/TTLS"), Zscaler content filtering. `scripts/install.sh` (Phase 11) creates the NetworkManager profile via `nmcli` (`wifi-sec.key-mgmt wpa-eap`, `802-1x.eap peap`, `802-1x.phase2-auth mschapv2`) from `CLASSPAD_WIFI_SSID`/`CLASSPAD_WIFI_IDENTITY`/`CLASSPAD_WIFI_PASSWORD`/`CLASSPAD_WIFI_CA_CERT` env vars — never hardcoded, add-only (never modifies/deletes an existing connection). Actual PEAP association is **unverified** — no RADIUS server available in dev, see TODO.md Phase 11.
- Machine identity: hostname is `11e-<serialnumber>` (from `dmidecode -s system-serial-number`) and is the authoritative machine ID server-side (`machines.id`) — `launcher/config.py` (Phase 9) should use `socket.gethostname()` for the `/config/<machine_id>` and `/telemetry/<machine_id>` calls, matching this. `scripts/install.sh` sets the hostname once at install time (requires root; a later boot path might not have it) and also writes it to `/opt/classpad/machine_id` as a fallback record — see `pre-build-decisions.md` §7. Admins additionally give each machine a friendly `display_name` (e.g. `blue-3`) in the admin portal, shown in place of the hostname there and matched to a physical sticker on the machine (see "Central Server" above) — portal-only decoration; the hostname itself does not change and the client never sees or sends its own `display_name`.
- Central server: local network, Docker — deployment target is Ubuntu Server on Hyper-V, on real server hardware (decided 2026-07-30). Developed separately from the 11e client (see Development Environment above). **Server base URL on the client must be a configurable hostname, not a hardcoded IP** — each deployment site runs its own site-local Windows DNS with an entry for the server, since the server's actual address differs per site. `launcher/config.py` (Phase 9) should read this hostname from config rather than assuming a fixed address.
- SMTP: unauthenticated relay inside WAN, accepts mail for @education.vic.gov.au and @edumail.vic.gov.au
- Target deployment: Lenovo ThinkPad 11e 3rd gen (20G9S05P00) and 5th gen (20LRS04R00)
