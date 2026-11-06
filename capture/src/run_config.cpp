#include "sar/run_config.hpp"

#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <set>
#include <sstream>
#include <stdexcept>

#include "sar/budget.hpp"

namespace sar {
namespace {

class Section {
public:
    Section(const YAML::Node& root, std::string name) : name_(std::move(name)), node_(root[name_]) {
        if (!node_.IsMap()) {
            throw std::runtime_error("config: missing section '" + name_ + "'");
        }
    }

    template <typename T>
    T required(const std::string& key) {
        const YAML::Node value = lookup(key);
        if (value.IsNull()) {
            throw std::runtime_error("config: " + name_ + "." + key + " must not be null");
        }
        return convert<T>(key, value);
    }

    template <typename T>
    std::optional<T> nullable(const std::string& key) {
        const YAML::Node value = lookup(key);
        if (value.IsNull()) {
            return std::nullopt;
        }
        return convert<T>(key, value);
    }

    void reject_unknown_keys() const {
        for (const auto& entry : node_) {
            const auto key = entry.first.as<std::string>();
            if (seen_.count(key) == 0) {
                throw std::runtime_error("config: unknown key " + name_ + "." + key);
            }
        }
    }

private:
    YAML::Node lookup(const std::string& key) {
        seen_.insert(key);
        const YAML::Node value = node_[key];
        if (!value.IsDefined()) {
            throw std::runtime_error("config: missing key " + name_ + "." + key);
        }
        return value;
    }

    template <typename T>
    T convert(const std::string& key, const YAML::Node& value) const {
        try {
            return value.as<T>();
        } catch (const YAML::Exception&) {
            throw std::runtime_error("config: bad value for " + name_ + "." + key);
        }
    }

    std::string name_;
    YAML::Node node_;
    std::set<std::string> seen_;
};

bool is_whole(double value) { return std::abs(value - std::round(value)) < 1e-6; }

}  // namespace

RunConfig parse_run_config(const std::string& yaml_text) {
    YAML::Node root;
    try {
        root = YAML::Load(yaml_text);
    } catch (const YAML::Exception& error) {
        throw std::runtime_error(std::string("config: invalid YAML: ") + error.what());
    }

    RunConfig c;
    c.source_text = yaml_text;

    Section device(root, "device");
    c.device.args = device.required<std::string>("args");
    c.device.master_clock_rate_hz = device.required<double>("master_clock_rate_hz");
    c.device.rx_timestamp_offset_ticks = device.required<int64_t>("rx_timestamp_offset_ticks");
    device.reject_unknown_keys();

    Section waveform(root, "waveform");
    c.waveform.sample_rate_hz = waveform.required<double>("sample_rate_hz");
    c.waveform.num_steps = waveform.required<int>("num_steps");
    c.waveform.step_spacing_hz = waveform.required<double>("step_spacing_hz");
    c.waveform.chirp_bandwidth_hz = waveform.required<double>("chirp_bandwidth_hz");
    c.waveform.chirp_length_s = waveform.required<double>("chirp_length_s");
    c.waveform.dwell_s = waveform.required<double>("dwell_s");
    c.waveform.taper_fraction = waveform.required<double>("taper_fraction");
    c.waveform.tx_amplitude = waveform.required<double>("tx_amplitude");
    waveform.reject_unknown_keys();

    Section rf(root, "rf");
    c.rf.center_freq_hz = rf.required<double>("center_freq_hz");
    c.rf.lo_offset_hz = rf.required<double>("lo_offset_hz");
    c.rf.tx_gain_db = rf.required<double>("tx_gain_db");
    c.rf.max_tx_gain_db = rf.required<double>("max_tx_gain_db");
    c.rf.rx_gain_db = rf.required<double>("rx_gain_db");
    c.rf.tx_antenna = rf.required<std::string>("tx_antenna");
    c.rf.rx_antenna = rf.required<std::string>("rx_antenna");
    c.rf.tx_mode = rf.required<std::string>("tx_mode");
    rf.reject_unknown_keys();

    Section band(root, "permitted_band");
    c.permitted_band.min_hz = band.nullable<double>("min_hz");
    c.permitted_band.max_hz = band.nullable<double>("max_hz");
    c.permitted_band.legal_basis = band.nullable<std::string>("legal_basis");
    c.permitted_band.max_avg_eirp_dbm = band.nullable<double>("max_avg_eirp_dbm");
    band.reject_unknown_keys();

    Section timing(root, "timing");
    c.timing.retune_s = timing.required<double>("retune_s");
    c.timing.settle_margin_s = timing.required<double>("settle_margin_s");
    c.timing.command_lead_s = timing.required<double>("command_lead_s");
    c.timing.lock_timeout_s = timing.required<double>("lock_timeout_s");
    c.timing.clock_sync_interval_s = timing.required<double>("clock_sync_interval_s");
    c.timing.max_run_s = timing.required<double>("max_run_s");
    c.timing.sync_mode = timing.required<std::string>("sync_mode");
    timing.reject_unknown_keys();

    Section antenna(root, "antenna");
    c.antenna.type = antenna.required<std::string>("type");
    c.antenna.gain_dbi = antenna.nullable<double>("gain_dbi");
    c.antenna.beamwidth_deg = antenna.required<double>("beamwidth_deg");
    antenna.reject_unknown_keys();

    Section platform(root, "platform");
    c.platform.planned_speed_mps = platform.required<double>("planned_speed_mps");
    platform.reject_unknown_keys();

    Section output(root, "output");
    c.output.dataset_root = output.nullable<std::string>("dataset_root");
    output.reject_unknown_keys();

    for (const auto& entry : root) {
        const auto key = entry.first.as<std::string>();
        static const std::set<std::string> known = {"device", "waveform", "rf", "permitted_band",
                                                    "timing", "antenna", "platform", "output"};
        if (known.count(key) == 0) {
            throw std::runtime_error("config: unknown section '" + key + "'");
        }
    }
    return c;
}

RunConfig load_run_config(const std::string& path) {
    std::ifstream file(path);
    if (!file) {
        throw std::runtime_error("config: cannot open " + path);
    }
    std::ostringstream text;
    text << file.rdbuf();
    return parse_run_config(text.str());
}

ConfigReport validate_config(const RunConfig& c) {
    ConfigReport report;
    const auto error = [&report](const std::string& message) { report.errors.push_back(message); };
    const auto& w = c.waveform;

    if (c.device.master_clock_rate_hz <= 0 || w.sample_rate_hz <= 0 || w.chirp_length_s <= 0 || w.dwell_s <= 0) {
        error("rates and durations must be positive");
        return report;
    }
    if (w.sample_rate_hz > c.device.master_clock_rate_hz ||
        !is_whole(c.device.master_clock_rate_hz / w.sample_rate_hz)) {
        error("master clock must be a whole multiple of the sample rate");
    }
    if (!is_whole(w.chirp_length_s * w.sample_rate_hz) || w.chirp_length_s * w.sample_rate_hz < 2) {
        error("chirp length must be a whole number of samples");
    }
    if (!is_whole(w.dwell_s / w.chirp_length_s) || w.dwell_s < w.chirp_length_s) {
        error("dwell must be a whole number of chirps, or leakage will not stay in a fixed range window");
    }
    if (w.num_steps < 1) {
        error("num_steps must be at least 1");
    }
    if (w.step_spacing_hz <= 0 || w.chirp_bandwidth_hz <= 0) {
        error("step spacing and chirp bandwidth must be positive");
    }
    if (std::abs(c.rf.lo_offset_hz) + w.chirp_bandwidth_hz / 2 > w.sample_rate_hz / 2) {
        error("chirp plus LO offset does not fit in the sample rate");
    }
    if (w.taper_fraction < 0 || w.taper_fraction > 0.5) {
        error("taper_fraction must be between 0 and 0.5");
    }
    if (w.tx_amplitude <= 0 || w.tx_amplitude > 1) {
        error("tx_amplitude must be in (0, 1]");
    }
    if (c.rf.tx_mode != "timed_burst") {
        error("rf.tx_mode must be timed_burst; continuous TX would break the EIRP budget");
    }
    if (c.timing.sync_mode != "host_clock" && c.timing.sync_mode != "pps") {
        error("timing.sync_mode must be host_clock or pps");
    }
    if (c.timing.retune_s < 0 || c.timing.settle_margin_s < 0 || c.timing.command_lead_s <= 0 || c.timing.lock_timeout_s <= 0 ||
        c.timing.clock_sync_interval_s <= 0 || c.timing.max_run_s <= 0) {
        error("timing values must be positive");
    }

    const auto& band = c.permitted_band;
    if (!band.min_hz || !band.max_hz || !band.legal_basis || !band.max_avg_eirp_dbm) {
        error("permitted_band is incomplete: capture will not run without a legal basis");
    } else if (w.num_steps >= 1) {
        const double half_span = (w.num_steps - 1) * w.step_spacing_hz / 2 + w.chirp_bandwidth_hz / 2;
        if (c.rf.center_freq_hz - half_span < *band.min_hz || c.rf.center_freq_hz + half_span > *band.max_hz) {
            std::ostringstream message;
            message << "sweep occupies " << (c.rf.center_freq_hz - half_span) / 1e6 << "-"
                    << (c.rf.center_freq_hz + half_span) / 1e6 << " MHz, outside permitted_band";
            error(message.str());
        }
    }
    if (!c.antenna.gain_dbi) {
        error("antenna.gain_dbi is needed for the EIRP check");
    }
    if (c.rf.tx_gain_db > c.rf.max_tx_gain_db) {
        error("rf.tx_gain_db is above rf.max_tx_gain_db");
    }

    if (report.errors.empty()) {
        const Budget budget = compute_budget(c);
        if (c.platform.planned_speed_mps > budget.max_speed_lambda_over_3_mps) {
            std::ostringstream message;
            message << "planned speed " << c.platform.planned_speed_mps << " m/s is over the lambda/3 limit of "
                    << budget.max_speed_lambda_over_3_mps << " m/s";
            report.warnings.push_back(message.str());
        }
    }
    return report;
}

std::size_t chirp_samples(const RunConfig& c) {
    return static_cast<std::size_t>(std::llround(c.waveform.chirp_length_s * c.waveform.sample_rate_hz));
}

std::size_t dwell_chirps(const RunConfig& c) {
    return static_cast<std::size_t>(std::llround(c.waveform.dwell_s / c.waveform.chirp_length_s));
}

std::size_t burst_samples(const RunConfig& c) { return chirp_samples(c) * dwell_chirps(c); }

int64_t ticks_per_sample(const RunConfig& c) {
    return std::llround(c.device.master_clock_rate_hz / c.waveform.sample_rate_hz);
}

int64_t seconds_to_ticks(const RunConfig& c, double seconds) {
    return std::llround(seconds * c.device.master_clock_rate_hz);
}

double step_center_freq_hz(const RunConfig& c, int step) {
    return c.rf.center_freq_hz + (step - (c.waveform.num_steps - 1) / 2.0) * c.waveform.step_spacing_hz;
}

}  // namespace sar
