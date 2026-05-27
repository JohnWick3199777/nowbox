# nowbox ZSH — sentinel written by precmd after every command.
# Format of /tmp/.nowbox_sentinel: "<seq> <exit_code> <cwd>"
# stdout capture is handled separately by command wrapping in VNCTerminal.

_NOWBOX_SEQ=0

precmd() {
    local rc=$?
    _NOWBOX_SEQ=$((_NOWBOX_SEQ + 1))
    printf "%d %d %s\n" "$_NOWBOX_SEQ" "$rc" "$PWD" > /tmp/.nowbox_sentinel
}

PROMPT='%F{cyan}%~%f %F{yellow}$%f '
