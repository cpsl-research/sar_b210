#!/usr/bin/env bash
# Write docker/app/.env for `docker compose --profile hw` (uid/gid, the B210's USB group, USB mapping,
# run folder, GUI host/port). No sudo, never opens the radio. See docs/docker.md.
#   docker/app/setup_env.sh [--non-interactive] [--print] [--force]
# SYSFS_USB_DIR (/sys/bus/usb/devices) and USB_NODE_DIR (/dev/bus/usb) override the paths (tests).
set -eu

NONINT=0; PRINT=0; FORCE=0
for a in "$@"; do
  case "$a" in
    --non-interactive) NONINT=1 ;;
    --print) PRINT=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
ENV_FILE="$HERE/.env"
SYSFS_USB_DIR=${SYSFS_USB_DIR:-/sys/bus/usb/devices}
USB_NODE_DIR=${USB_NODE_DIR:-/dev/bus/usb}

if [ "$PRINT" = 0 ] && [ -e "$ENV_FILE" ] && [ "$FORCE" = 0 ]; then
  echo "$ENV_FILE exists; use --force to overwrite (or --print to preview)." >&2
  exit 1
fi

say() { echo "$@" >&2; }
# ask PROMPT DEFAULT -> answer on stdout (the default when non-interactive or on empty input)
ask() {
  if [ "$NONINT" = 1 ]; then echo "$2"; return; fi
  printf '%s [%s]: ' "$1" "$2" >&2
  ans=""; read -r ans || true
  echo "${ans:-$2}"
}

# ---- B210: Ettus vendor id 2500 (2500:0020 before the FPGA load, 0021/0022 after) -----------
node=""
for d in "$SYSFS_USB_DIR"/*; do
  [ -r "$d/idVendor" ] || continue
  [ "$(cat "$d/idVendor")" = 2500 ] || continue
  bus=$(cat "$d/busnum" 2>/dev/null || true); dev=$(cat "$d/devnum" 2>/dev/null || true)
  [ -n "$bus" ] && [ -n "$dev" ] || continue
  node=$(printf '%s/%03d/%03d' "$USB_NODE_DIR" "$bus" "$dev")
  break
done
if [ -n "$node" ] && [ -e "$node" ]; then
  USB_GID=$(stat -L -c %g "$node")
  say "B210 at $node (group $USB_GID: $(getent group "$USB_GID" | cut -d: -f1))."
else
  USB_GID=$(getent group plugdev | cut -d: -f3 || true); USB_GID=${USB_GID:-46}
  say "warning: no B210 (USB id 2500:*) found; USB_GID falls back to plugdev ($USB_GID). Re-run with the radio plugged in."
fi
case " $(id -G) " in *" $USB_GID "*) ;; *) say "note: you are not in group $USB_GID; the container user gets it through group_add, so this only matters for host-side runs." ;; esac
if [ ! -e /usr/lib/udev/rules.d/60-uhd-host.rules ] && [ ! -e /etc/udev/rules.d/60-uhd-host.rules ]; then
  say "warning: no 60-uhd-host.rules on this host; the container cannot open the B210 without it. Run: python3 tools/setup/host_setup.py"
fi

# ---- run folder and GUI bind ----------------------------------------------------------------
RUN_ROOT=$(ask "Run folder (host path; mounted at /runs)" "$REPO/runs")
GUI_HOST=$(ask "GUI host (127.0.0.1 = this machine only)" 127.0.0.1)
GUI_PORT=$(ask "GUI port" 8010)
if [ "$GUI_HOST" != 127.0.0.1 ] && [ "$GUI_HOST" != localhost ]; then
  say "warning: $GUI_HOST:$GUI_PORT is unauthenticated; anyone who can reach it can use the GUI."
fi

content=$(cat <<EOT
# Written by docker/app/setup_env.sh. Read by compose.yaml (see .env.example).
HOST_UID=$(id -u)
HOST_GID=$(id -g)
USB_GID=$USB_GID
USB_DEV=$USB_NODE_DIR
RUN_ROOT=$RUN_ROOT
GUI_HOST=$GUI_HOST
GUI_PORT=$GUI_PORT
EOT
)

if [ "$PRINT" = 1 ]; then
  echo "$content"
else
  printf '%s\n' "$content" > "$ENV_FILE"
  mkdir -p "$RUN_ROOT" "$REPO/configs/user"   # Docker would otherwise create them root-owned
  say "wrote $ENV_FILE"
fi
say ""
say "Next (from the repo root; the hw services are in the hw profile):"
say "  docker compose -f docker/app/compose.yaml --profile hw run --rm capture --check /app/configs/default.yaml"
say "  docker compose -f docker/app/compose.yaml --profile hw up gui      # http://$GUI_HOST:$GUI_PORT/"
