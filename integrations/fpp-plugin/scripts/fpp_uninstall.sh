#!/bin/bash
# FacadeKit plugin uninstall. Safe to run more than once. On a Reinstall (FPP 10.4 sets
# FPP_PLUGIN_REINSTALL=1) the projector table, token hash and show list are kept.
: "${FPPDIR:=/opt/fpp}"
. "${FPPDIR}/scripts/common"

REPO=fpp-plugin-FacadeKit
DATA_DIR="${MEDIADIR}/plugindata/${REPO}"

# Background workers stop on their own once the plugin folder is gone; ask running ones to
# stop now. Only PHP processes running this plugin's own cli.php are matched.
if command -v pkill >/dev/null 2>&1; then
    pkill -f "plugins/${REPO}/lib/cli.php" 2>/dev/null || true
fi

if [ "${FPP_PLUGIN_REINSTALL:-0}" != "1" ]; then
    [ -d "${DATA_DIR}" ] && rm -rf "${DATA_DIR}"
    rm -f "${LOGDIR}/plugin-${REPO}.log" "${LOGDIR}/plugin-${REPO}.log".[0-9]* 2>/dev/null
fi
exit 0
