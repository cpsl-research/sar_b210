// Bench timing probe: measures retune and LO lock times between the configured steps. Validation
// stage 1. Never transmits. Overflow rate comes from a long `sar_capture --rx-only` run.
// Usage: sar_bench_probe CONFIG [RETUNES]
#include <algorithm>
#include <chrono>
#include <exception>
#include <iomanip>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "sar/run_config.hpp"
#include "sar/uhd_radio.hpp"

namespace {

using Clock = std::chrono::steady_clock;

double ms_since(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

void report(const char* name, std::vector<double> values) {
    std::sort(values.begin(), values.end());
    double sum = 0;
    for (const double value : values) {
        sum += value;
    }
    std::cout << std::setw(14) << name << std::fixed << std::setprecision(3) << "  median "
              << values[values.size() / 2] << "  mean " << sum / static_cast<double>(values.size()) << "  p99 "
              << values[values.size() * 99 / 100] << "  max " << values.back() << " ms\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2 || argc > 3) {
        std::cerr << "usage: sar_bench_probe CONFIG [RETUNES]\n";
        return 2;
    }
    try {
        const sar::RunConfig config = sar::load_run_config(argv[1]);
        const int retunes = argc == 3 ? std::stoi(argv[2]) : 500;
        const auto radio = sar::make_uhd_radio(config, std::make_shared<sar::RunLog>(), false);

        std::vector<double> tune_ms, lock_ms, query_ms;
        int unlocked = 0;
        for (int i = 0; i < retunes; ++i) {
            const int step = i % config.waveform.num_steps;
            auto start = Clock::now();
            radio->tune(sar::step_center_freq_hz(config, step), config.rf.lo_offset_hz);
            tune_ms.push_back(ms_since(start));

            start = Clock::now();
            while (!radio->lo_locked()) {
                if (ms_since(start) > config.timing.lock_timeout_s * 1e3) {
                    ++unlocked;
                    break;
                }
            }
            lock_ms.push_back(ms_since(start));

            start = Clock::now();
            radio->time_now_ticks();
            query_ms.push_back(ms_since(start));
        }

        std::cout << retunes << " retunes across " << config.waveform.num_steps << " steps, " << unlocked
                  << " failed to lock\n";
        report("tune", tune_ms);
        report("lock check", lock_ms);
        report("time query", query_ms);
        std::cout << "These cover host-side timing only. Settle margin still has to be set from leakage phase\n"
                     "stability once TX is enabled (validation stage 2).\n";
        return unlocked == 0 ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "ERROR: " << error.what() << '\n';
        return 1;
    }
}
