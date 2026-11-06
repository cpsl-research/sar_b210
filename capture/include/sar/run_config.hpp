#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace sar {

// Mirrors configs/default.yaml and src/b210_sar/config.py.
struct RunConfig {
    struct Device {
        std::string args;
        double master_clock_rate_hz = 0;
        int64_t rx_timestamp_offset_ticks = 0;  // constant device pipeline offset
    } device;

    struct Waveform {
        double sample_rate_hz = 0;
        int num_steps = 0;
        double step_spacing_hz = 0;
        double chirp_bandwidth_hz = 0;
        double chirp_length_s = 0;
        double dwell_s = 0;
        double taper_fraction = 0;
        double tx_amplitude = 0;
    } waveform;

    struct Rf {
        double center_freq_hz = 0;
        double lo_offset_hz = 0;
        double tx_gain_db = 0;
        double max_tx_gain_db = 0;
        double rx_gain_db = 0;
        std::string tx_antenna;
        std::string rx_antenna;
        std::string tx_mode;
    } rf;

    struct PermittedBand {
        std::optional<double> min_hz;
        std::optional<double> max_hz;
        std::optional<std::string> legal_basis;
        std::optional<double> max_avg_eirp_dbm;
    } permitted_band;

    struct Timing {
        double retune_s = 0;
        double settle_margin_s = 0;
        double command_lead_s = 0;
        double lock_timeout_s = 0;
        double clock_sync_interval_s = 0;
        double max_run_s = 0;
        std::string sync_mode;
    } timing;

    struct Antenna {
        std::string type;
        std::optional<double> gain_dbi;
        double beamwidth_deg = 0;
    } antenna;

    struct Platform {
        double planned_speed_mps = 0;
    } platform;

    struct Output {
        std::optional<std::string> dataset_root;
    } output;

    std::string source_text;  // the YAML as loaded, copied verbatim into each dataset
};

// Throws std::runtime_error naming the offending key on a missing, unknown or mistyped value.
RunConfig parse_run_config(const std::string& yaml_text);
RunConfig load_run_config(const std::string& path);

struct ConfigReport {
    std::vector<std::string> errors;    // capture refuses to run
    std::vector<std::string> warnings;  // capture runs and logs them
};
ConfigReport validate_config(const RunConfig& config);

// Derived sizes. Only meaningful for a config with no validation errors.
std::size_t chirp_samples(const RunConfig& config);
std::size_t dwell_chirps(const RunConfig& config);
std::size_t burst_samples(const RunConfig& config);
int64_t ticks_per_sample(const RunConfig& config);
int64_t seconds_to_ticks(const RunConfig& config, double seconds);
double step_center_freq_hz(const RunConfig& config, int step);

}  // namespace sar
