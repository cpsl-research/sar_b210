#include "sar/budget.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <sstream>

namespace sar {

Budget compute_budget(const RunConfig& c) {
    constexpr double kSpeedOfLight = 299792458.0;
    constexpr double kPi = 3.14159265358979323846;
    constexpr double kBytesPerSample = 4.0;
    const auto& w = c.waveform;

    Budget b{};
    b.total_bandwidth_hz = (w.num_steps - 1) * w.step_spacing_hz + w.chirp_bandwidth_hz;
    b.range_resolution_m = kSpeedOfLight / (2 * b.total_bandwidth_hz);

    // Mirrors the control loop: retune, then TX leads RX by one chirp, both after settle and command lead.
    const double wait_s = std::max(c.timing.settle_margin_s, c.timing.command_lead_s + w.chirp_length_s);
    b.step_time_s = c.timing.retune_s + wait_s + w.dwell_s;
    b.sweep_time_s = w.num_steps * b.step_time_s;
    b.sweep_rate_hz = 1 / b.sweep_time_s;

    b.wavelength_m = kSpeedOfLight / c.rf.center_freq_hz;
    b.spacing_lambda_over_3_m = b.wavelength_m / 3;
    b.spacing_for_beamwidth_m = b.wavelength_m / (4 * std::sin(c.antenna.beamwidth_deg * kPi / 360));
    b.max_speed_lambda_over_3_mps = b.spacing_lambda_over_3_m / b.sweep_time_s;
    b.max_speed_for_beamwidth_mps = b.spacing_for_beamwidth_m / b.sweep_time_s;
    b.position_accuracy_m = b.wavelength_m / 8;

    b.data_per_sweep_bytes = w.num_steps * w.dwell_s * w.sample_rate_hz * kBytesPerSample;
    b.data_rate_bytes_per_s = b.data_per_sweep_bytes / b.sweep_time_s;

    b.tx_duty_cycle = (w.dwell_s + w.chirp_length_s) / b.step_time_s;
    b.max_avg_conducted_dbm = std::numeric_limits<double>::quiet_NaN();
    b.max_burst_conducted_dbm = std::numeric_limits<double>::quiet_NaN();
    if (c.permitted_band.max_avg_eirp_dbm && c.antenna.gain_dbi) {
        b.max_avg_conducted_dbm = *c.permitted_band.max_avg_eirp_dbm - *c.antenna.gain_dbi;
        b.max_burst_conducted_dbm = b.max_avg_conducted_dbm - 10 * std::log10(b.tx_duty_cycle);
    }

    b.ram_slots = static_cast<std::size_t>(std::ceil(c.timing.max_run_s * 1.25 / b.step_time_s));
    b.ram_bytes = static_cast<double>(b.ram_slots) * w.dwell_s * w.sample_rate_hz * kBytesPerSample;
    return b;
}

std::string format_budget(const RunConfig& c, const Budget& b) {
    const double half_span = b.total_bandwidth_hz / 2;
    std::ostringstream out;
    out.setf(std::ios::fixed);
    out.precision(2);
    out << "Parameter budget\n"
        << "  occupied band        " << (c.rf.center_freq_hz - half_span) / 1e6 << " - "
        << (c.rf.center_freq_hz + half_span) / 1e6 << " MHz (" << c.waveform.num_steps << " steps)\n"
        << "  range resolution     " << b.range_resolution_m << " m\n"
        << "  sweep time           " << b.sweep_time_s * 1e3 << " ms (" << b.sweep_rate_hz << " sweeps/s)\n"
        << "  wavelength           " << b.wavelength_m * 100 << " cm\n"
        << "  max speed            " << b.max_speed_lambda_over_3_mps << " m/s at lambda/3, "
        << b.max_speed_for_beamwidth_mps << " m/s for " << c.antenna.beamwidth_deg << " deg beam\n"
        << "  planned speed        " << c.platform.planned_speed_mps << " m/s\n"
        << "  position accuracy    " << b.position_accuracy_m * 1e3 << " mm relative\n"
        << "  data rate            " << b.data_rate_bytes_per_s / 1e6 << " MB/s\n"
        << "  RAM buffer           " << b.ram_bytes / 1e9 << " GB (" << b.ram_slots << " bursts)\n"
        << "  TX duty cycle        " << b.tx_duty_cycle * 100 << " %\n";
    if (!std::isnan(b.max_avg_conducted_dbm)) {
        out << "  TX conducted limit   " << b.max_avg_conducted_dbm << " dBm average, " << b.max_burst_conducted_dbm
            << " dBm during a burst if duty-cycle averaging applies\n";
    }
    return out.str();
}

}  // namespace sar
