#!/usr/bin/env bash
set -e

export DISPLAY=:1

# Virtual framebuffer — 1280x720 true-colour
Xvfb :1 -screen 0 1280x720x24 -ac -noreset &

# Give Xvfb a moment to start
sleep 0.5

# VNC server — no password, persist across client disconnects
x11vnc -display :1 -forever -nopw -rfbport 5900 -quiet -bg

# Start a tmux session running our instrumented zsh.
# tmux send-keys injects input directly into the PTY — no X11 focus required.
ZDOTDIR=/etc/nowbox tmux new-session -d -s nowbox -x 160 -y 40 "zsh"

# Attach xterm to the tmux session for the visual display.
exec xterm \
    -fa 'Mono' -fs 14 \
    -bg '#1e1e1e' -fg '#d4d4d4' \
    -geometry 160x40+0+0 \
    -title 'nowbox' \
    -e tmux attach-session -t nowbox
