#!/usr/bin/env bash
# Read-only check of host readiness for capture. Changes nothing.
echo "governor (want performance): $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"
echo "usb autosuspend (want -1):   $(cat /sys/module/usbcore/parameters/autosuspend 2>/dev/null)"
echo "rtprio limit (want > 0):     $(ulimit -r)"
echo "mlock limit KB (want > 1.9 GB or unlimited): $(ulimit -l)"
echo "AC power online:             $(cat /sys/class/power_supply/AC*/online /sys/class/power_supply/ADP*/online 2>/dev/null | head -1)"
if ls "${UHD_IMAGES_DIR:-/usr/share/uhd/images}/usrp_b210_fpga.bin" >/dev/null 2>&1; then
  echo "B210 FPGA image:             found"
else
  echo "B210 FPGA image:             not found; set UHD_IMAGES_DIR, or run: uhd_images_downloader -t b2xx -i DIR"
fi
echo "--- B210 and USB topology (want its own controller, no hub)"
lsusb | grep -E '2500:|fffe:' || echo "B210 not detected"
lsusb -t
