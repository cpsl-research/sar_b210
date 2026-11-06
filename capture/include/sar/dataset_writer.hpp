#pragma once

#include <optional>
#include <string>
#include <vector>

#include "sar/burst_record.hpp"
#include "sar/chirp.hpp"
#include "sar/ram_buffer.hpp"
#include "sar/run_config.hpp"
#include "sar/run_log.hpp"

namespace sar {

struct DatasetContents {
    const RunConfig& config;
    const std::vector<Sample>& chirp;
    const RamBuffer& iq;
    const std::vector<BurstRecord>& bursts;
    const std::vector<ClockSyncRecord>& clock_sync;
    const RunLog& log;
    bool transmit;
    const std::optional<std::string>& fatal_error;
};

// Writes a new run folder in the layout read by src/b210_sar/dataset.py, syncs it to disk, then
// writes checksums. Refuses to overwrite an existing path or start on a disk that is too small.
void write_dataset(const std::string& run_dir, const DatasetContents& contents);

}  // namespace sar
