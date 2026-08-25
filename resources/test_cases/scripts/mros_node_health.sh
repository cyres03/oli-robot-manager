#!/bin/sh
set -u

pattern=${1:-.}

if ! bash -lic 'command -v mrosconsole >/dev/null' 2>/dev/null; then
    printf '%s\n' 'mrosconsole is unavailable in the login environment' >&2
    exit 127
fi

bash -lic \
    'timeout --signal=TERM --kill-after=2s 8s mrosconsole 2>&1 | grep -E -m 200 -- "$1"' \
    oli-mros-health "$pattern" 2>/dev/null

status=$?
if [ "$status" -eq 1 ]; then
    printf 'mrosconsole produced no lines matching: %s\n' "$pattern" >&2
fi
exit "$status"