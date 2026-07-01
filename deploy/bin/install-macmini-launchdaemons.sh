#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/Users/agent/project/notion-api-gateway-py}"
RUN_USER="agent"
LAUNCHD_DIR="/Library/LaunchDaemons"

POLL_LABEL="com.worxphere.notion-api-gateway"
WATCHDOG_LABEL="com.worxphere.notion-api-gateway-watchdog"

POLL_PLIST="${POLL_LABEL}.plist"
WATCHDOG_PLIST="${WATCHDOG_LABEL}.plist"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo PROJECT_ROOT=${PROJECT_ROOT} $0" >&2
  exit 1
fi

if [[ ! -d "${PROJECT_ROOT}" ]]; then
  echo "Project root not found: ${PROJECT_ROOT}" >&2
  exit 1
fi

if ! id "${RUN_USER}" >/dev/null 2>&1; then
  echo "Run user not found: ${RUN_USER}" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

mkdir -p operations/logs "${LAUNCHD_DIR}"
chown "${RUN_USER}:$(id -gn "${RUN_USER}")" operations operations/logs

plutil -lint "deploy/launchd/${POLL_PLIST}" "deploy/launchd/${WATCHDOG_PLIST}"

install -o root -g wheel -m 0644 "deploy/launchd/${POLL_PLIST}" "${LAUNCHD_DIR}/${POLL_PLIST}"
install -o root -g wheel -m 0644 "deploy/launchd/${WATCHDOG_PLIST}" "${LAUNCHD_DIR}/${WATCHDOG_PLIST}"

RUN_UID="$(id -u "${RUN_USER}")"
launchctl bootout "gui/${RUN_UID}/${POLL_LABEL}" >/dev/null 2>&1 || true
launchctl bootout "user/${RUN_UID}/${POLL_LABEL}" >/dev/null 2>&1 || true

for label in "${POLL_LABEL}" "${WATCHDOG_LABEL}"; do
  launchctl bootout "system/${label}" >/dev/null 2>&1 || true
done

# Stop any temporary nohup/manual poller before launchd takes ownership.
pkill -u "${RUN_USER}" -f "[n]otion-gateway poll" >/dev/null 2>&1 || true

launchctl bootstrap system "${LAUNCHD_DIR}/${POLL_PLIST}"
launchctl enable "system/${POLL_LABEL}"
launchctl kickstart -k "system/${POLL_LABEL}"

launchctl bootstrap system "${LAUNCHD_DIR}/${WATCHDOG_PLIST}"
launchctl enable "system/${WATCHDOG_LABEL}"
launchctl kickstart -k "system/${WATCHDOG_LABEL}"

launchctl print "system/${POLL_LABEL}" >/dev/null
launchctl print "system/${WATCHDOG_LABEL}" >/dev/null

echo "Installed and started:"
echo "  ${POLL_LABEL}"
echo "  ${WATCHDOG_LABEL}"
echo
echo "Check status:"
echo "  sudo launchctl print system/${POLL_LABEL}"
echo "  sudo launchctl print system/${WATCHDOG_LABEL}"
