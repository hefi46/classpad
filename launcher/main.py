import os
from pathlib import Path

import pygame

from launcher import process_manager
from launcher.button import Button
from launcher.config import build_button_grid
from launcher.plugin_manager import scan_plugins

BAR_HEIGHT = 55  # must match bar/bar.py
BACKGROUND_COLOR = (173, 216, 240)
CLICK_SOUND_PATH = Path(__file__).parent / "assets" / "sounds" / "click.wav"
FPS = 30


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

    plugins = scan_plugins()
    buttons = [Button(plugin, rect) for plugin, rect in build_button_grid(plugins, screen_width, window_height)]

    clock = pygame.time.Clock()
    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.MOUSEMOTION:
                for button in buttons:
                    button.set_hovered(button.contains(event.pos))
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
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

        screen.fill(BACKGROUND_COLOR)
        for button in buttons:
            button.draw(screen)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    main()
