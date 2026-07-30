# Pre-Build Decision Checklist
### Kids' Linux Launcher Project

---

## 1. Base OS & Environment

- [ ] Linux distro choice — Lubuntu vs Debian minimal + Openbox vs other
- [ ] Window manager — Openbox (recommended) vs LXQt vs other
- [ ] Python version (3.10+ recommended)
- [ ] Auto-login display manager — LightDM confirmed?
- [ ] Single OS image for all machines, or per-machine variants?
- [ ] Imaging/cloning strategy for deploying to multiple machines (Clonezilla, custom ISO, Ansible?)

---

## 2. Pygame Launcher

- [ ] Target screen resolution and how to handle machines that differ
- [ ] Button grid layout — how many buttons max on one screen?
- [ ] Button size, icon size, font size standards
- [ ] Audio feedback on button press — yes or no?
- [ ] Transition effect when launching an app (fade, instant?)
- [ ] Config format finalised — JSON structure decided? (see Server section)
- [ ] Local fallback config if server is unreachable
- [ ] Config poll interval (e.g. every 30 seconds, on boot only?)
- [ ] Where are icons and assets stored on the machine?
- [ ] How does the launcher detect a child app has exited and return fullscreen?
- [ ] How are website buttons visually distinguished from app buttons in the launcher UI?

---

## 3. Website / Kiosk Mode Buttons

### Browser

- [ ] Browser confirmed — Chromium recommended (best flag support for kiosk lockdown)
- [ ] Chromium launch flags agreed — suggested baseline:
  - `--kiosk` (hides all browser UI)
  - `--no-first-run`
  - `--disable-session-crashed-bubble` (suppresses crash recovery dialog)
  - `--disable-features=TranslateUI`
  - `--overscroll-history-navigation=0` (disables back swipe gesture)
  - `--disable-pinch` (no zoom)
  - `--incognito` or fresh `--user-data-dir` per launch (clean session every time)
- [ ] Per-website Chromium flags, or one global set for all website buttons?
- [ ] Default zoom level — consider increasing for young children (e.g. `--force-device-scale-factor=1.25`)

### Return to Home — the key design decision

The core problem: for external websites you do not control, you cannot inject a "Home" button into the page itself.

**Recommended approach: always-on-top overlay window**
Spawn a small separate window (PyGTK or PyQt5) at the same time as Chromium, set to always-on-top, positioned in a consistent corner (top-right recommended — less likely to be obscured). It shows a large, clearly labelled HOME button. Clicking it kills the Chromium process and closes itself, returning the Pygame launcher to fullscreen. This works for any website regardless of origin.

- [ ] Always-on-top overlay confirmed as approach?
- [ ] Overlay button position agreed — top-right corner recommended
- [ ] Overlay button size and label (icon only, text only, or both?)
- [ ] Overlay toolkit — PyGTK or PyQt5? (PyGTK is lighter; PyQt5 is easier if already a dependency)
- [ ] Should the overlay also show which site is open, or button only?

### Navigation & Content Control

- [ ] Navigation scope — can children click links within the site and navigate away from the starting URL?
  - `--kiosk` alone does not prevent in-page navigation
  - Consider a whitelist-only DNS filter or proxy as a safety net
  - Or accept that curated sites are trusted and in-page navigation is fine
- [ ] New tab and popup handling — block popups (`--disable-popup-blocking` off by default in kiosk, but verify)
- [ ] Download handling — block all downloads (recommended for this age group)
- [ ] HTTPS only — enforce or warn? (recommended: enforce, refuse to open HTTP URLs)
- [ ] What to show if a whitelisted site fails to load or is slow? (Chromium's default error page, or custom?)
- [ ] Content safety net — DNS-level filter (e.g. CleanBrowsing, NextDNS family preset) as a backstop if a site changes its content?

### Session & Config

- [ ] Cookie/session data — wipe on every Chromium launch (recommended) or persist across sessions?
- [ ] Website URLs defined in the same config JSON as app buttons, with a `type: "website"` field?
- [ ] Who can add or edit website URLs — admin only, or teachers via a restricted portal role?
- [ ] Should individual website buttons be lockable to specific machines or groups? (e.g. a phonics site only on certain machines)

---

## 5. App Selection

### Confirmed apps
- [ ] TuxPaint
- [ ] TuxType
- [ ] TuxMath — include in v1 or later?

### Existing Linux apps to evaluate
- [ ] GCompris — full suite launched from its own menu, or launch specific activities directly from the launcher?
- [ ] Pysycache — include for mouse training?
- [ ] KTuberling — include for creative play?
- [ ] Blinken (Simon Says memory) — include?
- [ ] Childsplay — evaluate vs GCompris, or both?

### Custom apps to build — confirm which are v1 vs later
- [ ] Simple word processor with "Send to Teacher" email button
- [ ] Card matching / memory game (with swappable card sets via server)
- [ ] On-screen xylophone / piano
- [ ] Picture-book / storybook viewer (content managed via server)
- [ ] Emotion check-in picker ("how are you feeling today?")
- [ ] Mouse dexterity mini-games (if not covered by Pysycache/GCompris)

### Per-app config decisions
- [ ] TuxPaint — which tools, stamps, and brushes to enable/disable?
- [ ] TuxType — appropriate word sets and difficulty for 5-6 year olds confirmed?
- [ ] TuxMath — difficulty ceiling for this age group?
- [ ] GCompris — activities whitelist agreed with teaching staff?

---

## 6. Central Server

- [ ] Hosting location — local network (Raspberry Pi / old PC) or cloud VPS?
- [ ] What happens to the children's experience if the server goes offline?
- [ ] Server OS and tech stack for admin portal (Python Flask/Django, Node.js, other?)
- [ ] Database — SQLite sufficient, or PostgreSQL?
- [ ] Admin portal authentication — how many admin accounts, any roles (teacher vs admin)?
- [ ] Does the server serve web-based activities, or config/email only?
- [ ] SMTP relay for "Send to Teacher" — handled by central server?
- [ ] Which email provider / SMTP credentials?
- [ ] Config JSON structure agreed before building client and server (machines, groups, buttons, apps)

---

## 7. Machine Identity & Network

- [ ] Static IP or DHCP for client machines? (Static simplifies admin)
- [ ] Hostname naming scheme (e.g. classroom-01, classroom-02)
- [ ] Individual machine configs or group/class configs?
- [ ] Are machines on a wired or wireless network?
- [ ] Children's internet access — blocked entirely, or filtered?
- [ ] What logging or telemetry goes back to the server? (last-seen, errors, usage?)
- [ ] Firewall rules — what can client machines reach?

---

## 8. User & Session Model

- [ ] Confirmed: no login, single shared OS user per machine (auto-login)
- [ ] TuxPaint artwork — save locally per session, sync to server, or wipe on reboot?
- [ ] Word processor — how does the child identify themselves? (name picker on that screen, or not required?)
- [ ] Any session data retained between reboots? If so, what and where?

---

## 9. Word Processor & Email

- [ ] Format teacher receives — plain text, PDF, or screenshot?
- [ ] Which teacher receives emails — per-machine config, selectable on screen, or fixed per classroom?
- [ ] Child's name on email — entered by child, picked from a list, or taken from machine identity?
- [ ] Does the teacher reply? (if yes, needs more thought — probably out of scope for v1)
- [ ] Email content retention — stored on server, or send-and-forget?

---

## 10. Staff Recovery & Resilience

- [ ] Staff key combo agreed — e.g. RShift + RCtrl + F12
- [ ] Key combo communicated how? (laminated card, sticker on machine?)
- [ ] Launcher runs as systemd service with Restart=always — confirmed approach?
- [ ] xbindkeys (or equivalent) as OS-level listener — confirmed approach?
- [ ] Remote reset from admin portal — in v1, or later?
- [ ] Process kill list agreed — which app process names does the recovery kill? (must include: chromium, tuxpaint, tuxtype, tuxmath, and the overlay window process)

---

## 11. Accessibility

- [ ] Colour scheme and minimum contrast ratios
- [ ] Minimum font/icon sizes across all custom apps
- [ ] Audio cues on all interactions — yes for everything, or selective?
- [ ] Text-to-speech for storybook viewer (espeak / festival)?
- [ ] Trackpad sensitivity defaults — adjusted in OS for small hands?
- [ ] Any children with specific accessibility needs to design for?

---

## 12. Privacy & Safeguarding

- [ ] What data, if any, leaves the local network?
- [ ] GDPR / school data policy compliance reviewed?
- [ ] Child-generated content (artwork, typed text) — retention and deletion policy?
- [ ] Email feature — does it need parental consent consideration?
- [ ] Who has access to the admin portal and teacher email inbox?

---

## 13. Scope & Phasing

- [ ] MVP app list confirmed (what must be in v1 vs what can wait?)
- [ ] Custom apps to build in v1 vs v2 agreed
- [ ] Number of machines in initial rollout
- [ ] Who maintains the server and machines ongoing?
- [ ] OS and app update strategy (how and how often?)
- [ ] Source control — repo location and structure agreed before first commit
