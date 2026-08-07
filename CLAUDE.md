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
- **Runs as a systemd `--user` unit (`classpad-bar.service`), `Restart=always`, added 2026-08-02** — previously just backgrounded (`bar.py &`) from `system/openbox/autostart` with nothing to bring it back if it crashed or wedged. Started from autostart via `systemctl --user start`, same as the launcher and for the same reason (see Pygame Launcher below): enabling it on `default.target` directly would let it race X coming up and crash-loop. Being a proper unit is what lets the recovery hotkey target it (see Staff Recovery below).
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
- **Paginated grid, not shrink-to-fit (redesigned 2026-08-02).** `launcher/config.py`'s `compute_page_grid()` derives a fixed `(cols, rows, tile_size)` from the screen area alone (capped at `MAX_TILE_SIZE`, floored at `button.ICON_SIZE`) — tile size is constant on every page regardless of plugin count, so installing more plugins adds pages instead of shrinking every tile on the home screen. `launcher/pager.py`'s `Pager` renders left/right arrow buttons (disabled/greyed at the first/last page) and numbered "1 2 3" page indicators, iPad-home-screen style. `main.py` keeps the full plugin list around and rebuilds the current page's buttons on pager clicks and on poller-driven config updates (which also clamps the current page if the new list is shorter, so it can't end up pointing at a page that no longer exists).
- On button click: launches the target as a subprocess, then waits for it to exit
- Polls the central server for config updates on a configurable interval
- Falls back to locally cached config if server is unreachable
- Runs as a systemd service with `Restart=always`
- **Server polling (Phase 9), implemented 2026-07-31.** `launcher/config.py`'s `run_poller()` runs on its own daemon thread, started by `main.py` — required because the main thread blocks synchronously in `process_manager.wait_for_exit()` for however long a child app is open (potentially the whole school day), so polling/telemetry can't live in the render loop. The poller never touches pygame or constructs `Button` objects (SDL surface work is main-thread-only); it hands the main thread already-locally-installed `Plugin` objects via a `queue.Queue(maxsize=1)`, which the render loop drains non-blockingly each frame. Server base URL: `CLASSPAD_SERVER_URL` env var if set (test/dev override), else `/opt/classpad/server_url` (written once by `install.sh` from the same env var, mirroring the `machine_id` file pattern) — never a hardcoded address, matching the "Network & Deployment Context" requirement below. **The button grid is never blanked by a poll result** — an enabled-plugins list that matches nothing installed locally (a fresh/misconfigured server profile, or a stale cache) is deliberately left un-applied rather than clearing the current grid; on a 5-year-old's kiosk with a teacher who can't debug it, a stale-but-populated grid is far preferable to an empty screen. Verified on real hardware against a stub HTTP server (not the real Flask server, which wasn't reachable from this host in this session): config-change reordering with no restart, `force_home` killing the running child and returning to the launcher with a `force_home_ack` telemetry POST observed, cache fallback when the stub was killed, and the empty-reconciliation case leaving the grid untouched — including a fresh-process-start with the server down *and* a cache that reconciles to nothing, which still shows the full local plugin set rather than blanking (the initial grid build always comes from a direct local scan, never from cache).
- `process_manager.kill_all()` (called directly from the poller on `force_home`, not by shelling out to `scripts/recovery.sh`) and `recovery.sh` are only equivalent because their kill lists are kept in sync by hand — both files already carry a "must match" comment; a future plugin added to one and not the other silently reintroduces the same un-killable-process bug the Xylophone plugin hit (see "Process kill list" below).

### Launcher visual design
**"Soft pastel & calm" redesign, 2026-08-03** — replaced the original generic white-card/stock-blue-accent look across the button grid, pager, and bar. A shared token set (not a shared module — `launcher/button.py`/`launcher/pager.py` are pygame RGB tuples, `bar/bar.py` is Cairo 0-1 floats + hex strings, separate processes on separate rendering stacks, so each expresses the same named values in its own format):
  - **Palette**: Ink `#4A4358` (text/icons, warmer than pure black) · Paper `#FFFCF7` (tile/bar surface, warmer than stark white) · Border `#C9BAC2` (dusty mauve) · Sage `#7CA79C` (accent — pager arrows/dots, active wifi bars, "good" battery) · Sage-soft `#DCEBE7` (hover fill, label chip). Genuine alerts (wifi disconnected, battery low) deliberately keep a real, clearly-legible warm red (`#C0392B`) rather than folding into the pastel palette — see the "one flat color for every lit bar" comment in `bar/bar.py`: a teacher once misread a colored wifi indicator as an error signal, and the fix there was to stop using red for a *non-error* state, not to make red itself less alarming.
  - **Font**: `Quicksand` (see `fonts-quicksand` in Required Packages above) replaces `DejaVu Sans` for tile labels, pager dot numerals, and the bar's clock/activity label — rounder terminals read as more "calm"/kid-friendly than a technical UI font. `DejaVu Sans` is kept for the plugin-manager/wordprocessor toolbar's hand-drawn icons (unrelated to this font swap) and is still the only thing covering Unicode ranges Quicksand doesn't (see the "Hand-drawn (Cairo)" comment in `bar/bar.py`).
  - **Tile signature**: a "paper card resting on the pastel wash" — cream surface, thin dusty-mauve border, generous 26px corner radius, a soft unblurred offset shadow (cheap: one extra translucent `pygame.draw.rect` per tile, pre-rendered once per `Button` alongside the icon/label rather than redrawn every frame), and a small sage-tinted chip behind each label.
  - **Background themes**: the admin portal's background picker (`server/models.py`'s `BACKGROUND_THEMES`) is a curated set of 5 named pastels (Sky/Mint/Blush/Butter/Lilac) rather than a freeform `<input type="color">` — a raw color picker let an admin choose a background that clashed with or killed contrast against the fixed tile/accent palette above; a small pre-approved set is guaranteed to work with it. Storage is still a plain hex string in `settings.background_color` (the `/config` JSON contract is unchanged — `background.color` is still just a hex string, the client has no idea themes exist), so this is purely an admin-portal-side constraint, enforced in `admin.py`'s `set_background_color` route (form submits a theme *name*, server looks up the hex — never trusts a raw hex from the form). Default theme is Sky (`#DCEEF7`), matching `launcher/config.py`'s `DEFAULT_BACKGROUND_COLOR` fallback.
  - **Page-flip animation**: `main.py` slides the outgoing/incoming page's tiles horizontally over `PAGE_TRANSITION_SECONDS` (0.22s) with a cubic ease-out, on both arrow clicks and page-dot jumps. Deliberately cheap — the tiles are already-rendered `Button` surfaces (icon/label/shadow baked in at construction), so a "transition" is just interpolating a blit x-offset over a handful of frames at the existing 30fps cap (`Button.draw(surface, offset=(dx, dy))`), no re-rendering of text or icons mid-flight. Input is ignored while a transition is in flight (`transition is not None` guard in the event loop) rather than queued, since 220ms isn't worth the complexity of queuing a click against tiles that are about to move. A poller-driven grid rebuild (server config change, not a user page-flip) cancels any in-flight transition and snaps instead — see the comment at that call site.
  - **Bar**: cream background (`.classpad-bar` CSS class + `BAR_WINDOW_CSS`, added via the same `Gtk.CssProvider` the clock/button CSS already used) instead of the default GTK theme grey, hand-drawn icons (`draw_home_icon`/`draw_speaker_icon`/`draw_battery_icon`) recolored via the `ICON_COLOR`/`BATTERY_COLOR_*` constants only — the drawing functions themselves are untouched, since they already took `color` as a parameter defaulting to the module constant. Volume slider trough/highlight/slider retinted to sage via CSS (`scale trough`/`scale trough highlight`/`scale slider` GTK3 node selectors).
  - **Hover-only description subtext, added same day.** Each tile's `manifest.json` `description` renders as small (15px, not bold) muted-mauve subtext below the label — but only while `Button.hovered` is true (`launcher/button.py`'s `draw()`), matching the existing hover-fill behavior rather than always showing, so the grid stays visually calm/uncluttered at rest and the extra context only appears when someone's actually looking at that tile. Pre-rendered once per `Button` at construction (`description_lines`), not per-frame — `draw()` just conditionally blits already-rendered line surfaces. `_wrap_description()` greedily word-wraps into up to `DESC_MAX_LINES` (2) lines sized to fit the tile width, then hard-truncates with an ellipsis (character-by-character, not word-by-word, so a single unbreakable long token like a URL still degrades gracefully) if the description doesn't fit in 2 lines. Fits inside the existing `MAX_TILE_SIZE=260` tile without any grid/layout changes — there was already ~65px of unused vertical space below the label at that size.

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

**Bug found and fixed on real hardware (2026-07-31), building Phase 10:** `plugin-install.sh` creates its extraction staging dir via `mktemp -d` (mode `0700`), then does `cp -a "$PLUGIN_ROOT/." "$TARGET_DIR/"` to install — `cp -a`'s attribute preservation propagates that `0700` onto `$TARGET_DIR` itself (confirmed by isolating the exact line with `bash -x` and `stat` before/after), leaving the installed plugin unreadable by anyone but root. This is a pre-existing bug in the script itself (Phase 3), not specific to Phase 10 — it just never surfaced before, because every previous invocation was same-user (whoever ran the install script could also read what it produced). Phase 10 is the first caller where the installer (root, via the plugin-deploy timer below) and the reader (`classpad`) are genuinely different users, which is exactly when it surfaced: a freshly-installed plugin was invisible to `plugin_manager.scan_plugins()` and unreadable by the launcher. Fixed with `chmod -R a+rX "$TARGET_DIR"` after the copy — read+execute only, not write, since installed plugin content is meant to be read-only once deployed. Any future direct/manual invocation of `plugin-install.sh` as root benefits from this fix too, not just the automated path.

**Bug found and fixed on real hardware (2026-08-03):** an orphaned local plugin copy with a corrupt `icon.png` (magic bytes only, not a real decodable image) crash-looped `classpad-launcher.service` indefinitely — `Button._load_icon()` (`launcher/button.py`) called `pygame.image.load()` on it deep inside `main()`'s startup path, an uncaught `pygame.error: Unsupported image format` took the whole process down before the first frame ever painted, and `systemd`'s `Restart=always` just relaunched straight back into the identical crash every ~3-4 seconds. Because this is a genuine unhandled exception and not a hang, the recovery hotkey (Staff Recovery, below) couldn't help — it only kills/restarts processes, and restarting reproduces the exact same crash. Fixed at the actual fault boundary: `plugin_manager.validate_manifest()` now attempts `pygame.image.load()` on the icon and raises `PluginValidationError` if it can't be decoded, so `scan_plugins()`'s existing try/except skip-with-a-warning handling (already used for a missing `manifest.json`) catches this too — one plugin with a broken icon is now skipped and logged instead of taking the whole launcher down. `pygame.image.load()` was confirmed safe to call with no `pygame.init()`/display mode set (needed since `plugin_manager.py` is also imported headlessly by `scripts/plugin_deploy.py`, running as root with no X session). Regression tests: `tests/test_plugin_manager.py::test_corrupt_icon_file_rejected` and `test_scan_plugins_skips_corrupt_icon_without_crashing`.

**Plugin deployment daemon (Phase 10), added 2026-07-31.** `scripts/plugin_deploy.py`, run by `system/systemd/classpad-plugin-deploy.timer` (`OnUnitActiveSec=5min`) via a `Type=oneshot` service — deliberately a separate **system-level** (root) systemd unit, not folded into the Phase 9 launcher poller thread. Plugin `install.sh` scripts need root by design (see Security above), but the launcher itself runs as the unprivileged `classpad` kiosk user and should stay that way; giving `classpad` narrow `NOPASSWD` sudo for just `plugin-install.sh` was considered and rejected, since that script's entire job *is* running arbitrary admin-trusted code as root — scoping sudo to it scopes nothing. Polls `GET /plugins/`, compares versions against what's installed locally (any difference, not just "catalogue is newer", triggers a reinstall — so a rollback still converges), downloads+installs new/changed plugins one at a time via `plugin-install.sh`, and skips the entire run if `/tmp/classpad_activity` is non-empty (a plugin install does `rm -rf` on the target directory first, which would delete files out from under a process actually running from it). **No signalling to the launcher when a plugin lands** — Phase 9's `run_poller()` already calls `scan_plugins()` fresh every 30s, so a newly-installed plugin shows up in the button grid on its own; confirmed end-to-end on real hardware with zero IPC between the two processes. Don't add a signalling mechanism later; the decoupling is intentional, not a gap.

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

**"Luanti Solo" (`plugins/luanti-singleplayer`), added 2026-08-05 alongside the plain "Luanti" tile.** Skips Luanti's own main-menu/world-picker entirely — not appropriate for the target age group — and drops straight into a persistent local world via `luanti --go --world <path> --gameid minetest --config <path>`. Three things found on real hardware, not assumed:
- `--go` on a world path that doesn't exist yet hard-errors and exits instead of creating one (confirmed via `debug.txt`: `ERROR[Main]: Provided world path doesn't exist`) — an earlier, locally-installed version of this tile (predating this repo entry) hit exactly this and silently failed to launch every time. `plugins/luanti-singleplayer/launch.sh` creates the world (a minimal `world.mt`) and a dedicated `--config` file on first run only, then reuses both — a kid's build persists across sessions, same as TuxPaint's saved work. Both live under `$HOME` (the `classpad` user's home), never inside the plugin's own install directory, which is root-owned/read-only and gets `rm -rf`'d on every plugin update.
- Without `bind_address = 127.0.0.1` in that dedicated config, Luanti's integrated singleplayer server binds `UDP 0.0.0.0:30000` — reachable from the rest of the school LAN, no password, and the in-game UI itself reports the session as "Multiplayer" rather than "Singleplayer" as a visible symptom. Fixed by giving this tile its own `--config` file (not the shared `~/.minetest/minetest.conf`) with `bind_address = 127.0.0.1` — confirmed via `ss -tulnp` that the server now binds loopback-only, and the in-game pause menu confirms `Mode: Singleplayer`. Kept deliberately separate from the shared config so it can't affect a future dedicated-local-server tile (not yet built), which will need the opposite (listening on the LAN) — the two must stay independent of each other.
- Runs genuinely fullscreen (`fullscreen = true` in that same config), the same exception already granted to GCompris-qt/TuxMath (see Recovery Model below): the game captures the mouse during play regardless, so the bar is unreachable either way without pressing Escape first. Verified the safety case before committing to this, same standard as those two — screenshotted (not just checked in source) that Escape reliably opens a "Game paused" menu with a large, clearly-labeled, mouse-clickable "Exit to OS" button; clicking it exits the process cleanly with zero help from `recovery.sh`; and the recovery hotkey path still reaches and kills it while genuinely fullscreen and focused, un-escaped, as the backup.
- Its process still shows up as `comm=luanti` (single process, no wrapper-script indirection like `soffice`→`soffice.bin`), so it's already covered by the `luanti` entry in `process_manager.py`'s/`recovery.sh`'s `KILL_LIST` — no new kill-list entry needed for this second tile.

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
  - `GET /config/<machine_id>` — returns JSON config for a machine (the shared plugin profile, layout order, `force_home`, global `background`)
  - `GET /plugins/` — plugin catalogue
  - `GET /plugins/<id>/download` — download a plugin zip
  - `GET /background/image` — download the current global background artwork (404 if none set)
  - `POST /telemetry/<machine_id>` — receives last-seen, current activity, errors, and the client's ack of a handled `force_home`
  - Web admin portal at `/admin`
- **One shared plugin profile, not per-machine assignment — decided 2026-07-31**, revisited while reviewing an admin-portal design mockup. Every machine's `/config` returns the identical enabled/ordered plugin list; there is no per-machine plugin layout and no plan to add one. This matches the actual use case (one classroom, one shared app set) and keeps the admin portal to a single "edit the profile" screen instead of a per-machine assignment UI. `force_home` stays per-machine — it's a real command aimed at one box, not part of the profile.
- **Machines have an admin-set friendly `display_name`, separate from their hostname.** The hostname (`11e-<serialnumber>`, see Network & Deployment Context) stays the machine's real identity server-side — free, unique, needs no coordination at image time — but isn't something a teacher should have to read off a screen. `display_name` (e.g. `blue-3`) is optional, admin-editable in the portal, shown in place of the hostname there, and meant to match a physical sticker on the machine. The client never sees or sends its own `display_name`.
- **Machines-page "Last seen" shows a friendly relative time, not a raw ISO timestamp, added 2026-08-05.** `admin.py`'s `_relative_time()` buckets by age (`<60s` → `Ns ago`, `<60m` → `Nm ago`, `<24h` → `Nh ago`, else `Nd ago`) — a teacher glancing at the roster wants "3m ago", not to parse a timezone-aware ISO string. Purely a display concern, independent of `_machine_status()`'s online/stale/offline bucketing just above it even though the breakpoints rhyme — one decides a status pill's colour, the other just formats text, and conflating them would make a future change to one silently affect the other. The exact ISO timestamp isn't lost, just deprioritized — it's still in the cell's `title` attribute (hover to see it).
- **Admin portal "Config" page (renamed from "Plugins", 2026-08-03).** Nav label and page heading changed to "Config" to reflect that the plugin catalogue/profile is now one of three subsections on the same page — the underlying blueprint/routes/endpoint names (`admin.plugins`, `/admin/plugins`) deliberately stayed as-is; "Plugins" reads fine as a subsection label once it's not the whole page. The reorder mechanism stayed the plain up/down form buttons (no JS) — a deliberate call to preserve the existing no-JS-admin-portal decision from 2026-07-31 rather than add drag-and-drop.
  - **Website tile creator**: a form (name, URL, icon, optional Chromium flags, "add immediately" checkbox) that builds a `type: website` manifest + zip server-side (`admin.py`'s `create_website_plugin`) and runs it through the same `upsert_plugin`/zip-storage path as a manual `.zip` upload — just generated from form fields instead of requiring the admin to hand-assemble a zip. `id` is slugified from the name, de-duplicated (`-2`, `-3`, ...) against the existing catalogue. No image-format validation on the icon (matches the existing zip-upload path, which also doesn't validate icon bytes — plugin content is admin-trusted input either way).
  - **Background (global, not per-machine)**: a `settings` table (single row) holds `background_color` (hex) and `background_image_filename`; both surface in `get_config()`'s new `background` key for every machine identically, matching the "one shared profile" philosophy rather than reopening per-machine assignment. An uploaded image always takes priority over the solid colour when both are set. Each re-upload gets a fresh random filename (`background-<token>.png`) specifically so `image_version` (just the filename itself, not a separate version column) changes even if the admin re-uploads a file with the same original name — that's what the client uses to detect "redownload this."
  - Client-side: `launcher/config.py`'s `run_poller()` now takes an optional `background_queue` (same maxsize=1, main-thread-owns-SDL handoff convention as the existing plugin `update_queue`) and downloads/caches the image to `/opt/classpad/background_image.png` (atomic tmp-then-rename) only when `image_version` changes. `launcher/main.py` applies the last-cached background immediately at startup (from `config_cache.json`, same "don't flash the default while waiting for the first poll" reasoning as the plugin grid) and re-applies on every queued update thereafter.
  - **Lock to App (global, not per-machine), added 2026-08-05.** `settings.lock_to_app` (nullable plugin id) — same single-row-settings, same-value-for-every-machine pattern as background above, matching "locks all devices" rather than reopening per-machine control. `admin.py`'s `set_lock_to_app`/`clear_lock_to_app` routes (Config page, new "Lock to App" section) only ever accept an id that's currently an *enabled* catalogue entry — re-validated server-side, not trusted from the form, same rule as `set_background_color`'s theme lookup. Disabling or deleting the currently-locked plugin also clears the lock (`toggle_plugin`/`delete_plugin` both check for this) rather than leaving it pointed at a dead/inactive id. `get_config()` exposes it unfiltered by `enabled` (unlike the `plugins` list) specifically so the client can still tell what it *was* locked to and fail safe if that id stops resolving locally — see below.
    - **Client-side (launcher):** `launcher/config.py` gained a third maxsize=1 queue (`lock_queue`, same handoff convention as `update_queue`/`background_queue`) and `LOCK_STATUS_FILE` (`/tmp/classpad_lock_to_app`) for bar.py to read cross-process. `_apply_lock`'s change-detection sentinel is `object()`, not `None` — `None` is the legitimate "not locked" value, so starting the poller's `last_lock_state` at `None` would make a first poll that finds the server already-unlocked look like "no change" and never reach the queue, stranding a machine that booted with a stale locked cache (regression test: `test_run_poller_notifies_unlock_even_though_it_matches_the_sentinel_default`). `launcher/main.py`'s `_resolve_lock(plugins, lock_id)` looks the id up against what's actually installed locally and returns `None` — falling back to the normal grid, never a blank/frozen kiosk — if it doesn't resolve (not yet deployed to this machine, stale id), same "never leave a child stuck on a broken screen" reasoning as `reconcile_plugins`'s empty-result handling. When locked, the main loop skips the grid/pager entirely and just launches the locked plugin, blocks in `wait_for_exit()`, and relaunches immediately when it exits — checking `lock_queue` (non-blocking) each time round for a since-changed/cleared lock, but only ever at that boundary (a running child is never interrupted mid-session, matching the rest of this project's poller reconciliation). A newly-activated lock reaches an idle grid immediately too (drained once per frame, not just at a click) rather than waiting for the next user click. Applied from `config_cache.json` immediately at startup, same as background — a reboot mid-lock comes back locked without waiting on the first poll.
    - **Full-screen "locked" reload message, same day.** Without it, the gap between the locked app closing and the relaunch actually covering the screen again just showed whatever pygame last drew (the grid, or nothing at first boot) — reads as a glitch, not the deliberate state it is. `main.py`'s `_draw_lock_screen()` renders a centred padlock icon, "Locked to `<plugin name>`", the admin portal URL (`{get_server_url()}/admin` — reuses the same URL the bar's info panel already shows), and a muted "Reloading…" line, then flips once right before every `process_manager.launch()` call in the lock branch (covers both the very first entry into lock mode and every subsequent relaunch after a quit, not a special case for either). Icon is hand-drawn with `pygame.draw` (rect + arc), not an emoji glyph — Quicksand doesn't cover that Unicode range, same reasoning `bar.py` already hand-draws its home/speaker/battery icons instead of relying on font glyph coverage. One render+flip, not a loop: the content just sits on screen untouched until the relaunching app's own window maps over it, matching this file's existing "cheap, not continuously redrawn" bias elsewhere (page-flip transition, hover-only description). Reuses `button.py`'s palette constants directly (same rendering stack, unlike bar.py's separate Cairo stack) rather than redefining them.
    - **Bar:** `bar.py`'s `_tick_lock` polls `LOCK_STATUS_FILE` once a second and greys out (`set_sensitive(False)`) the Home button while it's non-empty, same "disabled at a boundary" treatment `pager.py` already gives its arrow buttons — Home doesn't lead anywhere while locked (quitting or Home-killing the app just relaunches it, per main.py above), so a live-looking clickable button would be misleading. This is cosmetic only: Home still functions as a kill signal underneath (`recovery.sh`, unchanged) whether or not the button is sensitive, and the emergency recovery hotkey is untouched either way — both are expected to just cause the same immediate relaunch, which is the intended "until admin turns off" behaviour, not a bug.
    - **Machines-page warning banner, added same day.** A lock is easy to forget about once set, and a teacher looking at the Machines roster wondering why every machine looks "stuck" shouldn't have to go check the Config page to find out why — `admin.machines()` now also reads `get_settings()` and renders a full-width warning banner (same warn/amber tokens as the Config page's `.lock-active`, just page-width) with a quick Unlock button when a lock is active. `clear_lock_to_app` checks `request.referrer` (not trusted as a redirect target, just compared against `url_for("admin.machines")`) so unlocking from the banner bounces back to Machines instead of always landing on Config.
    - **Timed lock (2h / 24h / indefinite), added same day.** `settings.lock_to_app_expires_at` (nullable ISO timestamp), migrated the same idempotent way as `lock_to_app` itself. `models.LOCK_DURATIONS` is a curated fixed set (not a freeform hours field) — same "typo-proof" reasoning as `BACKGROUND_THEMES`, since a bad number here locks a classroom out for a nonsense span with no easy undo if the admin isn't back at a computer; `set_lock_to_app`'s route re-validates the submitted key against it, same rule as the theme lookup. **Expiry is enforced entirely server-side and never reaches the client.** `models.get_settings()` lazily clears an expired lock on every read (checked against `datetime.now(UTC)`, no scheduler/cron — every machine already polls `/config` every 30s, so an expired lock is caught on the next poll regardless of whether anyone's looking at the portal) rather than running a background sweep, matching the fact that there's no job-scheduling infra anywhere else in this project. `get_config()` was refactored to call `get_settings()` (previously ran its own duplicate query) specifically so this lazy-expiry logic isn't duplicated — and `get_config()`'s response still only ever carries `lock_to_app` as a bare id-or-null, exactly as before this feature existed; `lock_to_app_expires_at` is deliberately never added to that response (regression test: `test_lock_to_app_expiry_is_never_exposed_to_the_client`) — a machine has no way to know a duration was even picked, only that the lock disappeared, indistinguishable from a manual admin unlock. This was a deliberate design call, not an oversight: expiry is an admin-portal-only concern, kept entirely server-side rather than teaching the client anything new.
    - **Migration note:** `settings.lock_to_app` was added after a real server (this project's own dev/test box) had already been deployed with the old schema — `CREATE TABLE IF NOT EXISTS` is a no-op against a `settings` table that already exists without the column. `models.init_db()` now runs a small idempotent `_ensure_column` (`PRAGMA table_info` check, `ALTER TABLE ADD COLUMN` only if missing) on every startup, verified against that same real deployed DB (10 pre-existing catalogue rows intact, column added, no data loss) rather than just in a fresh test DB.
  - **Plugin table: delete (website tiles only) + enable/disable colour coding, added 2026-08-03.** `admin.py`'s `delete_plugin` route (`models.delete_plugin`) hard-removes a catalogue row and its zip — gated to `type == "website"` only, rejecting `app`/`custom` types even if the route is hit directly. Reasoning: website tiles are wholly admin-authored (the create-website form or a hand-built zip of the same shape), so losing the catalogue entry is trivially recreated; the uploaded app set (TuxPaint, GCompris, ...) is the curated/vetted catalogue and has no delete path on purpose, so a stray click can't drop one. Like `toggle_plugin_enabled`, deleting an enabled plugin renumbers the remaining enabled positions. Deleting from the catalogue does **not** uninstall it from machines that already have it locally — same as Phase 10's `plugin_deploy.py` never removing vanished-from-catalogue plugins, out of stated scope; a deleted tile just stops being offered/enabled going forward. The Enable/Disable button is coloured by current state, not by the action it performs (`.toggle-btn.is-on`/`.is-off` in `admin.css`, reusing the existing `--good`/`--critical` tokens from the machines page's status pills) — green means "live right now" regardless of whether the button itself says Enable or Disable, so the table reads at a glance without hovering to check position/row styling.
- **Fresh-install catalogue seeding (`server/seed_plugins.py`), added 2026-08-05.** Decided: for image finalization, every curated plugin built so far should already be in a brand-new server's catalogue and already installed on every machine (via the existing plugin-deploy timer, which pulls the *whole* catalogue regardless of enabled state) — so turning an app on in the admin portal is a pure `/config` flag flip with no network install wait, not something a teacher has to wait on. Run from the Docker `CMD` before gunicorn starts, on every container start, not just the first: `models.catalogue_is_empty()` gates it, so it's a genuine no-op (skips reading manifests, zipping, and the DB write entirely) once the catalogue has any rows at all — verified on real hardware against the actual deployed server, which already had 10 admin-uploaded rows, and separately verified the insert path itself in an isolated throwaway container/data dir (12 rows: 8 enabled at positions 0–7, 4 disabled at `position=NULL`, all 12 zips written, second invocation correctly skips). Deliberately not wired into `create_app()`/`init_db()` itself — every test builds a fresh app against an empty DB (`tests/server/conftest.py`) and would otherwise get silently seeded too, changing what "empty catalogue" means throughout the existing test suite. Default-enabled (positions 0–7, in this order): tuxpaint, tuxtype, tuxmath, gcompris, coolmathgames, wordprocessor, xylophone, libreoffice-writer. Default-disabled but present (so enabling later is instant): libreoffice-calc, libreoffice-impress, luanti, luanti-singleplayer, cheese — matches each one's own "not yet vetted"/camera-access caveat. This list is a human decision recorded here, not inferred from `plugins/`'s contents — a plugin absent from both lists (`memory-game`/`storybook`/`emotion-checkin`, currently placeholder-only) is simply skipped by the seed, still available for a manual admin-portal upload later. `luanti-singleplayer` ("Luanti Solo") was added 2026-08-05 alongside the plain `luanti` tile — see "Plugin System" below for what makes it a genuinely separate tile rather than a duplicate.
  - **Binaries are a separate concern from the seeded catalogue entry.** Seeding only delivers the plugin *bundle* (manifest+icon zip) to `plugins_dir()`/`plugin_deploy.py` — it says nothing about whether the underlying program is actually installed on a given machine. Found while building this: only `libreoffice-writer` was installed on the dev/test hardware, so Calc/Impress showed greyed out in the LibreOffice Start Center despite having valid, catalogued plugin tiles. Fixed by adding all the curated apps' packages to `scripts/install.sh`'s apt dependency list (step 1/13) — see "Required packages" above — so a freshly imaged machine has every binary the seeded catalogue's tiles need, whether or not that tile is enabled yet.
  - **Also found and fixed while building this: `server/requirements.txt` was missing `pygame`, and `server/Dockerfile`'s CMD broke under `sh`.** `server/routes/admin.py` (existing, pre-dates this work) already imported `launcher.plugin_manager` for manifest validation, and that module has done a module-level `import pygame` since the 2026-08-03 corrupt-icon fix — but `pygame` was never added to `server/requirements.txt`, so every gunicorn worker in a container built from this Dockerfile crash-looped on `ModuleNotFoundError`. (Found independently in two places the same day: this session, and a parallel session building `server/quickstart.sh` — same root cause, same fix, merged together.) Confirmed pygame's PyPI wheel bundles its own SDL2 (no extra `apt` packages needed) and that `pygame.image.load()` works with no display set, matching how `scripts/plugin_deploy.py` already relies on the same module as root with no X session. Separately, switching the Dockerfile's `CMD` to shell form (`sh -c "... && exec gunicorn ..."`, needed to run the seed script first) broke on its own: `sh` in the `python:3.12-slim` image is `dash`, not `bash`, and dash parses the bare `()` in `server.app:create_app()` as a subshell — found on the real deployed server immediately after the previous fix (`sh: 1: Syntax error: "(" unexpected`, container failing to start at all), fixed by single-quoting the factory string in the `CMD` array.

### Staff Recovery / Recovery Model
`recovery.sh` has two modes, since it now has two different callers with different needs (split 2026-08-02 — see below):
- **Default (no args):** kills all known child processes by name only. This is what `bar.py`'s Home button shells out to (`_on_home_clicked`) for the routine "go home" path — killing the tracked child is enough to unblock the launcher's `wait_for_exit()` and send it home, and a normal Home click has no reason to touch the bar or launcher that's handling the click.
- **`--full`:** does the same child-process kill, then also force-kills the bar and launcher themselves (`systemctl --user kill --signal=SIGKILL classpad-bar.service classpad-launcher.service`, added 2026-08-02) so both come back fresh via their own `Restart=always`. Uses `systemctl kill`, not `stop`/`restart`, so it doesn't wait on the normal stop lifecycle's timeout — same reasoning as the SIGKILL-not-SIGTERM choice below, this is the emergency path and can't depend on a graceful shutdown. Only the xbindkeys emergency hotkey passes `--full` (`system/xbindkeys/xbindkeysrc` invokes `recovery.sh --full`) — **bug found and fixed on real hardware (2026-08-02):** before this split, Home reused the same script the hotkey used, so every routine Home click also SIGKILLed and visibly restarted the bar; the split was needed the moment the bar/launcher force-kill was added, not just for the emergency path.

Two-layer recovery system:
1. **xbindkeys** daemon running at X session level — listens for `Ctrl+Alt+Shift+Escape` regardless of which app is in front, runs `recovery.sh --full` (see above).
2. **Admin portal** "Return to Home" button per machine — sets a flag in config response, machine detects it on next poll and clears all child processes via `process_manager.kill_all()` directly (not `recovery.sh` — see "Process kill list" under Pygame Launcher above for why the two kill lists must be kept in sync by hand). (This path does not reload the bar/launcher — it's a routine "go home" signal handled cooperatively by the running launcher process, not an emergency force-kill.)

This hotkey is the actual safety net for a child stuck in a fullscreen app (the bar itself is covered in that case — see Persistent Bar above), and now also the recovery path if the bar or launcher process itself gets wedged (e.g. the stuck-pointer-grab class of bug, see App Launching below) rather than just a child app. **Verified on real 11e hardware:** the Phase 2 gate (2026-07-30) confirmed the underlying mechanism — a generic modifier+key xbindkeys binding (tested as `Ctrl+Shift+F12`), both via synthetic (`xdotool key`) and a real physical keypress, fires correctly through GCompris-qt and TuxMath while each is genuinely fullscreen and focused, since neither establishes a blocking active keyboard grab. The final combo was changed in Phase 6 (2026-07-30) from an F-key to `Ctrl+Alt+Shift+Escape` — F12 requires the Fn key on a lot of laptop keyboards, which is exactly the kind of thing that trips up non-technical teaching staff in an actual emergency — and re-verified on real hardware against TuxMath fullscreen with the real `recovery.sh` wired up. Both TuxPaint/TuxMath-style apps also have a clean exit path reachable by the target age group (Escape repeatedly, or an on-screen X/close affordance), so the hotkey is an emergency fallback, not the routine way out.

Note `recovery.sh` sends `SIGKILL` (`pkill -9`), not the default `SIGTERM` — confirmed on real hardware that TuxPaint catches `SIGTERM` and shows an "unsaved changes?" save dialog instead of exiting. An emergency recovery path can't depend on the stuck app cooperating with a graceful shutdown.

Process kill list: `chromium`, `tuxpaint`, `tuxtype`, `tuxmath`, `gcompris-qt`, `xylophone`, plus any processes launched from plugin install.sh scripts. (Corrected from `gcompris` — Debian trixie only ships the Qt/QML rewrite, `gcompris-qt`; the classic GTK `gcompris` package no longer exists.) Each plugin manifest that spawns a long-running process outside `launch_command` itself (e.g. a bundled local server) must declare its process name explicitly — the kill list cannot infer names for arbitrary future plugins. **Found on real hardware (2026-07-31), building the Xylophone plugin (Phase 14):** any `custom`-type plugin invoked as `python3 <script>` shows up in `ps`/`pkill` as `python3` — indistinguishable from the launcher's and bar's own processes (both are also literally `python3`), so it can't just be added to the kill list by its interpreter name without also matching (and killing) the launcher/bar. `plugins/xylophone/app/xylophone.py` renames itself via `ctypes`/`prctl(PR_SET_NAME)` at startup so it gets its own kill-list entry (`xylophone`) instead. Any future Python-based `custom` plugin needs the same treatment.

### Provisioning / Imaging
**Preseed-based USB provisioning, added 2026-08-06 — supersedes the golden-image/`dd` approach TODO.md's Phase 16 originally sketched.** A raw whole-disk clone was never actually built; this is the decided replacement, not a fix to one. Uses [debian-preseed-iso-generator](https://github.com/bergmann-max/debian-preseed-iso-generator) (vendored unmodified into `scripts/vendor/` — not checked in, `git clone`d on demand by `scripts/build-provisioning-iso.sh`, gitignored, so it tracks upstream by re-cloning rather than by hand-merging patches).

- **`preseeds/classpad/preseed.cfg`** drives the Debian installer itself: locale/AU timezone, wired-DHCP `netcfg` (see below for why not WiFi), the **AARNet mirror** pinned explicitly (`mirror.aarnet.edu.au`, both the main and `-security` paths) rather than relying on `deb.debian.org`'s geoip redirect, whole-disk guided partitioning (no LVM — single-purpose kiosk boxes, no resize/snapshot need), and a sudo `sysadmin` account with root login disabled (the crypted password in the checked-in file is a **deliberate non-functional placeholder** — `mkpasswd -m sha-512` before building a real ISO). `d-i pkgsel` stays deliberately minimal (`tasksel standard` + `openssh-server` only) — every classpad-specific package is `scripts/install.sh`'s job via `apt-get`, same as a manually-provisioned machine, so this file never needs editing when the package list changes.
- **Offline-first is the whole point, not an incidental property.** `preseed/late_command` runs `scripts/install.sh` from a copy of this repo bundled directly on the install medium (`/cdrom/classpad-src`, injected by the build script below) — never fetched from `classpad-admin` or GitHub during provisioning. This mirrors the same "server enhances, doesn't gate" principle the running client already lives by (poller falls back to cache, grid is never blanked because the server's unreachable) — extended to provisioning time. AARNet is still needed during the d-i stage itself for base Debian packages (that's unavoidable with a netinst image), but that's a different, one-off network need from "can this become a working classpad machine," which now has zero dependency on the server being up. Once the machine *is* provisioned, `classpad-plugin-deploy.timer` (Phase 10, already existed) reconciles the bundled repo's plugin set against the live catalogue on its own schedule — so a USB built weeks ago still ends up current, it just doesn't need to be to finish provisioning.
- **`scripts/build-provisioning-iso.sh`** builds the actual bootable image: runs the vendored generator (untouched) to produce a preseeded netinst ISO, then does its own second `xorriso` extract → inject → repack pass — the generator has no "extra files" option, and this avoids forking it. Injects `git archive HEAD` (the committed state at build time, not the working tree — warns loudly if there are uncommitted changes) as `/classpad-src` on the disc, regenerates `md5sum.txt`, and re-spins the ISO with the same BIOS+UEFI hybrid `xorriso -as mkisofs` invocation the generator itself uses. amd64-only (the only arch this project targets) — the generator's arm64 branch isn't replicated.
- **Assumes wired Ethernet + DHCP on the imaging bench** (confirmed for this deployment) — d-i's own network stage only ever needs to reach AARNet, and WPA2-Enterprise/PEAP inside the installer environment (`netcfg`) is genuinely fragile and poorly documented, so it's deliberately kept out of d-i entirely. The classroom's actual WiFi is a first-boot concern instead — see below.
- **First-boot interactive WiFi setup** (`scripts/wifi-setup-interactive.sh`, `system/systemd/classpad-wifi-setup.service` + a `lightdm.service.d` drop-in, both installed by `install.sh`'s own new step). Runs once, on the real console (`tty1`), before `lightdm` starts (the drop-in makes `lightdm.service` `Requires=`/`After=` the wifi unit — `Before=` alone only orders, it doesn't pull in a dependency). Covers the gap `install.sh`'s existing non-interactive, env-var-driven WiFi step (`CLASSPAD_WIFI_SSID`/...) can't: a machine imaged on the bench doesn't know the classroom's actual credentials yet, so whoever physically carries it in needs to enter them once. Gated on **actual reachability** (a direct probe of `classpad-admin`, not nmcli's own connectivity check, which depends on a connectivity URL that isn't guaranteed configured) rather than on a specific connection name — install.sh's own idempotency check cares about exact-name matching for its own re-run safety, but this gate only cares whether there's a route to the world right now, from any connection. Skips instantly (no prompt) if already reachable, e.g. a wired bench link. **Only ever gates the first boot** — once its marker file exists it's never re-checked, even if WiFi later breaks (rotated password, etc.); deliberate, matching the rest of this project's "never leave the kid stuck, run off local cache" philosophy rather than hanging boot on a console prompt nobody's there to answer.
- **`scripts/wifi-configure.sh`, extracted 2026-08-06.** The actual `nmcli` connection-add logic used to live inline in `install.sh`'s WiFi step; pulled out into its own script so there's exactly one implementation, not two to keep in sync (this project has already been burned once by that shape of bug — see the process-kill-list history under Staff Recovery). Takes `CLASSPAD_WIFI_SSID`/`IDENTITY`/`PASSWORD`/`CA_CERT` from the environment; no-ops (exit 0) if `SSID` isn't set, fails loudly only for genuine misconfiguration (`SSID` set but `IDENTITY`/`PASSWORD` missing). Two callers: `install.sh`'s WiFi step (env already set from however install.sh itself was invoked), and the answer-file path below.
- **WiFi answer-file USB, added 2026-08-06** — a fleet-rollout shortcut so the interactive wizard above doesn't have to be typed into on every single machine. Before showing the `nmtui` prompt, `wifi-setup-interactive.sh`'s `try_answer_file()` scans for a removable volume labeled `CLASSPAD-WIFI` (`lsblk` + a read-only `mount`), and if it's carrying a `wifi-answers.env` (same `CLASSPAD_WIFI_*` variable names `wifi-configure.sh` already understands), sources it and calls `wifi-configure.sh` non-interactively — falling back to the manual wizard if the file is missing, invalid, or doesn't actually result in a reachable connection. `scripts/write-wifi-answers-usb.sh` builds one (prompts for SSID/identity/password, never echoes the password, writes `chmod 600`, shell-quotes each value with `%q` so special characters in a password can't break the sourced file). **Deliberately a separate stick from the reusable provisioning ISO/USB**, not baked into it — the base image is meant to be shareable across sites/schools with no secrets on it, and this is the one artefact that's genuinely site-specific. It carries a plaintext WPA2-Enterprise password once built, so it needs the same physical-security treatment as any other credential — same trust tradeoff this project already accepts for plugin zips (admin-trusted input, no sandboxing), not a hardened secrets-delivery mechanism.
- **Uses `nmtui edit`** (ships with `network-manager`, already a dependency — nothing new to build) for the manual/interactive fallback rather than a bespoke wizard.
- **Not yet verified on real hardware:** whether a root-run `nmtui` creates a system-wide connection profile (available at the LightDM greeter before autologin, same as `wifi-configure.sh`'s `nmcli` block explicitly sets via `connection.permissions ""`) or a session-scoped one. Flagged inline in `wifi-setup-interactive.sh` — if it turns out scoped, add an explicit `nmcli connection modify <name> connection.permissions ""` after `nmtui` exits. Also not yet verified: an actual end-to-end build-and-boot of an ISO (needs `xorriso`/`isolinux` and a real AARNet fetch), and the answer-file USB's `lsblk`/`mount` scan against a real removable device (only exercised in tests against faked binaries so far, not real hardware) — only syntax-checked and traced against the generator's real source so far.

---

## Open Risks — Verify Before Building Further

Gated verification steps live in `TODO.md` Phase 2. Two of the three original risks here have been verified on real hardware and are resolved below; the rest are still open.

- **Struts vs. real fullscreen — RESOLVED, design updated.** Confirmed on real hardware: GCompris-qt and TuxMath both go genuinely fullscreen (`_NET_WM_STATE_FULLSCREEN`) and Openbox stacks them above the bar, covering it — struts/above-state do not survive this. Rather than forcing these apps out of fullscreen (which would fight their own layout), the design now accepts this: the bar is guaranteed on the home screen and over non-fullscreen windows only, and the recovery hotkey (next item) covers the fullscreen case.
- **Recovery hotkey vs. keyboard grabs — RESOLVED, verified working.** Confirmed on real hardware with both a synthetic and a real physical keypress (Phase 2 gate, then re-verified in Phase 6 with the final `Ctrl+Alt+Shift+Escape` combo and the real `recovery.sh`): the combo reaches xbindkeys through GCompris-qt/TuxMath while each is fullscreen and focused. Neither app establishes a blocking active keyboard grab. This is what makes the fullscreen-bar limitation above acceptable rather than a blocker.
- **Chromium F11 fullscreen-escape — RESOLVED, verified working.** Confirmed on real hardware: F11 inside `--app` mode escaped to real fullscreen and hid the bar. Fixed with `FullscreenAllowed: false` in the Chromium managed policy (`system/chromium/policies/managed/classpad-policy.json`); re-verified on real hardware that F11 is now a no-op.
- **Website in-page navigation containment is still undecided.** `--app --incognito` plus Zscaler does not stop a child following a link off a curated site to uncontrolled content. The managed policy needs a `URLAllowlist` once the curated site list exists — not yet decided, see `pre-build-decisions.md` §3.
- **Plugin trust model.** Plugin zips can carry an `install.sh` that runs with elevated privileges via `plugin-install.sh`. Treat plugin upload as an admin-only, trusted-input operation (enforced in the admin portal, Phase 8) — there is no sandboxing of plugin code in this design.
- **Preseed USB provisioning — built 2026-08-06, not yet built-and-booted on real hardware.** `scripts/build-provisioning-iso.sh` has only been traced against the vendored generator's source and syntax-checked, not run end-to-end (needs `xorriso`/`isolinux` and a real AARNet fetch). Whether a root-run `nmtui` (`scripts/wifi-setup-interactive.sh`) creates a system-wide vs. session-scoped connection profile is also unverified, as is the answer-file USB's `lsblk`/`mount` device scan against real hardware (tested so far only against faked binaries) — see "Provisioning / Imaging" above for all three.
- **`install.sh`'s deploy rsync can silently delete server-deployed plugins — found 2026-08-03, not yet fixed.** Step 3's `rsync -a --delete ... "$REPO_DIR"/ "$DEPLOY_DIR"/` excludes `machine_id`/`server_url`/`config_cache.json`/`documents` but not `plugins/` — it only ever anticipated the *bundled* plugins that ship in the git repo. Plugins installed dynamically by `scripts/plugin_deploy.py` (anything created via the admin portal's website-tile-creator, or otherwise not checked into the repo's `plugins/` directory) don't exist in `$REPO_DIR`, so `--delete` removes them from `$DEPLOY_DIR` on every re-run — discovered by re-running this exact rsync command by hand for a code-only redeploy and watching a just-created website tile vanish from `/opt/classpad/plugins/`. Not destructive in practice (the server's catalogue is authoritative; `plugin_deploy.py`'s next run reinstalls anything still enabled there), but silent and surprising, and worth a real fix — either exclude `plugins/` from this rsync entirely and let `plugin_deploy.py` own that whole directory, or something smarter. Needs a decision, not a reflexive patch.

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
│   ├── pager.py                # Left/right page arrows + "1 2 3" indicators
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
│   ├── coolmathgames/          # Website-type button — https://www.coolmathgames.com/
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
│   ├── apt/
│   │   └── apt.conf.d/
│   │       ├── 20auto-upgrades              # Enables APT's periodic update+unattended-upgrade jobs — added 2026-08-07
│   │       └── 52classpad-auto-reboot       # Automatic-Reboot-WithUsers (kiosk is always "logged in"), 03:30 — added 2026-08-07
│   ├── lightdm/
│   │   └── lightdm.conf
│   ├── openbox/
│   │   └── autostart          # Starts bar.service then launcher.service
│   ├── systemd/
│   │   ├── classpad-bar.service                 # user-level (classpad), Restart=always — added 2026-08-02
│   │   ├── classpad-launcher.service           # user-level (classpad), the kiosk itself
│   │   ├── classpad-plugin-deploy.service       # system-level (root) — Phase 10
│   │   ├── classpad-plugin-deploy.timer         # OnUnitActiveSec=5min
│   │   ├── classpad-wifi-setup.service          # system-level (root), tty1, before lightdm — Provisioning, 2026-08-06
│   │   └── lightdm.service.d/
│   │       └── classpad-wifi-setup.conf         # Requires=/After= the unit above
│   ├── xbindkeys/
│   │   └── xbindkeysrc        # Ctrl+Alt+Shift+Escape recovery combo
│   └── chromium/
│       └── policies/
│           └── managed/
│               └── classpad-policy.json   # URLAllowlist — website navigation containment
├── preseeds/
│   └── classpad/
│       └── preseed.cfg        # Debian installer preseed — AARNet mirror, wired-DHCP netcfg, late_command runs install.sh from the bundled repo
├── scripts/
│   ├── install.sh             # First-time machine setup
│   ├── plugin-install.sh      # Install/update a plugin from server
│   ├── plugin_deploy.py       # Phase 10 — polls /plugins/, installs new/changed ones, root-only
│   ├── recovery.sh            # Called by xbindkeys to kill child processes
│   ├── build-provisioning-iso.sh   # Builds the preseeded USB/ISO, injects a repo copy for offline install
│   ├── wifi-setup-interactive.sh   # First-boot nmtui gate, run by classpad-wifi-setup.service
│   ├── wifi-configure.sh           # Shared nmcli WiFi logic — called by install.sh and the answer-file path
│   ├── write-wifi-answers-usb.sh   # Builds a site's WiFi answer-file USB (see Provisioning / Imaging)
│   └── vendor/                # gitignored — `debian-preseed-iso-generator` clone, fetched on demand
└── tests/                     # pytest unit tests (run via python3-pytest, apt-installed)
    ├── test_plugin_manager.py
    ├── test_plugin_install.py
    ├── test_plugin_deploy.py
    ├── test_process_manager.py
    ├── test_recovery.py
    ├── test_wifi_setup_interactive.py  # Fake curl/nmtui/lsblk/mount on PATH, same style as test_recovery.py
    ├── test_wifi_configure.py          # Fake nmcli on PATH — checks the actual connection-add args
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
  alsa-utils network-manager fonts-quicksand
  tuxpaint tuxtype tuxmath gcompris-qt cheese
  libreoffice-writer libreoffice-calc libreoffice-impress
  luanti luanti-game-minetest
  ```
  The second block (`tuxpaint` onward) is the curated-app binaries
  `server/seed_plugins.py` (2026-08-05) assumes are already present —
  seeding the server's plugin catalogue only delivers the plugin *bundle*
  (manifest+icon) via `plugin_deploy.py`, never the underlying program.
  Found on real hardware building the Calc/Impress tiles: only
  `libreoffice-writer` was actually installed, so Calc/Impress showed
  greyed out in the LibreOffice Start Center despite both having valid
  plugin tiles — the same failure would hit every seeded-but-uninstalled
  app on a freshly imaged machine without this list. `gcompris-qt` (not
  `gcompris` — trixie only ships the Qt/QML rewrite) and
  `luanti-game-minetest` (the default subgame; bare Luanti has no content
  to play) are corrected/complete package names, not assumptions.
  `fonts-quicksand` provides the `Quicksand` family used for tile/pager/bar
  text since the 2026-08-03 redesign (see "Launcher visual design" below) —
  it happened to already be installed on this dev machine when the redesign
  work started (reason unknown), which is what made it the obvious font
  choice; added here explicitly so a fresh image actually has it rather than
  silently falling back to whatever `pygame.font.SysFont`/Pango resolve
  "Quicksand" to when it's missing.
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

- **Every Chromium website plugin was silently failing to open — found and fixed on real hardware (2026-07-31), root cause was `install.sh` itself.** `install -d -o classpad -g classpad ".../.config/openbox"` (Phase 11) only chowns the *leaf* directory it's given, not any parent directories it has to create along the way — `.config` itself was left owned by `root` (default `mkdir` ownership from being created as a side effect). Chromium couldn't create `~/.config/chromium/Crash Reports` for its crash database because of this, and treats that as fatal: every single website-type launch crashed within about a second with `chrome_crashpad_handler: --database is required` followed by a SIGTRAP, before any window ever appeared. Reproduced with a completely bare `chromium about:blank` — nothing to do with this project's own Chromium flags. Extensively ruled out first: package corruption (reinstalled — no change), a Debian security-update regression (downgraded to the prior version — same crash), the Chromium managed policy file, an unrelated `tailscale` install, AppArmor (no denials logged), sandbox flags, `--single-process`, `--no-zygote`, disk/`/dev/shm`/ulimits, `XDG_RUNTIME_DIR`. Fixed by chowning `.config` itself explicitly in `install.sh` (Phase 11) before creating the `openbox` subdirectory inside it, regardless of whether `.config` already existed going in. Verified on real hardware: full Chromium process tree (browser, crashpad with a correct `--database=` flag, zygotes, GPU process, renderers) now starts and stays up, and a real website plugin (Bluey/ABC iview) renders its actual page inside the bar-and-launcher chrome.
- **Ctrl+Alt+F1-F12 virtual-terminal switching — found and fixed on real hardware (2026-07-31).** A real physical Ctrl+Alt+F2 press dropped straight to a text-mode console with no easy way back for a non-technical teacher — confirmed the classpad session (Xorg, bar, launcher) was still alive on its own VT the whole time, just not the active/displayed one (`chvt` back to it recovered everything with no other damage). LightDM's Xorg already runs with `-novtswitch`, but that flag only controls whether the server force-switches VTs on its own startup/exit, not the live Ctrl+Alt+Fn hotkey during a running session — the correct, separate setting is `Option "DontVTSwitch" "true"` in `system/X11/xorg.conf.d/10-classpad-no-vtswitch.conf` (new in Phase 11's `install.sh`, step 5/12). Verified on real hardware, both via `xdotool` and confirmed the active VT (`/sys/class/tty/tty0/active`) never changes across `Ctrl+Alt+F1`/`F2`/`F3`/`F7` after a LightDM restart picks up the config.
- **Unattended security updates, added 2026-08-07.** These are unsupervised classroom kiosks with no admin regularly checking in on them (same reasoning as the plugin-deploy timer and the WiFi first-boot gate), so `install.sh` now installs the `unattended-upgrades` package and writes `/etc/apt/apt.conf.d/20auto-upgrades` (`APT::Periodic::Update-Package-Lists "1"; APT::Periodic::Unattended-Upgrade "1";` — package install alone does not enable these; they're normally only written by `dpkg-reconfigure unattended-upgrades`, which this non-interactive install never runs) plus a classpad-specific `52classpad-auto-reboot` drop-in. Deliberately scoped to security-only updates: the package's own conffile (`/etc/apt/apt.conf.d/50unattended-upgrades`, never edited directly) already ships with only the Debian-Security origin uncommented by default, and that's left alone rather than overridden — APT's config-list syntax appends to an existing block rather than replacing it, so changing it from a second file would need an explicit `#clear` first, and the default already matches what's wanted. `Unattended-Upgrade::Automatic-Reboot-WithUsers "true"` is the load-bearing line in the reboot drop-in: the kiosk user is *always* "logged in" via LightDM autologin for as long as the machine is powered on (see "No login" in Key Design Decisions), so without it a security update needing a reboot (e.g. a kernel patch) would never get one — there's no "no users logged in" moment on this hardware for the default `WithUsers=false` behaviour to ever catch. Reboot time is pinned to 03:30, and is safe/self-healing by design on this hardware: silent GRUB boot (install.sh step 6) + LightDM autologin + `Restart=always` on the bar/launcher units bring the kiosk straight back to the home screen with no one there to click through anything.

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
- Central server: local network, Docker — deployment target is Ubuntu Server on Hyper-V, on real server hardware (decided 2026-07-30). Developed separately from the 11e client (see Development Environment above). **Server hostname: decided 2026-07-31 — hardcoded to `classpad-admin`** (`launcher/config.py`'s `DEFAULT_SERVER_URL = "http://classpad-admin:5000"`, port matching `server/docker-compose.yml`/`server/Dockerfile`'s `5000`). Each deployment site's own local DNS just needs an A record for that name — a fresh machine then needs zero configuration to find the server. `CLASSPAD_SERVER_URL` (env var, checked first) and `/opt/classpad/server_url` (file, checked second) both still override this, for testing or a site using a different naming convention.
- SMTP: unauthenticated relay inside WAN, accepts mail for @education.vic.gov.au and @edumail.vic.gov.au
- Target deployment: Lenovo ThinkPad 11e 3rd gen (20G9S05P00) and 5th gen (20LRS04R00)

**Server host bring-up (bare Debian 13 trixie test box, 2026-08-03).** Used to test client↔server interaction end-to-end on real hardware, standing in for the eventual Ubuntu Server/Hyper-V target — separate from the WSL2 dev flow above. Findings, worth checking on any future fresh box:
  - `docker` + the `docker compose` plugin were already present on this image (Debian trixie ships them) — no `apt install` needed here, but don't assume that holds on every future host; verify with `docker compose version` before assuming a package install step is required.
  - The invoking user is not in the `docker` group by default, so plain `docker`/`docker compose` commands get "permission denied" on the socket. Either run compose via `sudo`, or `sudo usermod -aG docker <user>` and re-login (group changes don't apply to the current session).
  - `server/docker-compose.yml` requires `CLASSPAD_ADMIN_USERNAME`, `CLASSPAD_ADMIN_PASSWORD`, `CLASSPAD_SECRET_KEY` with no defaults (`server/app.py` refuses to start without them) — there's no template/example env file in the repo, so a fresh checkout needs a `server/.env` (gitignored) hand-created before first `up`.
  - This box's firewall (`iptables` INPUT policy `ACCEPT`, no `ufw`) let port 5000 through to the LAN with no extra rule. A hardened target (the real Ubuntu Server deployment, or any box with `ufw` enabled) will need an explicit allow rule for 5000/tcp — don't assume it's open.
  - Verified end-to-end against a real client on the same LAN: `GET /config/<machine_id>`, `GET /plugins/`, and the client's `DEFAULT_SERVER_URL` port (5000) all lined up with no changes needed on either side.

**`server/quickstart.sh`, added 2026-08-05.** Wraps the two manual steps above (hand-creating `server/.env`, then `docker compose up --build -d`) into one script, run from `server/`. If `server/.env` already exists it's left untouched (safe to re-run to just restart the container); otherwise it prompts for an admin username/password and generates `CLASSPAD_SECRET_KEY` itself (`openssl rand -hex 32`, falling back to `python3 -c 'import secrets; ...'` if `openssl` isn't present), writes `server/.env` with `chmod 600`, and never echoes the password to the terminal or into any file the script prints. Also skips the prompt entirely if all three vars are already exported in the shell — compose can read them from there without a `.env` at all. Detects the missing-`docker`/missing-compose-plugin and docker-group-permission cases from the bring-up notes above and prints the `usermod -aG docker` fix inline rather than just surfacing compose's raw error.

**Bug found and fixed on real hardware (2026-08-05), first `quickstart.sh` run on the test box:** the container booted and bound port 5000, but every gunicorn worker crashed and restart-looped on `ModuleNotFoundError: No module named 'pygame'` — `server/routes/admin.py` imports `launcher.plugin_manager` to reuse its manifest-validation schema, and `plugin_manager.py` has imported `pygame` at module level since the 2026-08-03 corrupt-icon fix (see "Bug found and fixed on real hardware (2026-08-03)" under Plugin System above), but `server/requirements.txt` was never updated to list it — it only had `Flask`/`gunicorn`. This is a container-image dependency, not a host one, so it belongs in `server/requirements.txt`/`server/Dockerfile`, not `quickstart.sh` (which only creates `.env` and drives `docker compose`, it has no say over what's installed inside the image). Fixed by adding `pygame` to `server/requirements.txt`; also corrected a stale `server/Dockerfile` comment that claimed `plugin_manager.py` had "no pygame import at module level," which stopped being true on 2026-08-03. Manylinux pygame wheels bundle SDL2, so a module-level `import pygame` alone shouldn't need extra `apt`/system packages (the server never calls `pygame.init()` or sets a display mode) — but this hasn't yet been re-verified with a real `docker compose up --build` on real hardware; do that before considering this closed, since it's possible the slim base image is still missing some shared library pygame's wheel expects to find on the system.
