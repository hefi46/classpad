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

FONT_NAME = "DejaVu Sans"  # matches launcher/button.py
NOTE_LABEL_SIZE = 64
NOTE_LABEL_COLOR = (255, 255, 255)
NOTE_LABEL_OUTLINE = (40, 40, 40)
NOTE_LABEL_BOTTOM_PADDING = 40

KEYCAP_SIZE = 56
KEYCAP_TOP_PADDING = 24
KEYCAP_COLOR = (250, 250, 250)
KEYCAP_BORDER_COLOR = (90, 90, 90)
KEYCAP_LABEL_COLOR = (40, 40, 40)

# (wav filename, key color, note name, bound key, keycap label) — a
# toy-xylophone rainbow order, one octave C4-C5. The Z-X-C-V-B-N-M-,
# bottom-QWERTY-row mapping is the standard "computer keyboard piano"
# layout, which happens to line up exactly with an 8-note single octave.
KEYS = [
    ("c4.wav", (230, 25, 25), "C", pygame.K_z, "Z"),
    ("d4.wav", (245, 130, 32), "D", pygame.K_x, "X"),
    ("e4.wav", (245, 220, 30), "E", pygame.K_c, "C"),
    ("f4.wav", (60, 180, 60), "F", pygame.K_v, "V"),
    ("g4.wav", (40, 110, 220), "G", pygame.K_b, "B"),
    ("a4.wav", (100, 50, 190), "A", pygame.K_n, "N"),
    ("b4.wav", (200, 60, 190), "B", pygame.K_m, "M"),
    ("c5.wav", (230, 25, 25), "C", pygame.K_COMMA, ","),
]


def _render_outlined_text(font, text, fg, outline, outline_width=2):
    base = font.render(text, True, fg)
    outlined = pygame.Surface(
        (base.get_width() + outline_width * 2, base.get_height() + outline_width * 2),
        pygame.SRCALPHA,
    )
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx or dy:
                outlined.blit(font.render(text, True, outline), (dx + outline_width, dy + outline_width))
    outlined.blit(base, (outline_width, outline_width))
    return outlined


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

    sounds = [pygame.mixer.Sound(str(NOTES_DIR / filename)) for filename, *_ in KEYS]

    key_width = screen_width // len(KEYS)
    key_rects = [
        pygame.Rect(i * key_width, 0, key_width - KEY_GAP, window_height)
        for i in range(len(KEYS))
    ]

    key_to_index = {key: i for i, (_, _, _, key, _) in enumerate(KEYS)}

    note_font = pygame.font.SysFont(FONT_NAME, NOTE_LABEL_SIZE, bold=True)
    note_labels = [
        _render_outlined_text(note_font, note_name, NOTE_LABEL_COLOR, NOTE_LABEL_OUTLINE)
        for _, _, note_name, _, _ in KEYS
    ]
    keycap_font = pygame.font.SysFont(FONT_NAME, 28, bold=True)
    keycap_labels = [keycap_font.render(keycap, True, KEYCAP_LABEL_COLOR) for *_, keycap in KEYS]

    pressed_indices = set()
    mouse_down_index = None
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
                        pressed_indices.add(i)
                        mouse_down_index = i
            elif event.type == pygame.MOUSEBUTTONUP:
                if mouse_down_index is not None:
                    pressed_indices.discard(mouse_down_index)
                    mouse_down_index = None
            elif event.type == pygame.MOUSEMOTION:
                # Glissando: dragging across the bars with the button held (like
                # sliding a mallet across a real xylophone) plays each bar it
                # crosses, not just the one first pressed.
                if mouse_down_index is not None and event.buttons[0]:
                    for i, rect in enumerate(key_rects):
                        if rect.collidepoint(event.pos) and i != mouse_down_index:
                            sounds[i].play()
                            pressed_indices.discard(mouse_down_index)
                            pressed_indices.add(i)
                            mouse_down_index = i
                            break
            elif event.type == pygame.KEYDOWN:
                i = key_to_index.get(event.key)
                if i is not None:
                    sounds[i].play()
                    pressed_indices.add(i)
            elif event.type == pygame.KEYUP:
                i = key_to_index.get(event.key)
                if i is not None:
                    pressed_indices.discard(i)

        screen.fill(BACKGROUND_COLOR)
        for i, (rect, (_, color, _, _, _)) in enumerate(zip(key_rects, KEYS)):
            pygame.draw.rect(screen, color, rect, border_radius=20)
            if i in pressed_indices:
                pygame.draw.rect(screen, PRESSED_BORDER, rect, width=6, border_radius=20)

            keycap_rect = pygame.Rect(0, 0, KEYCAP_SIZE, KEYCAP_SIZE)
            keycap_rect.centerx = rect.centerx
            keycap_rect.top = rect.top + KEYCAP_TOP_PADDING
            pygame.draw.rect(screen, KEYCAP_COLOR, keycap_rect, border_radius=10)
            pygame.draw.rect(screen, KEYCAP_BORDER_COLOR, keycap_rect, width=3, border_radius=10)
            keycap_label_rect = keycap_labels[i].get_rect(center=keycap_rect.center)
            screen.blit(keycap_labels[i], keycap_label_rect)

            note_label_rect = note_labels[i].get_rect(
                centerx=rect.centerx, bottom=rect.bottom - NOTE_LABEL_BOTTOM_PADDING
            )
            screen.blit(note_labels[i], note_label_rect)

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    main()
