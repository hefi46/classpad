import os
import queue
import threading
from pathlib import Path

import pygame

from launcher import process_manager
from launcher.button import Button
from launcher.config import (
    BACKGROUND_IMAGE_CACHE,
    DEFAULT_BACKGROUND_COLOR,
    build_button_grid,
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
    poller_stop = threading.Event()
    poller_thread = threading.Thread(
        target=run_poller,
        args=(update_queue, poller_stop),
        kwargs={"background_queue": background_queue},
        daemon=True,
    )
    poller_thread.start()

    clock = pygame.time.Clock()
    running = True
    transition = None  # None when idle, else {"from", "to", "direction", "start"}

    while running:
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
