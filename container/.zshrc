# nowbox ZSH — sentinel written by precmd after every command.
# Format of /tmp/.nowbox_sentinel: "<seq> <exit_code> <cwd>"
# stdout capture is handled separately by command wrapping in VNCTerminal.

_NOWBOX_SEQ=0

precmd() {
    # When the last command was a pipeline (e.g. cmd | tee ...), $pipestatus[1]
    # holds the exit code of the first stage. Fall back to $? otherwise.
    local rc=${pipestatus[1]:-$?}
    _NOWBOX_SEQ=$((_NOWBOX_SEQ + 1))
    printf "%d %d %s\n" "$_NOWBOX_SEQ" "$rc" "$PWD" > /tmp/.nowbox_sentinel
}

PROMPT='%F{cyan}%~%f %F{yellow}$%f '
