#pragma once

#include <memory>

#include "sar/radio.hpp"
#include "sar/run_config.hpp"
#include "sar/run_log.hpp"

namespace sar {

// Opens and configures the USRP from the config, setting device time per timing.sync_mode.
// With transmit false no TX streamer exists, so the radio cannot emit a burst.
std::unique_ptr<Radio> make_uhd_radio(const RunConfig& config, std::shared_ptr<RunLog> log, bool transmit);

}  // namespace sar
