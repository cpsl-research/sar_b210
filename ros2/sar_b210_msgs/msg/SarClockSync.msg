# One sample of USRP time against the host clocks (the capture library's ClockSyncRecord).
# Host times are the midpoint of the USRP time query; round_trip_ns bounds the error.
int64 usrp_ticks
int64 host_monotonic_ns
int64 host_realtime_ns
int64 round_trip_ns
