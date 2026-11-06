#pragma once

#include <mutex>
#include <string>
#include <vector>

namespace sar {

// Thread-safe text log, written to run_log.txt. Shared with the UHD logger, which can outlive a run.
class RunLog {
public:
    void add(const std::string& line) {
        std::lock_guard<std::mutex> lock(mutex_);
        lines_.push_back(line);
    }
    std::vector<std::string> lines() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return lines_;
    }

private:
    mutable std::mutex mutex_;
    std::vector<std::string> lines_;
};

}  // namespace sar
