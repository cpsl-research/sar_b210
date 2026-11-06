#include "sar/chirp.hpp"

#include <cmath>

namespace sar {
namespace {
constexpr double kPi = 3.14159265358979323846;
constexpr double kFullScale = 32767.0;
}  // namespace

std::vector<Sample> generate_chirp(const RunConfig& c) {
    const std::size_t length = chirp_samples(c);
    const double duration = static_cast<double>(length) / c.waveform.sample_rate_hz;
    const double bandwidth = c.waveform.chirp_bandwidth_hz;
    const auto taper_length = static_cast<std::size_t>(std::llround(c.waveform.taper_fraction * length));
    const double scale = c.waveform.tx_amplitude * kFullScale;

    std::vector<Sample> chirp(length);
    for (std::size_t n = 0; n < length; ++n) {
        const double t = static_cast<double>(n) / c.waveform.sample_rate_hz;
        const double phase = 2 * kPi * (-bandwidth / 2 * t + bandwidth / (2 * duration) * t * t);

        double taper = 1.0;
        const std::size_t edge = std::min(n, length - 1 - n);
        if (edge < taper_length) {
            taper = 0.5 * (1 - std::cos(kPi * (edge + 0.5) / taper_length));
        }
        chirp[n] = Sample(static_cast<int16_t>(std::lround(scale * taper * std::cos(phase))),
                          static_cast<int16_t>(std::lround(scale * taper * std::sin(phase))));
    }
    return chirp;
}

std::vector<std::complex<float>> chirp_to_replica(const std::vector<Sample>& chirp) {
    std::vector<std::complex<float>> replica;
    replica.reserve(chirp.size());
    for (const Sample& sample : chirp) {
        replica.emplace_back(static_cast<float>(sample.real() / kFullScale),
                             static_cast<float>(sample.imag() / kFullScale));
    }
    return replica;
}

std::vector<Sample> make_tx_burst(const RunConfig& c, const std::vector<Sample>& chirp) {
    std::vector<Sample> burst;
    const std::size_t periods = dwell_chirps(c) + 1;
    burst.reserve(chirp.size() * periods);
    for (std::size_t i = 0; i < periods; ++i) {
        burst.insert(burst.end(), chirp.begin(), chirp.end());
    }
    return burst;
}

}  // namespace sar
