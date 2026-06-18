# nowbox ZSH — sentinel written by precmd after every command.
# Format of /tmp/.nowbox_sentinel: "<seq> <exit_code> <cwd>"
# stdout/stderr capture is handled by wrapping accepted ZLE lines below.

_NOWBOX_SEQ=0

precmd() {
    # When the last command was a pipeline (e.g. cmd | tee ...), $pipestatus[1]
    # holds the exit code of the first stage. Fall back to $? otherwise.
    local rc=${pipestatus[1]:-$?}
    _NOWBOX_SEQ=$((_NOWBOX_SEQ + 1))
    printf "%d %d %s\n" "$_NOWBOX_SEQ" "$rc" "$PWD" > /tmp/.nowbox_sentinel
}

nowbox_capture_command() {
    local rc
    local cmd

    cmd="$(cat /tmp/.nowbox_command 2>/dev/null)"
    : > /tmp/.nowbox_stdout
    : > /tmp/.nowbox_stderr

    print -Pn -- $'\033[1A\033[2K'"$PROMPT"
    print -r -- "$cmd"
    eval "$cmd" > /tmp/.nowbox_stdout 2> /tmp/.nowbox_stderr
    rc=$?

    cat /tmp/.nowbox_stdout
    cat /tmp/.nowbox_stderr >&2
    return "$rc"
}

nowbox_accept_line() {
    if [[ -n "$BUFFER" ]]; then
        print -r -- "$BUFFER" > /tmp/.nowbox_command
        BUFFER="nowbox_capture_command"
    fi
    zle .accept-line
}

zle -N accept-line nowbox_accept_line

PROMPT='%F{cyan}%~%f %F{yellow}$%f '
