#!/usr/bin/env bash
# Read-only check of host readiness for capture. Changes nothing. Thin wrapper: the
# checks live in tools/setup/host_setup.py (run it directly for --json, --scope, --apply).
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$here/tools/setup/host_setup.py" "$@"
rc=$?
echo "--- B210 and USB topology (want its own controller, no hub)"
lsusb | grep -E '2500:|fffe:' || echo "B210 not detected"
lsusb -t
exit $rc
