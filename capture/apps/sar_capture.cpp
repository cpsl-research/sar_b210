// Capture program. See `sar_capture --help`.
#include <atomic>
#include <chrono>
#include <csignal>
#include <exception>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "sar/budget.hpp"
#include "sar/capture_session.hpp"
#include "sar/run_config.hpp"
#include "sar/uhd_radio.hpp"

namespace {

volatile std::sig_atomic_t g_interrupted = 0;
void on_interrupt(int) { g_interrupted = 1; }

constexpr const char* kUsage =
    "usage: sar_capture CONFIG RUN_DIR [--rx-only] [--duration SECONDS]\n"
    "       sar_capture --check CONFIG\n"
    "\n"
    "  --check      validate the config and print the budget; does not open the radio\n"
    "  --rx-only    do not create a TX streamer; for timing and overflow tests with nothing radiated\n"
    "  --duration   end after this long (default timing.max_run_s); Ctrl-C ends the run early\n";

int check(const std::string& path) {
    const sar::RunConfig config = sar::load_run_config(path);
    const sar::ConfigReport report = sar::validate_config(config);
    for (const std::string& error : report.errors) {
        std::cerr << "ERROR: " << error << '\n';
    }
    if (!report.errors.empty()) {
        return 1;
    }
    std::cout << sar::format_budget(config, sar::compute_budget(config));
    for (const std::string& warning : report.warnings) {
        std::cout << "WARNING: " << warning << '\n';
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    const std::vector<std::string> args(argv + 1, argv + argc);
    std::vector<std::string> positional;
    bool rx_only = false;
    double duration_s = 0;
    try {
        for (std::size_t i = 0; i < args.size(); ++i) {
            if (args[i] == "--check" && args.size() == 2 && i == 0) {
                return check(args[1]);
            } else if (args[i] == "--rx-only") {
                rx_only = true;
            } else if (args[i] == "--duration" && i + 1 < args.size()) {
                duration_s = std::stod(args[++i]);
            } else if (args[i].rfind("--", 0) == 0) {
                std::cerr << kUsage;
                return args[i] == "--help" ? 0 : 2;
            } else {
                positional.push_back(args[i]);
            }
        }
        if (positional.size() != 2) {
            std::cerr << kUsage;
            return 2;
        }

        sar::RunConfig config = sar::load_run_config(positional[0]);
        if (duration_s > 0) {
            config.timing.max_run_s = duration_s;
        }
        const sar::ConfigReport report = sar::validate_config(config);
        for (const std::string& error : report.errors) {
            std::cerr << "ERROR: " << error << '\n';
        }
        if (!report.errors.empty()) {
            return 1;
        }
        std::cout << sar::format_budget(config, sar::compute_budget(config));
        for (const std::string& warning : report.warnings) {
            std::cout << "WARNING: " << warning << '\n';
        }
        std::cout << (rx_only ? "Mode: RX only, nothing is transmitted\n" : "Mode: TRANSMITTING\n");

        auto log = std::make_shared<sar::RunLog>();
        if (duration_s > 0) {
            log->add("--duration overrides timing.max_run_s in config.yaml: " + std::to_string(duration_s) + " s");
        }
        sar::CaptureSession session(config, sar::make_uhd_radio(config, log, !rx_only), log, {!rx_only});

        std::signal(SIGINT, on_interrupt);
        std::signal(SIGTERM, on_interrupt);
        session.start();
        while (session.status().running) {
            if (g_interrupted) {
                session.request_stop();
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        session.wait();

        const sar::CaptureStatus status = session.status();
        std::cout << "Captured " << status.bursts << " bursts, " << status.invalid_bursts << " invalid\n";
        session.write_dataset(positional[1]);
        std::cout << "Wrote " << positional[1] << '\n';
        if (session.fatal_error()) {
            std::cerr << "Run ended early: " << *session.fatal_error() << '\n';
            return 1;
        }
        if (status.bursts > 0 && status.invalid_bursts * 100 > status.bursts) {
            std::cerr << "SUSPECT RUN: over 1% of bursts are invalid\n";
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "ERROR: " << error.what() << '\n';
        return 1;
    }
}
