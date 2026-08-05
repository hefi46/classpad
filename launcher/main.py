import math
import os
import queue
import sys
import threading
from pathlib import Path

import pygame

from launcher import process_manager
from launcher.button import DESC_COLOR, FONT_NAME, INK, TILE_COLOR, Button
from launcher.config import (
    BACKGROUND_IMAGE_CACHE,
    DEFAULT_BACKGROUND_COLOR,
    build_button_grid,
    get_server_url,
    load_cached_config,
    page_count,
    run_poller,
)
from launcher.pager import Pager
from launcher.plugin_manager import scan_plugins

BAR_HEIGHT = 55  # must match bar/bar.py
CLICK_SOUND_PATH = Path(__file__).parent / "assets" / "sounds" / "click.wav"
FPS = 30

# Page-flip slide animation (2026-08-03 redesign). Deliberately cheap: the
# tiles on both the outgoing and incoming page are already-rendered Button
# surfaces (icon/label/shadow baked in at construction, see button.py), so
# a "transition" here is nothing but interpolating a blit x-offset over a
# handful of frames at the existing FPS cap — no re-rendering of text or
# icons mid-flight, no measurable extra CPU on this hardware.
PAGE_TRANSITION_SECONDS = 0.22


def _ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _load_background_image(width, height):
    # convert() (not convert_alpha()) since this always fills the whole
    # window — there's nothing beneath it that transparency would reveal.
    try:
        image = pygame.image.load(str(BACKGROUND_IMAGE_CACHE)).convert()
    except (pygame.error, FileNotFoundError):
        return None
    return pygame.transform.smoothscale(image, (width, height))


def _resolve_lock(plugins, lock_id):
    """Look up the "lock to app" id (server-controlled, see config.py's
    _apply_lock) against what's actually installed locally.

    Deliberately returns None — falling back to the normal grid — rather
    than raising or freezing on an unresolvable id (admin locked to
    something not yet deployed to this machine, a stale id after a
    deletion, etc.): same "never leave a 5-6 year old stuck on a broken
    screen" reasoning as reconcile_plugins' empty-result handling in
    config.py. A lock that can't be honoured degrades to "just show the
    launcher", not a blank/frozen kiosk.
    """
    if not lock_id:
        return None
    for plugin in plugins:
        if plugin.id == lock_id:
            return plugin
    print(
        f"warning: locked to {lock_id!r} but it isn't installed locally — showing the normal grid instead",
        file=sys.stderr,
    )
    return None


# Sage accent — same #7CA79C used elsewhere (pager.py's ARROW_BG,
# button.py's hover fill family) for anything that needs to read as "calm
# highlight", not imported directly since pager.py's name is specific to
# arrows and would be a misleading name for this unrelated icon.
LOCK_ICON_COLOR = (124, 167, 156)
LOCK_HEADING_SIZE = 46
LOCK_BODY_SIZE = 26
LOCK_MUTED_SIZE = 20


def _draw_lock_icon(surface, center, size, color):
    """A plain hand-drawn padlock (rect body + arc shackle) rather than an
    emoji glyph — Quicksand doesn't cover that Unicode range (same reason
    bar.py hand-draws its home/speaker/battery icons instead of relying on
    font glyph coverage; see CLAUDE.md's "Launcher visual design").
    """
    body_size = int(size * 0.8)
    body_rect = pygame.Rect(0, 0, body_size, int(body_size * 0.75))
    body_rect.center = (center[0], center[1] + int(size * 0.12))
    pygame.draw.rect(surface, color, body_rect, border_radius=int(body_size * 0.15))

    shackle_rect = pygame.Rect(0, 0, int(size * 0.6), int(size * 0.6))
    shackle_rect.center = (center[0], body_rect.top)
    pygame.draw.arc(surface, color, shackle_rect, 0, math.pi, width=max(4, size // 9))


def _draw_lock_screen(screen, width, height, plugin_name, admin_url):
    """Full-screen "locked" message for the gap between the locked app
    closing and the relaunch actually covering the screen again.

    Without this, that gap just shows whatever pygame last drew — the grid,
    or nothing at first boot — which reads as a glitch, not the deliberate
    "you're locked, ask an admin" state it actually is. One render+flip,
    not a loop: this content just sits on screen untouched (nothing else is
    drawing) until the relaunching app's own window maps over it, so
    there's nothing to animate — same "cheap, not continuously redrawn"
    bias as the rest of this file's rendering.
    """
    screen.fill(TILE_COLOR)
    center_x = width // 2

    _draw_lock_icon(screen, (center_x, height // 2 - 130), 90, LOCK_ICON_COLOR)

    heading_font = pygame.font.SysFont(FONT_NAME, LOCK_HEADING_SIZE, bold=True)
    body_font = pygame.font.SysFont(FONT_NAME, LOCK_BODY_SIZE)
    muted_font = pygame.font.SysFont(FONT_NAME, LOCK_MUTED_SIZE)

    lines = [
        (heading_font, f"Locked to {plugin_name}", INK, height // 2 - 20),
        (body_font, "Ask an admin to unlock it from the admin portal:", INK, height // 2 + 38),
        (body_font, admin_url, LOCK_ICON_COLOR, height // 2 + 76),
        (muted_font, "Reloading…", DESC_COLOR, height // 2 + 130),
    ]
    for font, text, color, y in lines:
        text_surface = font.render(text, True, color)
        rect = text_surface.get_rect(center=(center_x, y))
        screen.blit(text_surface, rect)

    pygame.display.flip()


def main():
    os.environ.setdefault("SDL_VIDEO_WINDOW_POS", f"0,{BAR_HEIGHT}")

    # Hardware (HDA Intel PCH) only runs natively at 48000Hz — pygame's mixer
    # default of 44100Hz forces ALSA to resample continuously for the life of
    # the process (SDL keeps the stream open, not just while a sound plays),
    # which was causing periodic ALSA underruns under the launcher's own CPU
    # load (found on real hardware, 2026-07-31). Matching the native rate
    # removes that resampling; the larger buffer adds slack against
    # scheduling jitter from the render loop.
    pygame.mixer.pre_init(frequency=48000, size=-16, channels=2, buffer=4096)
    pygame.init()
    screen_width = pygame.display.Info().current_w
    screen_height = pygame.display.Info().current_h
    window_height = screen_height - BAR_HEIGHT

    screen = pygame.display.set_mode((screen_width, window_height), pygame.NOFRAME)
    pygame.display.set_caption("Classpad")

    click_sound = pygame.mixer.Sound(str(CLICK_SOUND_PATH))

    # Applied immediately at startup from the last-known-good cache, same
    # reasoning as the plugin grid never being blanked (launcher/config.py):
    # showing last session's background beats flashing the hardcoded
    # default for the ~30s until the first poll lands.
    cached_background = (load_cached_config() or {}).get("background") or {}
    background_color = _hex_to_rgb(cached_background.get("color") or DEFAULT_BACKGROUND_COLOR)
    background_image = (
        _load_background_image(screen_width, window_height)
        if BACKGROUND_IMAGE_CACHE.exists()
        else None
    )

    plugins = scan_plugins()

    # Same "apply the cache immediately, don't wait for the first poll"
    # reasoning as background above — a machine that reboots mid-lock
    # should come back locked straight away, not show the grid for ~30s.
    cached_lock_id = (load_cached_config() or {}).get("lock_to_app")
    locked_plugin = _resolve_lock(plugins, cached_lock_id)

    pager = Pager()
    pager.set_page_count(page_count(len(plugins), screen_width, window_height))
    pager.layout(screen_width, window_height)
    buttons = [
        Button(plugin, rect)
        for plugin, rect in build_button_grid(plugins, screen_width, window_height, page=pager.page)
    ]

    # run_poller() blocks on network I/O and would stall this render loop if
    # called directly — it also has to keep running while wait_for_exit()
    # below blocks the main thread for however long a child app is open, so
    # it lives on its own daemon thread. It never touches pygame itself
    # (Button() does SDL surface work, main-thread only); it just hands
    # already-locally-installed Plugin objects to update_queue for this loop
    # to turn into Buttons when convenient.
    update_queue = queue.Queue(maxsize=1)
    background_queue = queue.Queue(maxsize=1)
    lock_queue = queue.Queue(maxsize=1)
    poller_stop = threading.Event()
    poller_thread = threading.Thread(
        target=run_poller,
        args=(update_queue, poller_stop),
        kwargs={"background_queue": background_queue, "lock_queue": lock_queue},
        daemon=True,
    )
    poller_thread.start()

    clock = pygame.time.Clock()
    running = True
    transition = None  # None when idle, else {"from", "to", "direction", "start"}

    while running:
        if locked_plugin is not None:
            # Still pump QUIT (session/systemd shutdown) even though we skip
            # the rest of the normal event loop — everything else (clicks,
            # the pager, hover) is meaningless while there's no grid to show.
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
            if not running:
                break
            _draw_lock_screen(
                screen, screen_width, window_height, locked_plugin.name, f"{get_server_url()}/admin"
            )
            process = process_manager.launch(locked_plugin)
            process_manager.wait_for_exit(process)
            pygame.event.clear()  # same stale-click reason as the normal launch path below
            # The only point a newly-cleared (or newly-changed) lock can take
            # effect — matches the rest of this project's "reconcile only at
            # natural boundaries" rule (config.py never interrupts a running
            # child for a plugin/background update either).
            try:
                new_lock_id = lock_queue.get_nowait()
            except queue.Empty:
                pass
            else:
                locked_plugin = _resolve_lock(plugins, new_lock_id)
            continue

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.MOUSEMOTION:
                for button in buttons:
                    button.set_hovered(button.contains(event.pos))
            elif transition is not None:
                continue  # ignore clicks mid-flip — 220ms, not worth queuing
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                old_page = pager.page
                if pager.handle_click(event.pos):
                    old_buttons = buttons
                    buttons = [
                        Button(plugin, rect)
                        for plugin, rect in build_button_grid(
                            plugins, screen_width, window_height, page=pager.page
                        )
                    ]
                    transition = {
                        "from": old_buttons,
                        "to": buttons,
                        "direction": 1 if pager.page > old_page else -1,
                        "start": pygame.time.get_ticks() / 1000.0,
                    }
                    continue
                for button in buttons:
                    if button.contains(event.pos):
                        click_sound.play()
                        process = process_manager.launch(button.plugin)
                        process_manager.wait_for_exit(process)
                        # Nothing pumped the event queue while we were blocked in
                        # wait_for_exit(), so clicks made on the covered launcher
                        # window (or against its own now-stale button rects) are
                        # sitting in the queue — discard them, or the first queued
                        # click instantly relaunches the app that just closed.
                        pygame.event.clear()
                        for other in buttons:
                            other.set_hovered(False)

        try:
            new_plugins = update_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            plugins = new_plugins
            pager.set_page_count(page_count(len(plugins), screen_width, window_height))
            pager.layout(screen_width, window_height)
            buttons = [
                Button(plugin, rect)
                for plugin, rect in build_button_grid(plugins, screen_width, window_height, page=pager.page)
            ]
            transition = None  # a config-driven grid change snaps, it doesn't flip

        try:
            background_update = background_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            background_color = _hex_to_rgb(background_update["color"])
            background_image = (
                _load_background_image(screen_width, window_height)
                if background_update["has_image"]
                else None
            )

        try:
            new_lock_id = lock_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            locked_plugin = _resolve_lock(plugins, new_lock_id)
            if locked_plugin is not None:
                # Admin just turned lock mode on while we were sitting on
                # the grid — hand off to the lock branch immediately rather
                # than waiting for a click. Skip this frame's render; the
                # lock branch takes over next iteration.
                transition = None
                continue

        if background_image is not None:
            screen.blit(background_image, (0, 0))
        else:
            screen.fill(background_color)

        if transition is not None:
            elapsed = pygame.time.get_ticks() / 1000.0 - transition["start"]
            t = min(1.0, elapsed / PAGE_TRANSITION_SECONDS)
            eased = _ease_out_cubic(t)
            direction = transition["direction"]
            to_x = round(direction * screen_width * (1 - eased))
            from_x = round(-direction * screen_width * eased)
            for button in transition["from"]:
                button.draw(screen, offset=(from_x, 0))
            for button in transition["to"]:
                button.draw(screen, offset=(to_x, 0))
            if t >= 1.0:
                transition = None
        else:
            for button in buttons:
                button.draw(screen)
        pager.draw(screen)
        pygame.display.flip()
        clock.tick(FPS)

    poller_stop.set()
    pygame.quit()


if __name__ == "__main__":
    main()
