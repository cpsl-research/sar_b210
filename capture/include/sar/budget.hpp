#pragma once

#include <cstddef>
#include <string>

#include "sar/run_config.hpp"

namespace sar {

// Derived budget, printed before every run. Recomputed from the config, so it follows N, dwell and settle.
struct Budget {
    double total_bandwidth_hz;
    double range_resolution_m;
    double step_time_s;
    double sweep_time_s;
    double sweep_rate_hz;
    double wavelength_m;
    double spacing_lambda_over_3_m;
    double spacing_for_beamwidth_m;
    double max_speed_lambda_over_3_mps;
    double max_speed_for_beamwidth_mps;
    double position_accuracy_m;
    double data_per_sweep_bytes;
    double data_rate_bytes_per_s;
    double tx_duty_cycle;
    double max_avg_conducted_dbm;    // from the EIRP cap and antenna gain; NaN if either is unset
    double max_burst_conducted_dbm;  // the same, if averaging over the duty cycle applies
    std::size_t ram_slots;           // bursts held: max_run_s + 25%
    double ram_bytes;
};

Budget compute_budget(const RunConfig& config);
std::string format_budget(const RunConfig& config, const Budget& budget);

}  // namespace sar
