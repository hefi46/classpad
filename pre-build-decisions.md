# Pre-Build Decision Checklist
### Kids' Linux Launcher Project

> Reconciled against `CLAUDE.md` / `TODO.md` on 2026-07-30 — nothing built yet (clean slate). Checked items are decided and documented elsewhere; unchecked items are still genuinely open. Items marked **FLAG** are gaps this pass surfaced that weren't visible before CLAUDE.md/TODO.md existed.

---

## 1. Base OS & Environment

- [x] Linux distro — Debian 13 (Trixie) minimal + Openbox (CLAUDE.md)
- [x] Window manager — Openbox (CLAUDE.md)
- [x] Python version — 3.11+ (CLAUDE.md)
- [x] Auto-login display manager — LightDM confirmed (CLAUDE.md, TODO Phase 11)
- [ ] Single OS image for all machines, or per-machine variants? — still open
- [ ] Imaging/cloning strategy — TODO Phase 16 mentions Clonezilla/`dd` but the choice isn't finalised

---

## 1b. Audio Stack

- [x] **RESOLVED (2026-07-30, real hardware)** — confirmed plain ALSA, no PulseAudio/PipeWire (`gcompris-qt`'s own log shows `PulseAudioService: pa_context_connect() failed`; nothing pulseaudio/pipewire-related installed). `bar/bar.py` uses `amixer -q sset Master <pct>%`. Still unverified: whether the ALSA control is actually named `Master` on the real 11e (`amixer scontrols`) — the dev machine used for this test may not be identical audio hardware to the classroom units.
- [ ] **FLAG (found 2026-07-30)** — hardware volume keys (dedicated keys and Fn+F1/Fn+F2) do not generate any input event on this 11e unit at the kernel level, even though `thinkpad_acpi` advertises `KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` as capabilities (checked via `evtest` on both the ThinkPad hotkey device and the main keyboard device directly, bypassing X). This means volume is **only** adjustable via the bar's on-screen slider, which in turn is unreachable during genuinely fullscreen apps (GCompris-qt, TuxMath) — see CLAUDE.md "Persistent Bar." Not investigated further (firmware-level issue, not something fixable in this stack); worth a quick check on a second physical unit before assuming it's true fleet-wide.

---

## 2. Pygame Launcher

- [ ] Target screen resolution and how to handle machines that differ — not addressed
- [x] Button grid layout — up to ~12 buttons (TODO Phase 4)
- [ ] Button size / icon size / font size standards — only "minimum 96px icons" specified (TODO Phase 4); no font size floor
- [x] Audio feedback on button press — yes (TODO Phase 4)
- [ ] Transition effect when launching an app — not specified
- [x] Config format — JSON via manifest.json + `/config/<machine_id>` response
- [x] Local fallback config if server unreachable — confirmed, `config_cache.json` (TODO Phase 9)
- [x] Config poll interval — 30s (TODO Phase 9)
- [ ] Where are icons/assets stored — implied `/opt/classpad/plugins/<id>/icon.png` via the plugin bundle, but never stated outside the manifest schema
- [x] How the launcher detects a child app has exited — `process_manager.wait_for_exit()` (TODO Phase 5)
- [x] **RESOLVED (2026-07-30)** — website buttons are NOT visually distinguished from app/custom buttons. To a 5-6 year old, a curated website and a native app are the same thing: a tile that launches an activity. Render all button types identically (icon + label); don't add a type badge/globe icon or any other marker.

---

## 3. Website / Kiosk Mode Buttons

### Browser

- [x] Browser confirmed — Chromium
- [x] Chromium launch flags agreed — **superseded from this checklist's original baseline**: CLAUDE.md decided `--app` (not `--kiosk`) plus the persistent bar, specifically because `--kiosk` overrides EWMH struts and hides the bar. Flags: `--app=URL --start-maximized --no-first-run --disable-session-crashed-bubble --disable-features=TranslateUI --overscroll-history-navigation=0 --disable-pinch --incognito`.
- [x] Per-website vs. global flags — per-website, via `chromium_flags` in the manifest (CLAUDE.md example)
- [ ] Default zoom level — `--force-device-scale-factor=1.25` shown only as an example in CLAUDE.md, not mandated; decide per-machine or global default

### Return to Home — the key design decision

- [x] **Superseded** — this checklist originally recommended a separate always-on-top overlay window per website. CLAUDE.md replaced that with the single persistent bar's Home button (kills all child processes, one mechanism for every app type, not one overlay per site). Confirm this supersession was deliberate if anyone revisits this file.
- [x] Overlay button position / size / toolkit questions below — **moot**, no per-site overlay is being built
  - ~~Overlay button position agreed~~
  - ~~Overlay button size and label~~
  - ~~Overlay toolkit — PyGTK or PyQt5~~
  - ~~Should the overlay also show which site is open~~
- [x] **VERIFIED (2026-07-30, real hardware) — struts do NOT survive genuine fullscreen.** GCompris-qt/TuxMath cover the bar entirely when fullscreen. Accepted as a limitation rather than fixed — see CLAUDE.md "Persistent Bar" and "Recovery model." The bar-Home button still works for the home screen and non-fullscreen apps; the recovery hotkey covers the fullscreen case (next section).

### Navigation & Content Control

- [x] **F11 fullscreen-escape — RESOLVED (2026-07-30, real hardware).** A separate, worse issue than in-page navigation: F11 inside `--app` mode escaped to real fullscreen and hid the bar (visually subtle, since `--app` already hides the address bar). Fixed and verified with `FullscreenAllowed: false` in `system/chromium/policies/managed/classpad-policy.json` — F11 is now confirmed a no-op, bar stays visible.
- [ ] In-page navigation off a curated site — **still open**, this is the original gap flagged in this checklist ("or accept curated sites are trusted" was the only fallback offered). The managed policy file exists and works (see above) but has no `URLAllowlist` yet — needs the actual curated site list before it can be added. Don't add a guessed domain list; see CLAUDE.md Open Risks.
- [ ] New tab / popup handling — not addressed; verify Chromium's default popup blocking behaviour in `--app` mode
- [ ] Download handling — not addressed; recommend blocking all downloads for this age group
- [ ] HTTPS only — not addressed; recommend enforcing, refuse to open HTTP URLs
- [ ] What to show if a whitelisted site fails to load or is slow — not addressed
- [ ] DNS-level filter as a backstop (CleanBrowsing, NextDNS) — not addressed; Zscaler is the only content filter currently in the stack and it doesn't cover in-page navigation on an allowlisted site

### Session & Config

- [x] Cookie/session data wiped every launch — `--incognito` confirms this
- [x] Website URLs in the same config JSON as app buttons, `type: "website"` — confirmed (CLAUDE.md manifest schema)
- [x] Who can add/edit website URLs — admin only via the admin portal (TODO Phase 8, single admin account)
- [x] Website buttons lockable to specific machines/groups — confirmed via per-machine plugin assignment (TODO Phase 8 "Assignment UI")

---

## 5. App Selection

### Confirmed apps
- [x] TuxPaint — v1 (TODO Phase 3 example manifests)
- [x] TuxType — v1
- [x] TuxMath — v1

### Existing Linux apps to evaluate — **still fully open, needs teacher input**
- [ ] GCompris-qt — full suite or specific activities? Still not decided; TODO doesn't build a manifest for it yet. It has been smoke-tested on real hardware (launches fullscreen, exits cleanly via Escape or its own close affordance, no PulseAudio dependency issue) purely as part of the Phase 2 fullscreen gate — that's not the same as an activity-content review with teaching staff.
- [ ] Pysycache — not decided
- [ ] KTuberling — not decided
- [ ] Blinken — not decided
- [ ] Childsplay — not decided

### Custom apps to build — v1 vs. later
- [x] Word processor with "Send to Teacher" — v1 (TODO Phase 12)
- [x] Memory card game — v1 (TODO Phase 13)
- [x] Xylophone — v1 (TODO Phase 14)
- [x] Storybook viewer — v1 (TODO Phase 15)
- [ ] Emotion check-in picker — **FLAG**: named in CLAUDE.md's directory structure but has no TODO phase. Confirm v1 or defer to v2.
- [ ] Mouse dexterity mini-games — not decided, may be covered by Pysycache/GCompris above

### Per-app config decisions — still open
- [ ] TuxPaint tools/stamps/brushes to enable
- [ ] TuxType word sets and difficulty for 5-6 year olds
- [ ] TuxMath difficulty ceiling
- [ ] GCompris activities whitelist (blocked on the "evaluate" decision above)

---

## 6. Central Server

- [x] Hosting location — local network, Docker (CLAUDE.md). Deployment target confirmed 2026-07-30: Ubuntu Server on Hyper-V, on real server hardware (x86_64).
- [x] Server dev environment — separate host from the 11e client, decided 2026-07-30: Windows machine via WSL2 + Docker, repo kept on the native WSL2 filesystem (not `/mnt/c/...`) for SQLite/filesystem-performance reasons. See CLAUDE.md "Development Environment."
- [ ] What happens to the children's experience if the server goes offline long-term — local cache covers short outages (TODO Phase 9) but no stated behaviour for extended downtime (e.g. plugin updates, telemetry backlog)
- [x] Server stack — Python Flask (CLAUDE.md)
- [x] Database — SQLite (CLAUDE.md)
- [x] Admin portal auth — single account, credentials in env var (TODO Phase 8). **No teacher/admin role split** — that's a deliberate scope cut, not an oversight, but worth confirming with whoever expected teacher self-service.
- [x] Server serves both config/plugins and web-based activity content (storybook JSON) — confirmed
- [x] SMTP relay handled by central server — confirmed (word processor → server → SMTP)
- [x] Email provider/credentials — school's own unauthenticated relay inside WAN, not a third-party provider (CLAUDE.md)
- [x] Config JSON structure — active plugins, layout order, pending commands like `force_home` (TODO Phase 7)

---

## 7. Machine Identity & Network

- [x] Static IP or DHCP — DHCP, WPA2-Enterprise PEAP/TTLS (CLAUDE.md)
- [x] Hostname naming scheme — `11e-<serialnumber>` from `dmidecode -s system-serial-number` (CLAUDE.md)
- [ ] **FLAG** — `dmidecode -s system-serial-number` needs root and can return blank/`None` on some hardware. TODO Phase 11 runs this once during `install.sh` (root context, fine) — but write the result to a machine_id file at that point rather than re-deriving it live on every boot, in case a later boot runs the check without root.
- [x] Individual vs. group configs — both supported, admin portal assigns "to a machine or group" (TODO Phase 8)
- [x] Wired or wireless — wireless, PEAP/TTLS (CLAUDE.md)
- [ ] Children's internet access — Zscaler filters, but see Navigation & Content Control above; allowlisted-site containment is the real open half of this question
- [x] Telemetry — last_seen, current_activity, errors via `POST /telemetry/<machine_id>` (TODO Phase 9)
- [ ] Firewall rules — what can client machines reach — not addressed anywhere

---

## 8. User & Session Model

- [x] No login, single shared OS user, auto-login — confirmed
- [ ] **FLAG** — TuxPaint artwork: save locally, sync to server, or wipe on reboot? Not addressed in CLAUDE.md or TODO — a real gap once children start producing content in Phase 3+ testing
- [x] Word processor child identification — name picker on open, names configured per-machine in admin portal (TODO Phase 12)
- [ ] Session data retained between reboots — `config_cache.json` persists by design; artwork/email retention is the open half (see above)

---

## 9. Word Processor & Email

- [ ] Format teacher receives — plain text, PDF, or screenshot? Not specified (TODO Phase 12 just says "POSTs content")
- [ ] **FLAG** — Which teacher receives the email? TODO Phase 12 only picks the *child's* name from a list; nothing selects or configures the receiving teacher address. This needs an explicit decision (fixed per-machine config in admin portal is the natural fit, but it isn't built).
- [x] Child's name on email — picked from a per-machine list (TODO Phase 12)
- [x] Does the teacher reply — out of scope for v1 (consistent with this checklist's own suggestion; no reply flow anywhere in TODO)
- [ ] Email content retention — stored on server or send-and-forget? Not addressed — same gap as artwork retention above
- **Note (2026-08-02):** the word processor rebuild (TODO Phase 12) gave saved stories a durable on-disk home (`/opt/classpad/documents/wordprocessor/`, plain `.txt` per story). That's a storage *mechanism* only — it does not answer any of the retention/consent flags above (wipe on reboot? sync to server? parental consent for "Send to Teacher"?), which are still open and still need a decision before that feature is built.

---

## 10. Staff Recovery & Resilience

- [x] Staff key combo agreed — `Ctrl+Alt+Shift+Escape` (CLAUDE.md). Changed in Phase 6 (2026-07-30) from the F12-based combo tested in the Phase 2 gate — F12 needs the Fn key on a lot of laptop keyboards, which trips up non-technical teaching staff in an actual emergency. No Fn key involved in the new combo.
- [ ] Key combo communicated how (laminated card, sticker) — non-technical, still open
- [x] Launcher runs as systemd service, `Restart=always` — confirmed (TODO Phase 6)
- [x] xbindkeys as OS-level listener — confirmed, and **verified 2026-07-30 on real hardware**: the Phase 2 gate confirmed the mechanism (tested as `Ctrl+Shift+F12`) fires correctly through both GCompris-qt and TuxMath while genuinely fullscreen and focused, via synthetic (`xdotool`) and real physical keypresses, with neither app blocking it via an active keyboard grab. The final `Ctrl+Alt+Shift+Escape` combo was re-verified the same way in Phase 6, with the real `recovery.sh` wired up (not the Phase 2 placeholder binding). This is the confirmed safety net for the fullscreen-bar limitation above.
- [x] `recovery.sh` uses `SIGKILL` (`pkill -9`), not the default `SIGTERM` — found on real hardware in Phase 6 that TuxPaint catches `SIGTERM` and shows a save-confirmation dialog instead of exiting, defeating the point of an emergency recovery path.
- [x] Remote reset from admin portal — v1, confirmed ("Return to Home" / `force_home`, TODO Phase 8-9)
- [x] Process kill list agreed — `chromium`, `tuxpaint`, `tuxtype`, `tuxmath`, `gcompris-qt` (CLAUDE.md, corrected from `gcompris` — Debian trixie only packages the Qt rewrite). Note: the list can't cover arbitrary future plugin processes automatically — CLAUDE.md now requires each plugin that spawns a long-running process to declare its process name explicitly.
- [x] **"Call Teacher" removed from scope entirely (2026-07-30)** — no hardware hotkey substitute either; the teacher is physically present in the classroom.

---

## 11. Accessibility

- [ ] Colour scheme and minimum contrast ratios — not addressed
- [ ] Minimum font/icon sizes across all custom apps — only launcher icon minimum (96px) is specified; nothing for the custom HTML5 apps (Phases 12-15)
- [ ] Audio cues on all interactions — TODO only specifies this for the launcher (Phase 4) and memory game (Phase 13), not universally
- [ ] Text-to-speech for storybook viewer — confirmed for that one app (TODO Phase 15, espeak/festival), not a broader decision
- [ ] Trackpad sensitivity defaults for small hands — not addressed
- [ ] Specific accessibility needs among the actual children — not addressed; needs input from teaching staff, not something to guess in code

---

## 12. Privacy & Safeguarding

**Still the least-addressed section, and the one with the most exposure once Phase 12 (email) ships. Worth closing before building Phase 12, not after.**

- [ ] What data, if any, leaves the local network — email via SMTP relay is the one confirmed egress path (stays within school WAN); no other stated egress, but never explicitly confirmed as "nothing else leaves"
- [ ] GDPR / school data policy compliance reviewed — not addressed
- [ ] Child-generated content retention/deletion policy — not addressed (same gap flagged in sections 8 and 9)
- [ ] Email feature parental consent — not addressed
- [ ] Who has access to the admin portal and teacher email inbox — admin portal is single-account (Section 6), but "who holds that credential" is a people/process question, not addressed

---

## 13. Scope & Phasing

- [x] MVP app list — tuxpaint, tuxtype, tuxmath + 5 custom apps (TODO Phases 3, 12-15); gcompris-qt and others remain unevaluated for activity whitelist (Section 5), though it's now installed and confirmed to run/exit cleanly on real hardware
- [ ] Custom apps v1 vs. v2 — resolved for 4 of 5 originally-listed custom apps; `emotion-checkin` still unscoped (Section 5, and TODO's new "Deferred" section)
- [ ] Number of machines in initial rollout — not stated
- [ ] Who maintains the server and machines ongoing — not stated
- [ ] OS and app update strategy — not addressed anywhere; worth deciding before Phase 16 imaging, since a fleet of unattended classroom laptops needs a patching story
- [x] Source control — repo exists, structure matches CLAUDE.md (this commit)
