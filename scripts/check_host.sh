#!/usr/bin/env bash
# Read-only check of host readiness for capture. Changes nothing.
echo "governor (want performance): $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"
# Autosuspend is fine if the attached Ettus device(s) have power/control=on (per-device udev rule),
# else fall back to the global usbcore parameter (-1 = off).
b210_ctl=""
for d in /sys/bus/usb/devices/*; do
  [ "$(cat "$d/idVendor" 2>/dev/null)" = "2500" ] || continue
  b210_ctl="$b210_ctl $(basename "$d")=$(cat "$d/power/control" 2>/dev/null)"
done
if [ -n "$b210_ctl" ]; then
  case "$b210_ctl" in
    *=auto*|*=) verdict="FAIL: autosuspend not disabled on:$b210_ctl" ;;
    *) verdict="ok (per-device power/control=on:$b210_ctl)" ;;
  esac
  echo "usb autosuspend (want power/control=on): $verdict"
else
  echo "usb autosuspend (want -1, no B210 attached): $(cat /sys/module/usbcore/parameters/autosuspend 2>/dev/null)"
fi
echo "rtprio limit (want > 0):     $(ulimit -r)"
echo "mlock limit KB (want > 1.9 GB or unlimited): $(ulimit -l)"
echo "AC power online:             $(cat /sys/class/power_supply/AC*/online /sys/class/power_supply/ADP*/online 2>/dev/null | head -1)"
if ls "${UHD_IMAGES_DIR:-/usr/share/uhd/images}/usrp_b210_fpga.bin" >/dev/null 2>&1; then
  echo "B210 FPGA image:             found"
else
  echo "B210 FPGA image:             not found; set UHD_IMAGES_DIR (only for a non-default dir), or run: uhd_images_downloader -t b2xx -i DIR"
fi
echo "--- B210 and USB topology (want its own controller, no hub)"
lsusb | grep -E '2500:|fffe:' || echo "B210 not detected"
lsusb -t
