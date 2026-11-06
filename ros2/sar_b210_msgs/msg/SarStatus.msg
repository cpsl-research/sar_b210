# Node and capture status, 2 Hz, also while idle.
std_msgs/Header header   # stamp = host realtime at publication

uint8 STATE_IDLE=0
uint8 STATE_PREFLIGHT=1   # config validated, opening the radio and checking every step tunes/locks
uint8 STATE_RUNNING=2
uint8 STATE_WRITING=3     # run ended, writing the run folder
uint8 STATE_DONE=4
uint8 STATE_FAILED=5
uint8 state
string state_name

bool transmit             # the current or last run radiates
string config_path
string run_dir            # run folder written at the end ('' until a run is set up, or if write_run_folder is false)
string fatal_error        # why the last run failed or ended early ('' if none)

uint64 bursts
uint64 invalid_bursts
uint64 ram_slots          # capture RAM capacity in bursts; the run ends when reached
float32 ram_fill          # bursts / ram_slots
float64 elapsed_s         # run start to the latest burst, USRP time
float64 max_run_s
float64 burst_rate_hz     # bursts / elapsed_s

uint64 sweeps             # sweep periods measured
float64 budget_sweep_period_s   # the config's computed sweep time
float64 last_sweep_period_s
float64 mean_sweep_period_s
float64 max_sweep_period_s
float64 budget_step_time_s
float64 max_burst_gap_s
uint32 last_error_flags   # flags of the latest invalid burst
string last_error_names   # names of those flags, space separated

int64 clock_round_trip_ns       # latest clock-sync round trip
int64 max_clock_round_trip_ns   # worst round trip this run

# Capture-library callback health (a callback that is slow delays the next retune).
uint64 callback_errors
float64 max_callback_s

# Publish path (callback -> bounded queue -> publisher thread -> b210/bursts).
bool publish_iq                 # false: SarBurst.iq is empty
uint32 publish_queue_capacity   # bursts the queue can hold
uint32 publish_queue_fill
uint32 publish_queue_max_fill
uint64 published_bursts
uint64 publish_drops            # bursts that were not published because the queue was full
