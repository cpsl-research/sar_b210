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
        double tx_rx_separation_m = 0;
    } antenna;

    struct Platform {
        double planned_speed_mps = 0;
    } platform;

    struct Output {
        std::optional<std::string> dataset_root;
    } output;

    std::string source_text;  // the YAML as loaded, copied verbatim into each dataset
    std::string source_path;  // file it was loaded from ("" for parse_run_config); where hw_limits.yaml is looked for
};

// Throws std::runtime_error naming the offending key on a missing, unknown or mistyped value.
RunConfig parse_run_config(const std::string& yaml_text);
RunConfig load_run_config(const std::string& path);

// One row of configs/hw_limits.yaml: a B210 hardware limit (kind chip|uhd|usb|library; severity error|warning).
struct HwLimitRow {
    std::string key;
    std::string unit;
    std::string kind;
    std::string severity;
    std::optional<double> min;
    std::optional<double> max;
    std::vector<std::string> allowed;  // port-name rows only
};

struct HwLimits {
    std::string path;
    std::vector<HwLimitRow> rows;
    const HwLimitRow* find(const std::string& key) const;  // nullptr if absent
};

// The same table b210_sar.config reads. Throws std::runtime_error if unreadable or malformed.
HwLimits load_hw_limits(const std::string& path);

// Where hw_limits.yaml lives, first match wins: $B210_SAR_CONFIG_DIR; the directory of `config_path` and its
// parent (configs/ and configs/user/; the ROS package installs it next to default.yaml in share/.../config);
// the repo's configs/ dir baked in at build time. Returns "" if none exists.
std::string find_hw_limits_path(const std::string& config_path = "");

struct ConfigIssue {
    std::string field;  // dotted config field, as b210_sar.config.Issue.field
    std::string severity;  // error | warning
    std::string message;
    std::string limit_key;  // hw_limits.yaml row, "" when not a hardware limit
    std::string kind;       // library | chip | uhd | usb
};

struct ConfigReport {
    std::vector<std::string> errors;    // capture refuses to run; "field: message"
    std::vector<std::string> warnings;  // capture runs and logs them; "field: message"
    std::vector<ConfigIssue> issues;    // the same, structured (errors and warnings, Python order)
    std::vector<std::string> not_checked;  // usb rows: need a live device, never evaluated offline
};

// Internal consistency plus the B210 hardware limits; mirrors b210_sar.config.validate. The one-argument form finds
// hw_limits.yaml via find_hw_limits_path(config.source_path); if it cannot be found or read, that is an error
// (capture does not run without the hardware table).
ConfigReport validate_config(const RunConfig& config);
ConfigReport validate_config(const RunConfig& config, const HwLimits& limits);

// Derived sizes. Only meaningful for a config with no validation errors.
std::size_t chirp_samples(const RunConfig& config);
std::size_t dwell_chirps(const RunConfig& config);
std::size_t burst_samples(const RunConfig& config);
int64_t ticks_per_sample(const RunConfig& config);
int64_t seconds_to_ticks(const RunConfig& config, double seconds);
double step_center_freq_hz(const RunConfig& config, int step);

}  // namespace sar
