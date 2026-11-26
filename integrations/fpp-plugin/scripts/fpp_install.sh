#!/bin/bash
# FacadeKit plugin install. Runs as root; safe to run again (Reinstall, updates).
# Installs nothing from the network: the plugin is plain PHP and shell.
: "${FPPDIR:=/opt/fpp}"
. "${FPPDIR}/scripts/common"

REPO=fpp-plugin-FacadeKit
PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${MEDIADIR}/plugindata/${REPO}"
LOG="${LOGDIR}/plugin-${REPO}.log"

chmod a+x "${PLUGIN_DIR}/callbacks.sh" "${PLUGIN_DIR}/commands/"*.sh "${PLUGIN_DIR}/scripts/"*.sh 2>/dev/null

if [ ! -d "${DATA_DIR}" ]; then
    mkdir -p "${DATA_DIR}"
fi
chmod 700 "${DATA_DIR}"
if [ -n "${FPPUSER}" ]; then
    chown -R "${FPPUSER}:${FPPGROUP:-${FPPUSER}}" "${DATA_DIR}" 2>/dev/null || true
fi
find "${DATA_DIR}" -type f -exec chmod 600 {} + 2>/dev/null || true

if [ ! -x /usr/bin/php ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] /usr/bin/php not found; the plugin's commands need php-cli" >> "${LOG}"
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] plugin installed; nothing runs until a projector is added" >> "${LOG}"
if [ -n "${FPPUSER}" ]; then
    chown "${FPPUSER}:${FPPGROUP:-${FPPUSER}}" "${LOG}" 2>/dev/null || true
fi
# callbacks.sh exists, so FPP 10.4 loads the commands live after install (no restart needed).
exit 0
