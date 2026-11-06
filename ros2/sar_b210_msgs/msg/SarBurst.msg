# One B210 SAR burst (one step of a sweep): metadata plus the raw IQ.
#
# header.stamp: host CLOCK_REALTIME at the burst centre, from the latest clock-sync pair
#   (host_realtime_ns + (centre_ticks - usrp_ticks) / master_clock_rate). Its error is bounded
#   by SarClockSync.round_trip_ns plus the USRP-to-host drift since that pair.
# header.frame_id: empty (antenna frames are b210-07).
std_msgs/Header header

uint64 index            # burst index in the run, 0-based, gap-free (bad bursts are marked, never dropped)
uint32 sweep
uint32 step
uint64 sample_offset    # offset of this burst's first sample in the run's iq.bin, in samples
uint64 num_samples      # complex samples in the burst
int64 usrp_ticks        # start of the first sample, in USRP master clock ticks
float64 usrp_time_s     # usrp_ticks / master clock rate
float64 tx_freq_hz
float64 rx_freq_hz
float64 lo_offset_hz
float64 tx_gain_db
float64 rx_gain_db
bool valid              # error_flags == 0
uint32 error_flags      # BurstError bits (burst_record.hpp)

# Interleaved I,Q int16: iq[2k] = I of sample k, iq[2k+1] = Q. Length 2*num_samples, or EMPTY
# when the node runs with publish_iq:=false (the raw IQ is then only in the run folder).
# The samples of an invalid burst that are missing are zero.
int16[] iq
