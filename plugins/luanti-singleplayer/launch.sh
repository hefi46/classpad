#!/usr/bin/env bash
# Launches Luanti directly into a persistent local-only world, skipping the
# main menu entirely — the plain "Luanti" tile's own main-menu/world-picker
# flow isn't something a 5-6 year old should have to navigate. `--go` (skip
# main menu, go directly in-game) requires the target world to already
# exist (confirmed on real hardware: `--go --world <path>` on a missing
# path hard-errors and exits instead of creating one), so this script
# creates it on first run and simply reuses it on every run after — a kid's
# build persists across sessions, same as TuxPaint's saved work.
#
# World + config live under $HOME (classpad's home directory), never inside
# this plugin's own /opt/classpad/plugins/luanti-singleplayer/ — that
# directory is root-owned, read-only (chmod a+rX, see CLAUDE.md's Phase 10
# notes) and gets rm -rf'd and recreated on every plugin update/redeploy,
# so anything writable (world saves, settings) has to live outside it.
#
# Uses its own --config file rather than the shared ~/.minetest/minetest.conf
# specifically so `bind_address = 127.0.0.1` here can't affect a future
# dedicated-local-server tile, which needs the opposite (listening on the
# LAN) — the two tiles must stay fully independent. See CLAUDE.md.
#
# Runs genuinely fullscreen (fullscreen = true in the config below), not
# just maximized — same exception CLAUDE.md already grants GCompris-qt/
# TuxMath: the game captures the mouse during play, so the bar is
# unreachable either way without pressing Escape first, and Escape reaches
# a mouse-clickable pause-menu exit (confirmed on real hardware) — the
# recovery hotkey and that exit cover getting back to the launcher, so
# there's no benefit to keeping the bar nominally visible-but-unusable
# behind maximized-not-fullscreen.
set -u

WORLD_DIR="$HOME/.minetest/worlds/classpad-world"
CONFIG_FILE="$HOME/.minetest/classpad-solo.conf"

mkdir -p "$WORLD_DIR"
if [ ! -f "$WORLD_DIR/world.mt" ]; then
    cat > "$WORLD_DIR/world.mt" <<EOF
gameid = minetest
backend = sqlite3
world_name = classpad-world
EOF
fi

if [ ! -f "$CONFIG_FILE" ]; then
    # bind_address = 127.0.0.1: confirmed on real hardware that Luanti's
    # integrated singleplayer server otherwise listens on 0.0.0.0:30000 —
    # reachable from the rest of the school LAN, not just this machine, and
    # this world has no password. This is the actual "just this computer"
    # enforcement; --address '' (the default, local game) only controls
    # what the *client* connects to, not what the server binds to.
    cat > "$CONFIG_FILE" <<EOF
name = student
bind_address = 127.0.0.1
fullscreen = true
EOF
fi

exec /usr/games/luanti --go --world "$WORLD_DIR" --gameid minetest --config "$CONFIG_FILE"
