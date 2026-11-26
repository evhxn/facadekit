#!/bin/bash
# fppd calls this through scripts/eventScript:
#   callbacks.sh --list                         -> which events we want
#   callbacks.sh --type playlist --data <json>  -> a playlist started, played an item, stopped
# fppd waits for this script, so it only hands the event to PHP, which returns at once and
# does any slow work (projector power, the fault guard) in background workers.
# The event JSON is passed as one argument; it is never evaluated by the shell.

case "$1" in
    --list)
        echo "playlist"
        exit 0
        ;;
    --type)
        if [ "$2" = "playlist" ] && [ "$3" = "--data" ]; then
            exec "${FK_PHP:-/usr/bin/php}" "${0%/*}/lib/cli.php" playlist-event "$4"
        fi
        ;;
esac
exit 0
