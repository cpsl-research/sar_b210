#include "sar/dataset_writer.hpp"

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <sstream>
#include <stdexcept>

#include "sar/budget.hpp"
#include "sar/sha256.hpp"

namespace sar {
namespace fs = std::filesystem;

namespace {

// A file that reaches disk or throws, so a full or removed USB drive cannot pass silently.
class OutputFile {
public:
    explicit OutputFile(const fs::path& path) : path_(path) {
        fd_ = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0644);
        if (fd_ < 0) {
            fail("open");
        }
    }
    ~OutputFile() {
        if (fd_ >= 0) {
            ::close(fd_);
        }
    }
    OutputFile(const OutputFile&) = delete;
    OutputFile& operator=(const OutputFile&) = delete;

    void write(const void* data, std::size_t size) {
        const char* cursor = static_cast<const char*>(data);
        while (size > 0) {
            const ssize_t written = ::write(fd_, cursor, size);
            if (written < 0) {
                if (errno == EINTR) {
                    continue;
                }
                fail("write");
            }
            cursor += written;
            size -= static_cast<std::size_t>(written);
        }
    }
    void write(const std::string& text) { write(text.data(), text.size()); }

    void close() {
        if (::fsync(fd_) != 0) {
            fail("sync");
        }
        const int fd = fd_;
        fd_ = -1;
        if (::close(fd) != 0) {
            fail("close");
        }
    }

private:
    [[noreturn]] void fail(const char* action) const {
        throw std::runtime_error(std::string("cannot ") + action + " " + path_.string() + ": " + std::strerror(errno));
    }

    fs::path path_;
    int fd_ = -1;
};

void write_text(const fs::path& path, const std::string& text) {
    OutputFile file(path);
    file.write(text);
    file.close();
}

std::string json_escape(const std::string& text) {
    std::string out;
    for (const char ch : text) {
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(ch) < 0x20) {
                    char escaped[8];
                    std::snprintf(escaped, sizeof(escaped), "\\u%04x", ch);
                    out += escaped;
                } else {
                    out += ch;
                }
        }
    }
    return out;
}

std::string bursts_csv(const DatasetContents& d) {
    const double tick_rate = d.config.device.master_clock_rate_hz;
    std::string out = std::string(kBurstCsvHeader) + "\n";
    char line[320];
    for (const BurstRecord& b : d.bursts) {
        std::snprintf(line, sizeof(line), "%llu,%u,%u,%llu,%llu,%lld,%.9f,%.3f,%.3f,%.3f,%.3f,%.3f,%d,%u\n",
                      static_cast<unsigned long long>(b.index), b.sweep, b.step,
                      static_cast<unsigned long long>(b.sample_offset), static_cast<unsigned long long>(b.num_samples),
                      static_cast<long long>(b.usrp_ticks), static_cast<double>(b.usrp_ticks) / tick_rate, b.tx_freq_hz,
                      b.rx_freq_hz, b.lo_offset_hz, b.tx_gain_db, b.rx_gain_db, b.error_flags == kBurstOk ? 1 : 0,
                      b.error_flags);
        out += line;
    }
    return out;
}

std::string clock_sync_csv(const DatasetContents& d) {
    std::string out = "usrp_ticks,host_monotonic_ns,host_realtime_ns,round_trip_ns\n";
    for (const ClockSyncRecord& r : d.clock_sync) {
        out += std::to_string(r.usrp_ticks) + "," + std::to_string(r.host_monotonic_ns) + "," +
               std::to_string(r.host_realtime_ns) + "," + std::to_string(r.round_trip_ns) + "\n";
    }
    return out;
}

std::string sigmf_meta(const DatasetContents& d) {
    std::ostringstream out;
    out.precision(15);
    out << "{\n  \"global\": {\n"
        << "    \"core:datatype\": \"ci16_le\",\n"
        << "    \"core:sample_rate\": " << d.config.waveform.sample_rate_hz << ",\n"
        << "    \"core:version\": \"1.0.0\",\n"
        << "    \"core:hw\": \"" << json_escape(d.config.device.args) << "\",\n"
        << "    \"core:description\": \"Stepped-frequency SAR bursts stored back to back; bursts.csv gives each burst's "
           "time and frequency\"\n"
        << "  },\n  \"captures\": [\n"
        << "    {\"core:sample_start\": 0, \"core:frequency\": " << d.config.rf.center_freq_hz << "}\n"
        << "  ],\n  \"annotations\": []\n}\n";
    return out.str();
}

std::string run_summary(const DatasetContents& d) {
    constexpr double kSuspectFraction = 0.01;
    std::size_t invalid = 0;
    for (const BurstRecord& b : d.bursts) {
        invalid += b.error_flags != kBurstOk ? 1 : 0;
    }
    const double fraction = d.bursts.empty() ? 0.0 : static_cast<double>(invalid) / static_cast<double>(d.bursts.size());
    const double duration = d.bursts.empty() ? 0.0
                                             : static_cast<double>(d.bursts.back().usrp_ticks - d.bursts.front().usrp_ticks) /
                                                   d.config.device.master_clock_rate_hz;
    std::ostringstream out;
    out << "{\n"
        << "  \"bursts\": " << d.bursts.size() << ",\n"
        << "  \"invalid_bursts\": " << invalid << ",\n"
        << "  \"invalid_percent\": " << fraction * 100 << ",\n"
        << "  \"suspect\": " << (fraction > kSuspectFraction || d.fatal_error ? "true" : "false") << ",\n"
        << "  \"duration_s\": " << duration << ",\n"
        << "  \"transmit\": " << (d.transmit ? "true" : "false") << ",\n"
        << "  \"fatal_error\": ";
    if (d.fatal_error) {
        out << '"' << json_escape(*d.fatal_error) << '"';
    } else {
        out << "null";
    }
    out << "\n}\n";
    return out.str();
}

}  // namespace

void write_dataset(const std::string& run_dir, const DatasetContents& d) {
    const fs::path dir(run_dir);
    const std::uintmax_t iq_bytes = d.bursts.size() * d.iq.samples_per_slot() * sizeof(Sample);
    constexpr std::uintmax_t kMetadataMargin = 64 * 1024 * 1024;

    if (fs::exists(dir)) {
        throw std::runtime_error("run folder already exists: " + run_dir);
    }
    fs::path existing = fs::absolute(dir);
    while (!fs::exists(existing)) {
        existing = existing.parent_path();
    }
    const std::uintmax_t available = fs::space(existing).available;
    if (available < iq_bytes + kMetadataMargin) {
        throw std::runtime_error("not enough space at " + existing.string() + ": need " +
                                 std::to_string((iq_bytes + kMetadataMargin) / 1000000) + " MB, have " +
                                 std::to_string(available / 1000000) + " MB");
    }
    fs::create_directories(dir);

    {
        OutputFile iq(dir / "iq.sc16");
        for (std::size_t i = 0; i < d.bursts.size(); ++i) {
            iq.write(d.iq.slot(i), d.iq.samples_per_slot() * sizeof(Sample));
        }
        iq.close();
    }
    {
        const auto replica = chirp_to_replica(d.chirp);
        OutputFile file(dir / "chirp_replica.cf32");
        file.write(replica.data(), replica.size() * sizeof(replica[0]));
        file.close();
    }
    write_text(dir / "bursts.csv", bursts_csv(d));
    write_text(dir / "clock_sync.csv", clock_sync_csv(d));
    write_text(dir / "iq.sigmf-meta", sigmf_meta(d));
    write_text(dir / "config.yaml", d.config.source_text);
    write_text(dir / "run_summary.json", run_summary(d));

    std::string log_text;
    for (const std::string& line : d.log.lines()) {
        log_text += line + "\n";
    }
    write_text(dir / "run_log.txt", log_text);

    std::string checksums;
    for (const char* name : {"iq.sc16", "chirp_replica.cf32", "bursts.csv", "clock_sync.csv", "iq.sigmf-meta",
                             "config.yaml", "run_summary.json", "run_log.txt"}) {
        checksums += sha256_file_hex((dir / name).string()) + "  " + name + "\n";
    }
    write_text(dir / "checksums.sha256", checksums);
}

}  // namespace sar
