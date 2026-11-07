// Capture tests that need no hardware. A fake radio injects each fault the capture must handle.
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>

#include "sar/budget.hpp"
#include "sar/capture_session.hpp"
#include "sar/chirp.hpp"
#include "sar/ram_buffer.hpp"
#include "sar/run_config.hpp"
#include "sar/sha256.hpp"

#include <yaml-cpp/yaml.h>

namespace fs = std::filesystem;
using namespace sar;

namespace {

int g_failures = 0;

#define CHECK(condition)                                                                \
    do {                                                                                \
        if (!(condition)) {                                                             \
            std::cerr << "  FAILED " << __FILE__ << ":" << __LINE__ << ": " #condition "\n"; \
            ++g_failures;                                                               \
        }                                                                               \
    } while (false)

#define CHECK_NEAR(a, b, tolerance) CHECK(std::abs((a) - (b)) <= (tolerance))

template <typename Fn>
bool throws(Fn fn, const std::string& fragment = "") {
    try {
        fn();
    } catch (const std::exception& error) {
        return std::string(error.what()).find(fragment) != std::string::npos;
    }
    return false;
}

RunConfig default_config() { return load_run_config(std::string(SAR_SOURCE_DIR) + "/configs/default.yaml"); }

RunConfig fast_config(double run_s = 0.1) {
    RunConfig c = default_config();
    c.device.rx_timestamp_offset_ticks = 0;
    c.timing.retune_s = 0;
    c.timing.settle_margin_s = 0.001;
    c.timing.command_lead_s = 0.0005;
    c.timing.lock_timeout_s = 0.005;
    c.timing.clock_sync_interval_s = 0.02;
    c.timing.max_run_s = run_s;
    return c;
}

struct FakeFaults {
    std::map<int, uint32_t> rx_flags;  // receive call number -> flags
    std::map<int, uint32_t> tx_flags;  // send call number -> flags
    std::set<int> unlocked_tunes;      // tune call numbers that never lock
    int rx_throw_at = -1;
    bool wrong_rx_tick = false;
    double tune_offset_hz = 0;
};

// Records what capture asked of it. Time runs on the real clock, as it does on hardware.
class FakeRadio final : public Radio {
public:
    struct Log {
        std::mutex mutex;
        std::vector<int64_t> tx_ticks;
        std::vector<int64_t> rx_ticks;
        std::vector<std::size_t> tx_counts;
        std::vector<double> tx_freqs;
        std::vector<int> tx_tune_numbers;
        std::vector<Sample> first_tx_burst;
        std::atomic<int> tunes{0};
        std::atomic<int> rx_active{0};
        std::atomic<int> tune_during_burst{0};
    };

    FakeRadio(double tick_rate, FakeFaults faults, std::shared_ptr<Log> log)
        : tick_rate_(tick_rate), faults_(std::move(faults)), log_(std::move(log)), start_(std::chrono::steady_clock::now()) {}

    Tuned tune(double freq_hz, double) override {
        if (log_->rx_active > 0) {
            ++log_->tune_during_burst;
        }
        current_tune_ = log_->tunes++;
        locked_ = faults_.unlocked_tunes.count(current_tune_) == 0;
        current_freq_ = freq_hz + faults_.tune_offset_hz;
        return {current_freq_, current_freq_};
    }

    bool lo_locked() override { return locked_; }

    int64_t time_now_ticks() override {
        const std::chrono::duration<double> elapsed = std::chrono::steady_clock::now() - start_;
        return std::llround(elapsed.count() * tick_rate_);
    }

    uint32_t send_burst(const Sample* samples, std::size_t count, int64_t start_tick) override {
        int number;
        {
            std::lock_guard<std::mutex> lock(log_->mutex);
            number = static_cast<int>(log_->tx_ticks.size());
            log_->tx_ticks.push_back(start_tick);
            log_->tx_counts.push_back(count);
            log_->tx_freqs.push_back(current_freq_);
            log_->tx_tune_numbers.push_back(current_tune_);
            if (number == 0) {
                log_->first_tx_burst.assign(samples, samples + count);
            }
        }
        const auto found = faults_.tx_flags.find(number);
        return found == faults_.tx_flags.end() ? kBurstOk : found->second;
    }

    Received receive_burst(Sample* samples, std::size_t count, int64_t start_tick) override {
        ++log_->rx_active;
        int number;
        {
            std::lock_guard<std::mutex> lock(log_->mutex);
            number = static_cast<int>(log_->rx_ticks.size());
            log_->rx_ticks.push_back(start_tick);
        }
        if (number == faults_.rx_throw_at) {
            --log_->rx_active;
            throw std::runtime_error("device unplugged");
        }
        std::this_thread::sleep_for(std::chrono::microseconds(200));

        Received result{kBurstOk, count, faults_.wrong_rx_tick ? start_tick + 1 : start_tick};
        const auto found = faults_.rx_flags.find(number);
        if (found != faults_.rx_flags.end()) {
            result.error_flags = found->second;
            result.num_samples = count / 2;  // a fault leaves the burst part filled
        }
        for (std::size_t i = 0; i < result.num_samples; ++i) {
            samples[i] = Sample(static_cast<int16_t>(number), static_cast<int16_t>(i & 0x7fff));
        }
        --log_->rx_active;
        return result;
    }

    double tx_gain_db() override { return 20; }
    double rx_gain_db() override { return 30; }

private:
    double tick_rate_;
    FakeFaults faults_;
    std::shared_ptr<Log> log_;
    std::chrono::steady_clock::time_point start_;
    std::atomic<bool> locked_{true};
    std::atomic<double> current_freq_{0};
    std::atomic<int> current_tune_{0};
};

struct Run {
    std::shared_ptr<FakeRadio::Log> radio_log = std::make_shared<FakeRadio::Log>();
    std::shared_ptr<RunLog> run_log = std::make_shared<RunLog>();
    std::unique_ptr<CaptureSession> session;
};

Run make_run(const RunConfig& config, FakeFaults faults = {}, bool transmit = true, CaptureOptions options = {}) {
    Run run;
    options.transmit = transmit;
    run.session = std::make_unique<CaptureSession>(
        config, std::make_unique<FakeRadio>(config.device.master_clock_rate_hz, std::move(faults), run.radio_log),
        run.run_log, std::move(options));
    return run;
}

Run run_to_end(const RunConfig& config, FakeFaults faults = {}, bool transmit = true) {
    Run run = make_run(config, std::move(faults), transmit);
    run.session->start();
    run.session->wait();
    return run;
}

std::string read_text(const fs::path& path) {
    std::ifstream file(path);
    std::ostringstream text;
    text << file.rdbuf();
    return text.str();
}

struct TempDir {
    fs::path path = fs::temp_directory_path() / ("sar_test_" + std::to_string(::getpid()));
    TempDir() { fs::remove_all(path); }
    ~TempDir() { fs::remove_all(path); }
};

// ----- config -----

void test_default_config_is_valid() {
    const ConfigReport report = validate_config(default_config());
    for (const auto& error : report.errors) {
        std::cerr << "  " << error << '\n';
    }
    CHECK(report.errors.empty());
    CHECK(report.warnings.empty());
}

void test_parser_rejects_bad_files() {
    const std::string good = default_config().source_text;
    CHECK(throws([&] { parse_run_config(good + "\nextra: 1\n"); }, "unknown section"));
    std::string misspelled = good;
    misspelled.replace(misspelled.find("dwell_s"), 7, "dwel_s");
    CHECK(throws([&] { parse_run_config(misspelled); }, "dwell_s"));
    std::string mistyped = good;
    const auto at = mistyped.find("num_steps: 2");
    mistyped.replace(at, 12, "num_steps: x");
    CHECK(throws([&] { parse_run_config(mistyped); }, "num_steps"));
    CHECK(throws([] { load_run_config("/nonexistent.yaml"); }, "cannot open"));
}

bool has_error(const RunConfig& config, const std::string& fragment) {
    for (const auto& error : validate_config(config).errors) {
        if (error.find(fragment) != std::string::npos) {
            return true;
        }
    }
    return false;
}

void test_validation_blocks_unsafe_configs() {
    RunConfig c = default_config();
    c.rf.center_freq_hz = 2450e6;
    CHECK(has_error(c, "outside permitted_band"));

    c = default_config();
    c.waveform.num_steps = 3;
    CHECK(has_error(c, "outside permitted_band"));

    c = default_config();
    c.permitted_band.legal_basis.reset();
    CHECK(has_error(c, "legal basis"));

    c = default_config();
    c.antenna.gain_dbi.reset();
    CHECK(has_error(c, "gain_dbi"));

    c = default_config();
    c.rf.tx_gain_db = c.rf.max_tx_gain_db + 1;
    CHECK(has_error(c, "max_tx_gain_db"));

    c = default_config();
    c.rf.tx_mode = "continuous_loop";
    CHECK(has_error(c, "timed_burst"));

    c = default_config();
    c.waveform.dwell_s = 0.00105 + 0.000001;
    CHECK(has_error(c, "whole number of chirps"));

    c = default_config();
    c.platform.planned_speed_mps = 5;
    CHECK(!validate_config(c).warnings.empty());
}

// ----- first-SAR config and antenna.tx_rx_separation_m (b210-30) -----

void test_sar_first_config_and_separation() {
    const RunConfig c = load_run_config(std::string(SAR_SOURCE_DIR) + "/configs/sar_first_2x40.yaml");
    CHECK(c.waveform.num_steps == 2);
    CHECK_NEAR(c.waveform.chirp_bandwidth_hz, 40e6, 1);
    CHECK_NEAR(c.waveform.step_spacing_hz, 38e6, 1);
    CHECK_NEAR(c.platform.planned_speed_mps, 0.15, 1e-12);
    CHECK_NEAR(c.antenna.tx_rx_separation_m, 0.15, 1e-12);
    const ConfigReport report = validate_config(c);
    CHECK(report.errors.empty() && report.warnings.empty());
    const Budget b = compute_budget(c);
    CHECK_NEAR(b.total_bandwidth_hz, 78e6, 1);
    CHECK_NEAR(b.sweep_time_s, 0.0364, 5e-5);
    // the field is required (both parsers reject a config without it)
    std::string text = default_config().source_text;
    const auto at = text.find("  tx_rx_separation_m:");
    CHECK(at != std::string::npos);
    text.erase(at, text.find('\n', at) - at + 1);
    CHECK(throws([&] { parse_run_config(text); }, "tx_rx_separation_m"));
}

// ----- 5.8 GHz first-SAR config (b210-33) -----

void test_sar_5g8_config() {
    const RunConfig c = load_run_config(std::string(SAR_SOURCE_DIR) + "/configs/sar_5g8_2x40.yaml");
    CHECK_NEAR(c.rf.center_freq_hz, 5.8e9, 1);
    CHECK(c.permitted_band.min_hz && c.permitted_band.max_hz);
    CHECK_NEAR(*c.permitted_band.min_hz, 5725e6, 1);
    CHECK_NEAR(*c.permitted_band.max_hz, 5875e6, 1);
    CHECK_NEAR(*c.antenna.gain_dbi, 5.0, 1e-12);
    const ConfigReport report = validate_config(c);
    CHECK(report.errors.empty() && report.warnings.empty());
    const Budget b = compute_budget(c);
    CHECK_NEAR(b.total_bandwidth_hz, 78e6, 1);
    CHECK_NEAR(b.sweep_time_s, 0.0364, 5e-5);
    CHECK_NEAR(b.range_resolution_m, 1.92, 0.01);
    CHECK_NEAR(b.wavelength_m, 0.0517, 0.0001);
    CHECK_NEAR(b.max_avg_conducted_dbm, -6.25, 1e-9);  // -1.25 dBm EIRP - 5 dBi
    // a centre outside 5725-5875 MHz (here the 2.4 GHz one) is refused
    RunConfig out = c;
    out.rf.center_freq_hz = 2441.75e6;
    CHECK(has_error(out, "outside permitted_band"));
    out = c;
    out.rf.center_freq_hz = 5850e6;  // upper step edge 5889 MHz
    CHECK(has_error(out, "outside permitted_band"));
}

void test_sar_5g8_2x30_config() {
    const RunConfig c = load_run_config(std::string(SAR_SOURCE_DIR) + "/configs/sar_5g8_2x30.yaml");
    CHECK_NEAR(c.rf.center_freq_hz, 5.845e9, 1);
    CHECK_NEAR(*c.permitted_band.min_hz, 5725e6, 1);
    CHECK_NEAR(*c.permitted_band.max_hz, 5875e6, 1);
    const ConfigReport report = validate_config(c);
    CHECK(report.errors.empty() && report.warnings.empty());
    const Budget b = compute_budget(c);
    CHECK_NEAR(b.total_bandwidth_hz, 58e6, 1);  // occupied 5816-5874 MHz
    CHECK_NEAR(b.range_resolution_m, 2.58, 0.01);
    CHECK_NEAR(b.wavelength_m, 0.0513, 0.0001);
    RunConfig out = c;
    out.rf.center_freq_hz = 5847e6;  // upper edge 5876 MHz (5846 would end exactly on 5875, still allowed)
    CHECK(has_error(out, "outside permitted_band"));
}

// ----- hardware limits (configs/hw_limits.yaml) and Python parity -----

// tests/fixtures/config_parity.yaml is the table tests/test_config_library.py checks too: same configs, same
// error/warning fields, same wording fragments.
void test_parity_table_matches_python() {
    const YAML::Node table = YAML::LoadFile(std::string(SAR_SOURCE_DIR) + "/tests/fixtures/config_parity.yaml");
    const YAML::Node base = YAML::Load(default_config().source_text);
    std::size_t cases = 0;
    for (const auto& item : table["cases"]) {
        ++cases;
        const std::string name = item["name"].as<std::string>();
        YAML::Node patched = YAML::Clone(base);
        for (const auto& entry : item["set"]) {
            const std::string dotted = entry.first.as<std::string>();
            const auto dot = dotted.find('.');
            patched[dotted.substr(0, dot)][dotted.substr(dot + 1)] = entry.second;
        }
        YAML::Emitter out;
        out << patched;
        const ConfigReport report = validate_config(parse_run_config(out.c_str()));
        for (const std::string severity : {"error", "warning"}) {
            std::set<std::string> got_fields;
            std::vector<const ConfigIssue*> got;
            for (const auto& issue : report.issues) {
                if (issue.severity == severity) {
                    got_fields.insert(issue.field);
                    got.push_back(&issue);
                }
            }
            std::set<std::string> want_fields;
            const YAML::Node wanted = item[severity + "s"];
            for (const auto& w : wanted) {
                const std::string field = w["field"].as<std::string>();
                const std::string text = w["text"].as<std::string>();
                want_fields.insert(field);
                bool found = false;
                for (const ConfigIssue* issue : got) {
                    found = found || (issue->field == field && issue->message.find(text) != std::string::npos);
                }
                if (!found) {
                    std::cerr << "  " << name << ": no " << severity << " '" << field << ": " << text << "'\n";
                }
                CHECK(found);
            }
            if (got_fields != want_fields) {
                std::cerr << "  " << name << ": " << severity << " fields differ from the table; got:\n";
                for (const ConfigIssue* issue : got) {
                    std::cerr << "    " << issue->field << ": " << issue->message << '\n';
                }
            }
            CHECK(got_fields == want_fields);
        }
        // errors/warnings strings are the structured issues as "field: message"; only errors reject
        CHECK(report.errors.empty() == !item["errors"].IsDefined());
        for (const auto& issue : report.issues) {
            const auto& list = issue.severity == "error" ? report.errors : report.warnings;
            CHECK(std::find(list.begin(), list.end(), issue.field + ": " + issue.message) != list.end());
        }
    }
    CHECK(cases >= 20);
}

void test_hw_limits_found_next_to_config_and_loaded() {
    const std::string path = find_hw_limits_path(std::string(SAR_SOURCE_DIR) + "/configs/default.yaml");
    CHECK(!path.empty());
    const HwLimits limits = load_hw_limits(path);
    CHECK(limits.find("rf.tx_gain_db") != nullptr);
    CHECK(limits.find("rf.tx_gain_db")->max.value_or(0) == 89.8);
    CHECK(limits.find("rf.tx_antenna") != nullptr && limits.find("rf.tx_antenna")->allowed.size() == 1);
    CHECK(limits.find("no.such.row") == nullptr);
    // user configs live in configs/user/: the parent directory is searched too
    CHECK(!find_hw_limits_path(std::string(SAR_SOURCE_DIR) + "/configs/user/mine.yaml").empty());
    CHECK(throws([] { load_hw_limits("/nonexistent/hw_limits.yaml"); }, "hw_limits"));
}

void test_usb_rows_are_not_evaluated_offline() {
    const ConfigReport report = validate_config(default_config());
    CHECK(report.not_checked.size() == 2);
    // 56 MS/s is far above the USB 2 row; offline validation must not warn or reject for it
    CHECK(report.errors.empty() && report.warnings.empty());
}

void test_missing_limits_table_refuses() {
    RunConfig c = default_config();
    HwLimits empty;
    empty.path = "empty";
    const ConfigReport report = validate_config(c, empty);
    CHECK(!report.errors.empty());  // a missing row is an error, never a silent pass
    // and a config loaded from a place with no table nearby still finds the build-time default or says so
    CHECK(validate_config(c).errors.empty());
}

void test_warning_rows_do_not_reject() {
    RunConfig c = default_config();
    c.timing.retune_s = 0.005;  // library row, severity warning
    const ConfigReport report = validate_config(c);
    CHECK(report.errors.empty());
    CHECK(!report.warnings.empty());
}

void test_session_refuses_invalid_config() {
    RunConfig c = fast_config();
    c.rf.center_freq_hz = 2450e6;
    CHECK(throws([&] { make_run(c); }, "outside permitted_band"));
}

void test_budget_values() {
    const RunConfig c = default_config();
    const Budget b = compute_budget(c);
    CHECK_NEAR(b.total_bandwidth_hz, 75e6, 1);
    CHECK_NEAR(b.range_resolution_m, 2.0, 0.01);
    CHECK_NEAR(b.sweep_time_s, 2 * (0.0092 + 0.008 + 0.001), 1e-9);
    CHECK_NEAR(b.wavelength_m, 0.1228, 0.001);
    CHECK_NEAR(b.max_speed_lambda_over_3_mps, 1.12, 0.01);
    CHECK_NEAR(b.spacing_for_beamwidth_m, b.wavelength_m / 2, 1e-9);
    CHECK_NEAR(b.data_rate_bytes_per_s, 12.31e6, 0.01e6);
    CHECK_NEAR(b.max_avg_conducted_dbm, -9.25, 1e-9);
    CHECK_NEAR(b.tx_duty_cycle, 1.02 / 18.2, 1e-9);
    CHECK_NEAR(b.ram_bytes, 0.923e9, 0.01e9);
    CHECK(burst_samples(c) == 56000);
    CHECK(chirp_samples(c) == 1120);
}

// ----- chirp -----

void test_chirp_sweeps_the_configured_band() {
    const RunConfig c = default_config();
    const std::vector<Sample> chirp = generate_chirp(c);
    CHECK(chirp.size() == 1120);

    const auto instantaneous_hz = [&](std::size_t n) {
        const std::complex<double> a(chirp[n].real(), chirp[n].imag());
        const std::complex<double> b(chirp[n + 1].real(), chirp[n + 1].imag());
        return std::arg(b * std::conj(a)) * c.waveform.sample_rate_hz / (2 * 3.14159265358979323846);
    };
    // Sampled just inside the taper, where the amplitude is large enough to measure.
    const double slope = c.waveform.chirp_bandwidth_hz / 1120;
    CHECK_NEAR(instantaneous_hz(100), -22.5e6 + slope * 100.5, 0.1e6);
    CHECK_NEAR(instantaneous_hz(560), slope * 0.5, 0.1e6);
    CHECK_NEAR(instantaneous_hz(1000), -22.5e6 + slope * 1000.5, 0.1e6);
}

void test_chirp_taper_and_scale() {
    const RunConfig c = default_config();
    const std::vector<Sample> chirp = generate_chirp(c);
    const auto magnitude = [&](std::size_t n) { return std::hypot(chirp[n].real(), chirp[n].imag()); };
    const double flat = c.waveform.tx_amplitude * 32767;
    CHECK(magnitude(0) < 0.01 * flat);
    CHECK(magnitude(1119) < 0.01 * flat);
    CHECK_NEAR(magnitude(28), flat * 0.5, flat * 0.05);
    CHECK_NEAR(magnitude(560), flat, 2);
    for (std::size_t n = 0; n < chirp.size(); ++n) {
        CHECK(magnitude(n) <= flat + 2);
    }
}

void test_replica_is_exactly_what_is_sent() {
    const RunConfig c = default_config();
    const std::vector<Sample> chirp = generate_chirp(c);
    const auto replica = chirp_to_replica(chirp);
    for (std::size_t n = 0; n < chirp.size(); ++n) {
        CHECK(std::lround(replica[n].real() * 32767.0) == chirp[n].real());
        CHECK(std::lround(replica[n].imag() * 32767.0) == chirp[n].imag());
    }
    const std::vector<Sample> burst = make_tx_burst(c, chirp);
    CHECK(burst.size() == chirp.size() * 51);
    for (std::size_t n = 0; n < burst.size(); ++n) {
        CHECK(burst[n] == chirp[n % chirp.size()]);
    }
}

// ----- helpers -----

void test_sha256_vectors() {
    CHECK(sha256_hex("", 0) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    CHECK(sha256_hex("abc", 3) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    const std::string million(1000000, 'a');
    CHECK(sha256_hex(million.data(), million.size()) ==
          "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    const std::string long_message = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    CHECK(sha256_hex(long_message.data(), long_message.size()) ==
          "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
}

void test_ram_buffer_starts_zeroed() {
    RamBuffer buffer(4, 100);
    for (std::size_t slot = 0; slot < 4; ++slot) {
        for (std::size_t i = 0; i < 100; ++i) {
            CHECK(buffer.slot(slot)[i] == Sample(0, 0));
        }
    }
    CHECK(buffer.slot(1) == buffer.slot(0) + 100);
}

// ----- capture session -----

void test_clean_run_steps_and_times() {
    RunConfig c = fast_config();
    // The fake radio returns bursts without pacing, so the RAM buffer (sized from the nominal step
    // time) fills in ~20 ms of real time, well short of max_run_s. Sync faster than that so the
    // periodic path is exercised: rows = start + periodic + end.
    c.timing.clock_sync_interval_s = 0.002;
    Run run = run_to_end(c);
    const auto& bursts = run.session->bursts();
    CHECK(!run.session->fatal_error());
    CHECK(bursts.size() >= 10);
    CHECK(run.session->status().invalid_bursts == 0);
    CHECK(!run.session->status().running);
    CHECK(run.radio_log->tune_during_burst == 0);

    int64_t previous = -1;
    const auto span = static_cast<uint64_t>(burst_samples(c));
    for (std::size_t i = 0; i < bursts.size(); ++i) {
        CHECK(bursts[i].index == i);
        CHECK(bursts[i].step == i % 2);
        CHECK(bursts[i].sweep == i / 2);
        CHECK(bursts[i].sample_offset == i * span);
        CHECK(bursts[i].num_samples == span);
        CHECK_NEAR(bursts[i].tx_freq_hz, step_center_freq_hz(c, static_cast<int>(i % 2)), 1e-3);
        CHECK(bursts[i].usrp_ticks > previous);
        previous = bursts[i].usrp_ticks;
    }
    CHECK(run.session->clock_sync().size() >= 3);
    for (const ClockSyncRecord& sync : run.session->clock_sync()) {
        CHECK(sync.round_trip_ns >= 0);
    }
}

void test_tx_leads_rx_by_one_chirp_inside_the_band() {
    const RunConfig c = fast_config();
    Run run = run_to_end(c);
    const auto& log = *run.radio_log;
    CHECK(!log.tx_ticks.empty());
    CHECK(log.tx_ticks.size() == log.rx_ticks.size());
    for (std::size_t i = 0; i < log.tx_ticks.size(); ++i) {
        CHECK(log.rx_ticks[i] - log.tx_ticks[i] == static_cast<int64_t>(chirp_samples(c)));
        CHECK(log.tx_counts[i] == burst_samples(c) + chirp_samples(c));
        const double half = c.waveform.chirp_bandwidth_hz / 2;
        CHECK(log.tx_freqs[i] - half >= *c.permitted_band.min_hz);
        CHECK(log.tx_freqs[i] + half <= *c.permitted_band.max_hz);
    }
    const auto burst = make_tx_burst(c, generate_chirp(c));
    CHECK(log.first_tx_burst == burst);
}

void test_rx_only_never_transmits() {
    Run run = run_to_end(fast_config(), {}, false);
    CHECK(run.radio_log->tx_ticks.empty());
    CHECK(!run.radio_log->rx_ticks.empty());
    CHECK(run.session->status().invalid_bursts == 0);
}

void test_faulted_bursts_are_marked_and_keep_their_slot() {
    FakeFaults faults;
    faults.rx_flags[3] = kBurstOverflow;
    faults.rx_flags[5] = kBurstLateCommand;
    faults.tx_flags[7] = kBurstTxUnderflow;
    faults.tx_flags[8] = kBurstTxLate;
    const RunConfig c = fast_config();
    Run run = run_to_end(c, faults);
    const auto& bursts = run.session->bursts();
    CHECK(bursts.size() > 8);
    CHECK(bursts[3].error_flags == kBurstOverflow);
    CHECK(bursts[5].error_flags == kBurstLateCommand);
    CHECK(bursts[7].error_flags == kBurstTxUnderflow);
    CHECK(bursts[8].error_flags == kBurstTxLate);
    CHECK(bursts[2].error_flags == kBurstOk);
    CHECK(bursts[3].sample_offset == 3 * burst_samples(c));
    CHECK(bursts[4].sample_offset == 4 * burst_samples(c));
    CHECK(run.session->status().invalid_bursts == 4);
    CHECK(!run.session->fatal_error());
}

void test_unlocked_lo_blocks_transmit_for_that_burst() {
    FakeFaults faults;
    // Tunes 0 and 1 are the preflight, so the run's fifth burst is tune 6.
    faults.unlocked_tunes.insert(6);
    Run run = run_to_end(fast_config(), faults);
    const auto& bursts = run.session->bursts();
    CHECK(bursts.size() > 5);
    CHECK(bursts[4].error_flags == kBurstLoNotLocked);
    CHECK(bursts[5].error_flags == kBurstOk);
    CHECK(run.radio_log->tx_ticks.size() == bursts.size() - 1);
    for (const int tune : run.radio_log->tx_tune_numbers) {
        CHECK(tune != 6);
    }
}

void test_preflight_failures_stop_before_any_transmit() {
    {
        FakeFaults faults;
        faults.unlocked_tunes.insert(1);
        Run run = make_run(fast_config(), faults);
        CHECK(throws([&] { run.session->start(); }, "did not lock"));
        CHECK(run.radio_log->tx_ticks.empty());
    }
    {
        FakeFaults faults;
        faults.tune_offset_hz = 20e6;  // the device landed on the wrong frequency
        Run run = make_run(fast_config(), faults);
        CHECK(throws([&] { run.session->start(); }, "outside permitted_band"));
        CHECK(run.radio_log->tx_ticks.empty());
    }
}

void test_timestamp_mismatch_invalidates() {
    FakeFaults faults;
    faults.wrong_rx_tick = true;
    Run run = run_to_end(fast_config(), faults);
    CHECK(!run.session->bursts().empty());
    for (const BurstRecord& burst : run.session->bursts()) {
        CHECK(burst.error_flags == kBurstRxTimeMismatch);
    }
}

void test_radio_failure_ends_run_and_keeps_data() {
    FakeFaults faults;
    faults.rx_throw_at = 4;
    const RunConfig c = fast_config();
    Run run = run_to_end(c, faults);
    CHECK(run.session->fatal_error().has_value());
    CHECK(*run.session->fatal_error() == "device unplugged");
    CHECK(run.session->bursts().size() == 5);
    CHECK(run.session->bursts()[4].error_flags & kBurstRadioError);
    CHECK(!run.session->status().running);

    TempDir dir;
    run.session->write_dataset((dir.path / "run").string());
    const std::string summary = read_text(dir.path / "run" / "run_summary.json");
    CHECK(summary.find("device unplugged") != std::string::npos);
    CHECK(summary.find("\"suspect\": true") != std::string::npos);
}

void test_request_stop_ends_run_promptly() {
    Run run = make_run(fast_config(30));
    run.session->start();
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    CHECK(run.session->status().running);
    run.session->request_stop();
    const auto start = std::chrono::steady_clock::now();
    run.session->wait();
    CHECK(std::chrono::steady_clock::now() - start < std::chrono::seconds(1));
    CHECK(!run.session->status().running);
}

// ----- live callbacks -----

bool same_record(const BurstRecord& a, const BurstRecord& b) {
    return a.index == b.index && a.sweep == b.sweep && a.step == b.step && a.sample_offset == b.sample_offset &&
           a.num_samples == b.num_samples && a.usrp_ticks == b.usrp_ticks && a.tx_freq_hz == b.tx_freq_hz &&
           a.rx_freq_hz == b.rx_freq_hz && a.lo_offset_hz == b.lo_offset_hz && a.tx_gain_db == b.tx_gain_db &&
           a.rx_gain_db == b.rx_gain_db && a.error_flags == b.error_flags;
}

bool same_sync(const ClockSyncRecord& a, const ClockSyncRecord& b) {
    return a.usrp_ticks == b.usrp_ticks && a.host_monotonic_ns == b.host_monotonic_ns &&
           a.host_realtime_ns == b.host_realtime_ns && a.round_trip_ns == b.round_trip_ns;
}

// What a callback saw. Written only from the control thread; read after wait().
struct Seen {
    std::vector<BurstRecord> bursts;
    std::vector<const Sample*> iq;
    std::vector<std::size_t> counts;
    std::vector<Sample> first;
    std::vector<Sample> last;
    std::vector<int> tunes_at_call;
    std::vector<ClockSyncRecord> syncs;
    std::set<std::thread::id> threads;
};

CaptureOptions recording_options(Seen& seen, const std::shared_ptr<FakeRadio::Log>& radio_log) {
    seen.bursts.reserve(1000);
    CaptureOptions options;
    options.on_burst = [&seen, radio_log](const BurstRecord& record, const Sample* iq, std::size_t count) {
        seen.tunes_at_call.push_back(radio_log->tunes.load());
        seen.bursts.push_back(record);
        seen.iq.push_back(iq);
        seen.counts.push_back(count);
        seen.first.push_back(iq[0]);
        seen.last.push_back(iq[count - 1]);
        seen.threads.insert(std::this_thread::get_id());
    };
    options.on_clock_sync = [&seen](const ClockSyncRecord& sync) {
        seen.syncs.push_back(sync);
        seen.threads.insert(std::this_thread::get_id());
    };
    return options;
}

void test_callbacks_see_every_final_burst_and_sync() {
    RunConfig c = fast_config();
    c.timing.clock_sync_interval_s = 0.002;
    FakeFaults faults;
    faults.rx_flags[3] = kBurstOverflow;  // receive call 3 = burst 3, half filled
    faults.unlocked_tunes.insert(6);      // burst 4: no stream, slot stays zero
    Seen seen;
    Run run;
    run.session = std::make_unique<CaptureSession>(
        c, std::make_unique<FakeRadio>(c.device.master_clock_rate_hz, faults, run.radio_log), run.run_log,
        recording_options(seen, run.radio_log));
    run.session->start();
    run.session->wait();

    const auto& bursts = run.session->bursts();
    const std::size_t span = burst_samples(c);
    CHECK(!run.session->fatal_error());
    CHECK(bursts.size() > 8);
    CHECK(seen.bursts.size() == bursts.size());  // one call per burst
    for (std::size_t i = 0; i < std::min(seen.bursts.size(), bursts.size()); ++i) {
        CHECK(same_record(seen.bursts[i], bursts[i]));  // in order, final values
        CHECK(seen.counts[i] == span);
        CHECK(seen.iq[i] == seen.iq[0] + i * span);  // the burst's own slot
        // Delivered after the NEXT burst was retuned and commanded (overlap), except the last.
        CHECK(seen.tunes_at_call[i] == static_cast<int>(i) + (i + 1 < bursts.size() ? 4 : 3));
    }
    // The IQ was complete when the callback ran (fake fills real = receive call number).
    CHECK(seen.first[2] == Sample(2, 0));
    CHECK(seen.last[2] == Sample(2, static_cast<int16_t>((span - 1) & 0x7fff)));
    CHECK(seen.first[3] == Sample(3, 0));
    CHECK(seen.last[3] == Sample(0, 0));  // the faulted burst's missing half is zero
    CHECK(seen.first[4] == Sample(0, 0));  // unlocked: nothing received
    CHECK(seen.first[5] == Sample(4, 0));

    CHECK(run.session->clock_sync().size() >= 3);
    CHECK(seen.syncs.size() == run.session->clock_sync().size());
    for (std::size_t i = 0; i < std::min(seen.syncs.size(), run.session->clock_sync().size()); ++i) {
        CHECK(same_sync(seen.syncs[i], run.session->clock_sync()[i]));
    }
    CHECK(seen.threads.size() == 1);
    CHECK(seen.threads.count(std::this_thread::get_id()) == 0);  // the control thread, not ours

    const CaptureStatus status = run.session->status();
    CHECK(status.callback_errors == 0);
    CHECK(status.max_callback_s >= 0 && status.max_callback_s < 1);
    CHECK(!status.failed);
    CHECK(status.bursts == bursts.size());
    CHECK(status.ram_slots == run.session->budget().ram_slots);
    CHECK(status.last_error_flags == kBurstLoNotLocked);  // burst 4 is the latest invalid one
    const double rate = c.device.master_clock_rate_hz;
    CHECK(status.sweeps == (bursts.size() - 1) / 2);
    CHECK(status.max_sweep_period_s >= status.mean_sweep_period_s && status.mean_sweep_period_s > 0);
    CHECK_NEAR(status.elapsed_s, (bursts.back().usrp_ticks - run.session->clock_sync().front().usrp_ticks) / rate,
               1e-9);
    int64_t max_gap = 0;
    for (std::size_t i = 1; i < bursts.size(); ++i) {
        max_gap = std::max(max_gap, bursts[i].usrp_ticks - bursts[i - 1].usrp_ticks);
    }
    CHECK_NEAR(status.max_burst_gap_s, max_gap / rate, 1e-9);
}

void test_slow_callback_does_not_change_burst_flags() {
    // The fake never reports lateness by itself, so this guards the session's own logic: a slow
    // consumer must not turn into flagged, dropped or overlapping bursts.
    const RunConfig c = fast_config();
    CaptureOptions options;
    std::atomic<int> calls{0};
    options.on_burst = [&calls](const BurstRecord&, const Sample*, std::size_t) {
        ++calls;
        std::this_thread::sleep_for(std::chrono::milliseconds(3));  // ~15x the fake's burst time
    };
    Run slow = make_run(c, {}, true, std::move(options));
    slow.session->start();
    slow.session->wait();
    CHECK(!slow.session->fatal_error());
    CHECK(slow.session->bursts().size() >= 10);
    CHECK(calls == static_cast<int>(slow.session->bursts().size()));
    for (const BurstRecord& burst : slow.session->bursts()) {
        CHECK(burst.error_flags == kBurstOk);
    }
    CHECK(slow.session->status().invalid_bursts == 0);
    CHECK(slow.radio_log->tune_during_burst == 0);
    CHECK(slow.session->status().max_callback_s >= 0.003);
    // The slow callback stretches the spacing, as documented, and status shows it.
    CHECK(slow.session->status().max_burst_gap_s >= 0.003);
}

void test_throwing_callback_does_not_end_the_run() {
    const RunConfig c = fast_config();
    CaptureOptions options;
    std::atomic<int> calls{0};
    options.on_burst = [&calls](const BurstRecord& record, const Sample*, std::size_t) {
        ++calls;
        if (record.index % 2 == 0) {
            throw std::runtime_error("consumer broke");
        }
    };
    options.on_clock_sync = [](const ClockSyncRecord&) { throw 42; };
    Run run = make_run(c, {}, true, std::move(options));
    run.session->start();
    run.session->wait();
    const auto& bursts = run.session->bursts();
    CHECK(!run.session->fatal_error());
    CHECK(bursts.size() >= 10);  // ran to its normal end (RAM full or max_run_s)
    CHECK(calls == static_cast<int>(bursts.size()));
    CHECK(run.session->status().invalid_bursts == 0);
    CHECK(run.session->status().callback_errors == (bursts.size() + 1) / 2 + run.session->clock_sync().size());
    int logged = 0;
    for (const std::string& line : run.run_log->lines()) {
        logged += line.find("callback threw") != std::string::npos ? 1 : 0;
    }
    CHECK(logged == 1);
}

void test_callbacks_deliver_bursts_before_a_radio_failure() {
    FakeFaults faults;
    faults.rx_throw_at = 4;
    const RunConfig c = fast_config();
    Seen seen;
    Run run;
    run.session = std::make_unique<CaptureSession>(
        c, std::make_unique<FakeRadio>(c.device.master_clock_rate_hz, faults, run.radio_log), run.run_log,
        recording_options(seen, run.radio_log));
    run.session->start();
    run.session->wait();
    CHECK(run.session->status().failed);
    CHECK(seen.bursts.size() == 5);
    CHECK(seen.bursts.size() == run.session->bursts().size());
    CHECK(!seen.bursts.empty() && (seen.bursts.back().error_flags & kBurstRadioError));
}

void test_destructor_stops_a_running_session() {
    Run run = make_run(fast_config(30));
    run.session->start();
    run.session.reset();
}

void test_session_runs_once() {
    Run run = run_to_end(fast_config(0.02));
    CHECK(throws([&] { run.session->start(); }, "runs once"));
}

// ----- dataset -----

void test_dataset_layout_and_contents() {
    const RunConfig c = fast_config();
    FakeFaults faults;
    faults.rx_flags[2] = kBurstOverflow;
    Run run = run_to_end(c, faults);
    TempDir dir;
    const fs::path out = dir.path / "nested" / "run";
    run.session->write_dataset(out.string());

    const auto& bursts = run.session->bursts();
    const std::size_t samples = burst_samples(c);
    CHECK(fs::file_size(out / "iq.sc16") == bursts.size() * samples * 4);
    CHECK(fs::file_size(out / "chirp_replica.cf32") == chirp_samples(c) * 8);
    CHECK(read_text(out / "config.yaml") == c.source_text);

    std::istringstream csv(read_text(out / "bursts.csv"));
    std::string line;
    std::getline(csv, line);
    CHECK(line == kBurstCsvHeader);
    std::size_t rows = 0;
    while (std::getline(csv, line)) {
        ++rows;
    }
    CHECK(rows == bursts.size());

    // A partly filled burst keeps its slot, and its missing samples stay zero.
    std::ifstream iq(out / "iq.sc16", std::ios::binary);
    const auto sample_at = [&](std::size_t burst, std::size_t offset) {
        iq.seekg(static_cast<std::streamoff>((burst * samples + offset) * sizeof(Sample)));
        Sample sample;
        iq.read(reinterpret_cast<char*>(&sample), sizeof(sample));
        return sample;
    };
    CHECK(sample_at(1, 5) == Sample(1, 5));
    CHECK(sample_at(2, 5) == Sample(2, 5));
    CHECK(sample_at(2, samples - 1) == Sample(0, 0));
    CHECK(sample_at(3, 5) == Sample(3, 5));

    CHECK(read_text(out / "run_summary.json").find("\"invalid_bursts\": 1") != std::string::npos);
    CHECK(read_text(out / "run_log.txt").find("rx_overflow") != std::string::npos);
    CHECK(read_text(out / "run_log.txt").find("Parameter budget") != std::string::npos);

    const std::string verify = "cd '" + out.string() + "' && sha256sum --status -c checksums.sha256";
    CHECK(std::system(verify.c_str()) == 0);
}

void test_dataset_never_overwrites() {
    Run run = run_to_end(fast_config(0.02));
    TempDir dir;
    const std::string out = (dir.path / "run").string();
    run.session->write_dataset(out);
    const std::string before = read_text(fs::path(out) / "bursts.csv");
    CHECK(throws([&] { run.session->write_dataset(out); }, "already exists"));
    CHECK(read_text(fs::path(out) / "bursts.csv") == before);
}

void test_write_dataset_rejected_while_running() {
    Run run = make_run(fast_config(30));
    run.session->start();
    TempDir dir;
    CHECK(throws([&] { run.session->write_dataset((dir.path / "run").string()); }, "active"));
    CHECK(!fs::exists(dir.path / "run"));
    run.session->request_stop();
    run.session->wait();
}

}  // namespace

int main() {
    const std::vector<std::pair<const char*, std::function<void()>>> tests = {
#define T(name) {#name, name}
        T(test_default_config_is_valid),
        T(test_parser_rejects_bad_files),
        T(test_sar_first_config_and_separation),
        T(test_sar_5g8_config),
        T(test_sar_5g8_2x30_config),
        T(test_validation_blocks_unsafe_configs),
        T(test_parity_table_matches_python),
        T(test_hw_limits_found_next_to_config_and_loaded),
        T(test_usb_rows_are_not_evaluated_offline),
        T(test_missing_limits_table_refuses),
        T(test_warning_rows_do_not_reject),
        T(test_session_refuses_invalid_config),
        T(test_budget_values),
        T(test_chirp_sweeps_the_configured_band),
        T(test_chirp_taper_and_scale),
        T(test_replica_is_exactly_what_is_sent),
        T(test_sha256_vectors),
        T(test_ram_buffer_starts_zeroed),
        T(test_clean_run_steps_and_times),
        T(test_tx_leads_rx_by_one_chirp_inside_the_band),
        T(test_rx_only_never_transmits),
        T(test_faulted_bursts_are_marked_and_keep_their_slot),
        T(test_unlocked_lo_blocks_transmit_for_that_burst),
        T(test_preflight_failures_stop_before_any_transmit),
        T(test_timestamp_mismatch_invalidates),
        T(test_radio_failure_ends_run_and_keeps_data),
        T(test_request_stop_ends_run_promptly),
        T(test_callbacks_see_every_final_burst_and_sync),
        T(test_slow_callback_does_not_change_burst_flags),
        T(test_throwing_callback_does_not_end_the_run),
        T(test_callbacks_deliver_bursts_before_a_radio_failure),
        T(test_destructor_stops_a_running_session),
        T(test_session_runs_once),
        T(test_dataset_layout_and_contents),
        T(test_dataset_never_overwrites),
        T(test_write_dataset_rejected_while_running),
#undef T
    };
    for (const auto& [name, test] : tests) {
        const int before = g_failures;
        test();
        std::cout << (g_failures == before ? "ok   " : "FAIL ") << name << '\n';
    }
    std::cout << tests.size() << " tests, " << g_failures << " failed checks\n";
    return g_failures == 0 ? 0 : 1;
}
