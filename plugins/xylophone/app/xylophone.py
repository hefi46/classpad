import ctypes
import os
from pathlib import Path

import pygame

PR_SET_NAME = 15


def _set_process_name(name):
    # Without this, the OS-level process name (`comm`, what `pkill`/`ps` see)
    # is just "python3" — indistinguishable from the launcher's and bar's own
    # processes, both of which are also literally "python3". Renaming lets
    # this plugin have its own entry in process_manager.KILL_LIST /
    # recovery.sh's kill list without a broad "python3" pattern that would
    # also match (and kill) the launcher and bar themselves.
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(PR_SET_NAME, name.encode(), 0, 0, 0)
    except OSError:
        pass

BAR_HEIGHT = 55  # must match bar/bar.py and launcher/main.py
BACKGROUND_COLOR = (173, 216, 240)
NOTES_DIR = Path(__file__).parent / "notes"
FPS = 30
KEY_GAP = 8
PRESSED_BORDER = (255, 255, 255)

# (wav filename, key color) — a toy-xylophone rainbow order, one octave C4-C5
KEYS = [
    ("c4.wav", (230, 25, 25)),
    ("d4.wav", (245, 130, 32)),
    ("e4.wav", (245, 220, 30)),
    ("f4.wav", (60, 180, 60)),
    ("g4.wav", (40, 110, 220)),
    ("a4.wav", (100, 50, 190)),
    ("b4.wav", (200, 60, 190)),
    ("c5.wav", (230, 25, 25)),
]


def main():
    _set_process_name("xylophone")
    os.environ.setdefault("SDL_VIDEO_WINDOW_POS", f"0,{BAR_HEIGHT}")

    # Matches the launcher's own mixer fix: this hardware's HDA codec only runs
    # natively at 48000Hz, so requesting 44100Hz forces ALSA to resample
    # continuously for the life of the process and was found to cause periodic
    # underruns (see CLAUDE.md, 2026-07-31).
    pygame.mixer.pre_init(frequency=48000, size=-16, channels=2, buffer=4096)
    pygame.init()
    screen_width = pygame.display.Info().current_w
    screen_height = pygame.display.Info().current_h
    window_height = screen_height - BAR_HEIGHT

    screen = pygame.display.set_mode((screen_width, window_height), pygame.NOFRAME)
    pygame.display.set_caption("Xylophone")

    sounds = [pygame.mixer.Sound(str(NOTES_DIR / filename)) for filename, _ in KEYS]

    key_width = screen_width // len(KEYS)
    key_rects = [
        pygame.Rect(i * key_width, 0, key_width - KEY_GAP, window_height)
        for i in range(len(KEYS))
    ]

    pressed_index = None
    clock = pygame.time.Clock()
    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                for i, rect in enumerate(key_rects):
                    if rect.collidepoint(event.pos):
                        sounds[i].play()
                        pressed_index = i
            elif event.type == pygame.MOUSEBUTTONUP:
                pressed_index = None

        screen.fill(BACKGROUND_COLOR)
        for i, (rect, (_, color)) in enumerate(zip(key_rects, KEYS)):
            pygame.draw.rect(screen, color, rect, border_radius=20)
            if i == pressed_index:
                pygame.draw.rect(screen, PRESSED_BORDER, rect, width=6, border_radius=20)

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    main()
