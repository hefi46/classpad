import os
from pathlib import Path

import pygame

from launcher.button import Button
from launcher.config import build_button_grid
from launcher.plugin_manager import scan_plugins

BAR_HEIGHT = 55  # must match bar/bar.py
BACKGROUND_COLOR = (173, 216, 240)
CLICK_SOUND_PATH = Path(__file__).parent / "assets" / "sounds" / "click.wav"
FPS = 30


def main():
    os.environ.setdefault("SDL_VIDEO_WINDOW_POS", f"0,{BAR_HEIGHT}")

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
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for button in buttons:
                    if button.contains(event.pos):
                        click_sound.play()
                        # Phase 5 wires this to process_manager.launch(); placeholder
                        # for now so the grid is clickable end to end.
                        print(f"Launch requested: {button.plugin.id}")

        screen.fill(BACKGROUND_COLOR)
        for button in buttons:
            button.draw(screen)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    main()
