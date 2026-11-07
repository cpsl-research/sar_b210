#include "sar/run_config.hpp"

#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstdio>
#include <filesystem>
#include <map>
#include <mutex>
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
    RunConfig config = parse_run_config(text.str());
    config.source_path = path;
    return config;
}

const HwLimitRow* HwLimits::find(const std::string& key) const {
    for (const auto& row : rows) {
        if (row.key == key) {
            return &row;
        }
    }
    return nullptr;
}

HwLimits load_hw_limits(const std::string& path) {
    YAML::Node root;
    try {
        root = YAML::LoadFile(path);
    } catch (const YAML::Exception& error) {
        throw std::runtime_error("hw_limits: cannot read " + path + ": " + error.what());
    }
    HwLimits limits;
    limits.path = path;
    const YAML::Node rows = root["limits"];
    if (!rows.IsSequence()) {
        throw std::runtime_error("hw_limits: " + path + " has no 'limits' list");
    }
    try {
        for (const auto& node : rows) {
            HwLimitRow row;
            row.key = node["key"].as<std::string>();
            row.unit = node["unit"].as<std::string>();
            row.kind = node["kind"].as<std::string>();
            row.severity = node["severity"].as<std::string>();
            if (node["min"] && !node["min"].IsNull()) row.min = node["min"].as<double>();
            if (node["max"] && !node["max"].IsNull()) row.max = node["max"].as<double>();
            if (node["allowed"] && node["allowed"].IsSequence()) {
                for (const auto& item : node["allowed"]) row.allowed.push_back(item.as<std::string>());
            }
            limits.rows.push_back(std::move(row));
        }
    } catch (const YAML::Exception& error) {
        throw std::runtime_error("hw_limits: malformed row in " + path + ": " + error.what());
    }
    return limits;
}

std::string find_hw_limits_path(const std::string& config_path) {
    namespace fs = std::filesystem;
    std::vector<fs::path> candidates;
    if (const char* env = std::getenv("B210_SAR_CONFIG_DIR"); env != nullptr && *env != '\0') {
        candidates.push_back(fs::path(env) / "hw_limits.yaml");
    }
    if (!config_path.empty()) {
        std::error_code ec;
        const fs::path dir = fs::absolute(fs::path(config_path), ec).parent_path();
        candidates.push_back(dir / "hw_limits.yaml");
        candidates.push_back(dir.parent_path() / "hw_limits.yaml");
    }
#ifdef SAR_DEFAULT_CONFIG_DIR
    candidates.push_back(fs::path(SAR_DEFAULT_CONFIG_DIR) / "hw_limits.yaml");
#endif
    for (const auto& candidate : candidates) {
        std::error_code ec;
        if (fs::is_regular_file(candidate, ec)) {
            return candidate.string();
        }
    }
    return "";
}

namespace {

// Same text as b210_sar.config._fmt (Python's .6g is C's %.6g).
std::string fmt_value(double v, const std::string& unit) {
    char buf[64];
    if (unit == "Hz") {
        std::snprintf(buf, sizeof buf, "%.6g MHz", v / 1e6);
    } else if (unit == "s") {
        std::snprintf(buf, sizeof buf, "%.6g ms", v * 1e3);
    } else {
        std::snprintf(buf, sizeof buf, "%.6g %s", v, unit.c_str());
    }
    return buf;
}

std::string g(double v) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%g", v);
    return buf;
}

std::string fixed2(double v) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.2f", v);
    return buf;
}

}  // namespace

ConfigReport validate_config(const RunConfig& c) {
    const std::string path = find_hw_limits_path(c.source_path);
    HwLimits limits;
    try {
        if (path.empty()) {
            throw std::runtime_error("hw_limits.yaml not found (looked in $B210_SAR_CONFIG_DIR, next to the config, "
                                     "and the build-time configs dir)");
        }
        static std::mutex mutex;
        static std::map<std::string, HwLimits> cache;
        const std::lock_guard<std::mutex> lock(mutex);
        auto it = cache.find(path);
        if (it == cache.end()) {
            it = cache.emplace(path, load_hw_limits(path)).first;
        }
        limits = it->second;
    } catch (const std::exception& e) {
        ConfigReport report;
        report.issues.push_back({"hw_limits", "error", e.what(), "", "library"});
        report.errors.push_back(std::string("hw_limits: ") + e.what());
        return report;
    }
    return validate_config(c, limits);
}

ConfigReport validate_config(const RunConfig& c, const HwLimits& limits) {
    ConfigReport report;
    const auto add = [&report](const std::string& field, const std::string& message, const std::string& severity = "error",
                               const std::string& key = "", const std::string& kind = "library") {
        report.issues.push_back({field, severity, message, key, kind});
        (severity == "error" ? report.errors : report.warnings).push_back(field + ": " + message);
    };
    const auto& w = c.waveform;
    const auto& rf = c.rf;
    const auto& t = c.timing;
    const auto& d = c.device;

    // --- hardware rows. A missing row is an error, never a silent pass.
    const auto row = [&](const std::string& key) -> const HwLimitRow* {
        const HwLimitRow* r = limits.find(key);
        if (r == nullptr) {
            add("hw_limits", "row " + key + " missing from " + limits.path);
        }
        return r;
    };
    const auto hw = [&](const std::string& key, const std::string& field, double value, const std::string& what = "") {
        const HwLimitRow* r = row(key);
        if (r == nullptr) {
            return;
        }
        const std::string label = what.empty() ? field : what;
        if (r->min && value < *r->min) {
            add(field, label + " " + fmt_value(value, r->unit) + " is below the " + key + " minimum " +
                           fmt_value(*r->min, r->unit) + " (" + r->kind + " limit)",
                r->severity, key, r->kind);
        }
        if (r->max && value > *r->max) {
            add(field, label + " " + fmt_value(value, r->unit) + " is above the " + key + " maximum " +
                           fmt_value(*r->max, r->unit) + " (" + r->kind + " limit)",
                r->severity, key, r->kind);
        }
    };

    // --- internal consistency
    if (d.master_clock_rate_hz <= 0 || w.sample_rate_hz <= 0 || w.chirp_length_s <= 0 || w.dwell_s <= 0) {
        add("device.master_clock_rate_hz", "rates and durations must be positive");
        return report;
    }
    if (w.sample_rate_hz > d.master_clock_rate_hz || !is_whole(d.master_clock_rate_hz / w.sample_rate_hz)) {
        add("waveform.sample_rate_hz", "master clock must be a whole multiple of the sample rate");
    }
    if (!is_whole(w.chirp_length_s * w.sample_rate_hz) || w.chirp_length_s * w.sample_rate_hz < 2) {
        add("waveform.chirp_length_s", "chirp length must be a whole number of samples");
    }
    if (!is_whole(w.dwell_s / w.chirp_length_s) || w.dwell_s < w.chirp_length_s) {
        add("waveform.dwell_s", "dwell must be a whole number of chirps, or leakage will not stay in a fixed range window");
    }
    if (w.num_steps < 1) {
        add("waveform.num_steps", "num_steps must be at least 1");
    }
    if (w.step_spacing_hz <= 0) {
        add("waveform.step_spacing_hz", "step spacing must be positive");
    }
    if (w.chirp_bandwidth_hz <= 0) {
        add("waveform.chirp_bandwidth_hz", "chirp bandwidth must be positive");
    }
    if (std::abs(rf.lo_offset_hz) + w.chirp_bandwidth_hz / 2 > w.sample_rate_hz / 2) {
        add("rf.lo_offset_hz", "chirp plus LO offset does not fit in the sample rate");
    }
    if (w.taper_fraction < 0 || w.taper_fraction > 0.5) {
        add("waveform.taper_fraction", "taper_fraction must be between 0 and 0.5");
    }
    if (w.tx_amplitude <= 0 || w.tx_amplitude > 1) {
        add("waveform.tx_amplitude", "tx_amplitude must be in (0, 1]");
    }
    if (rf.tx_mode != "timed_burst") {
        add("rf.tx_mode", "rf.tx_mode must be timed_burst; continuous TX would break the EIRP budget");
    }
    if (t.sync_mode != "host_clock" && t.sync_mode != "pps") {
        add("timing.sync_mode", "timing.sync_mode must be host_clock or pps");
    }
    if (t.retune_s < 0 || t.settle_margin_s < 0) {
        add(t.retune_s < 0 ? "timing.retune_s" : "timing.settle_margin_s", "timing values must not be negative");
    }
    const std::pair<const char*, double> positive[] = {{"command_lead_s", t.command_lead_s},
                                                       {"lock_timeout_s", t.lock_timeout_s},
                                                       {"clock_sync_interval_s", t.clock_sync_interval_s},
                                                       {"max_run_s", t.max_run_s}};
    for (const auto& [name, value] : positive) {
        if (value <= 0) {
            add(std::string("timing.") + name, "timing values must be positive");
        }
    }

    const auto& band = c.permitted_band;
    if (!band.min_hz || !band.max_hz || !band.legal_basis || !band.max_avg_eirp_dbm) {
        add("permitted_band", "permitted_band is incomplete: capture will not run without a legal basis");
    } else if (w.num_steps >= 1) {
        const double half_span = (w.num_steps - 1) * w.step_spacing_hz / 2 + w.chirp_bandwidth_hz / 2;
        const double lo = rf.center_freq_hz - half_span;
        const double hi = rf.center_freq_hz + half_span;
        if (lo < *band.min_hz || hi > *band.max_hz) {
            add("waveform", "sweep occupies " + fixed2(lo / 1e6) + "-" + fixed2(hi / 1e6) + " MHz, outside permitted_band " +
                                fixed2(*band.min_hz / 1e6) + "-" + fixed2(*band.max_hz / 1e6) + " MHz");
        }
    }
    if (!c.antenna.gain_dbi) {
        add("antenna.gain_dbi", "antenna.gain_dbi is needed for the EIRP check");
    }
    if (rf.tx_gain_db > rf.max_tx_gain_db) {
        add("rf.tx_gain_db", "rf.tx_gain_db " + g(rf.tx_gain_db) + " dB is above this config's rf.max_tx_gain_db " +
                                 g(rf.max_tx_gain_db) + " dB");
    }

    // --- B210 hardware limits (hw_limits.yaml; same keys and order as b210_sar.config.validate)
    if (w.num_steps >= 1) {
        for (const char* key : {"rf.center_freq_hz.rx_tune", "rf.center_freq_hz.tx_tune"}) {
            const int last = w.num_steps - 1;
            for (int i : {0, last}) {
                const std::size_t before = report.issues.size();
                hw(key, "rf.center_freq_hz", step_center_freq_hz(c, i), "step " + std::to_string(i) + " centre");
                if (report.issues.size() > before) {
                    break;
                }
                if (last == 0) {
                    break;
                }
            }
        }
    }
    hw("rf.tx_gain_db", "rf.tx_gain_db", rf.tx_gain_db);
    hw("rf.tx_gain_db", "rf.max_tx_gain_db", rf.max_tx_gain_db);
    hw("rf.rx_gain_db", "rf.rx_gain_db", rf.rx_gain_db);
    hw("device.master_clock_rate_hz.range_1ch", "device.master_clock_rate_hz", d.master_clock_rate_hz);
    hw("device.master_clock_rate_hz.recommended_max", "device.master_clock_rate_hz", d.master_clock_rate_hz);
    hw("waveform.sample_rate_hz.range_1ch", "waveform.sample_rate_hz", w.sample_rate_hz);
    hw("rf.analog_bandwidth_hz", "waveform.chirp_bandwidth_hz", w.chirp_bandwidth_hz);
    if (const HwLimitRow* bw = limits.find("rf.analog_bandwidth_hz"); bw != nullptr && bw->max && w.sample_rate_hz > *bw->max) {
        add("waveform.sample_rate_hz", "sample rate " + fmt_value(w.sample_rate_hz, "Hz") + " exceeds the " +
                                           fmt_value(*bw->max, "Hz") + " analog bandwidth; the filter is clipped, usable "
                                           "bandwidth stays at the limit",
            "warning", "rf.analog_bandwidth_hz", "uhd");
    }
    for (const auto& [key, value] : {std::pair<const char*, const std::string&>{"rf.tx_antenna", rf.tx_antenna},
                                     std::pair<const char*, const std::string&>{"rf.rx_antenna", rf.rx_antenna}}) {
        const HwLimitRow* r = row(key);
        if (r != nullptr && std::find(r->allowed.begin(), r->allowed.end(), value) == r->allowed.end()) {
            std::string list;
            for (const auto& a : r->allowed) {
                list += (list.empty() ? "" : ", ") + a;
            }
            add(key, "'" + value + "' is not a valid port; allowed (" + key + "): " + list, r->severity, key, r->kind);
        }
    }
    hw("timing.retune_s", "timing.retune_s", t.retune_s);
    for (const auto& r : limits.rows) {
        if (r.kind == "usb") {
            report.not_checked.push_back(r.key);
        }
    }

    if (report.errors.empty()) {
        const Budget budget = compute_budget(c);
        if (c.platform.planned_speed_mps > budget.max_speed_lambda_over_3_mps) {
            add("platform.planned_speed_mps", "planned speed " + fixed2(c.platform.planned_speed_mps) +
                                                  " m/s is over the lambda/3 limit of " +
                                                  fixed2(budget.max_speed_lambda_over_3_mps) + " m/s",
                "warning");
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
