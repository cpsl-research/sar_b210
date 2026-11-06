#include "sar/uhd_radio.hpp"

#include <uhd/stream.hpp>
#include <uhd/types/time_spec.hpp>
#include <uhd/types/tune_request.hpp>
#include <uhd/usrp/multi_usrp.hpp>
#include <uhd/utils/log_add.hpp>
#include <uhd/utils/thread.hpp>

#include <chrono>
#include <cmath>
#include <ctime>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>

#include "sar/burst_record.hpp"

namespace sar {
namespace {

constexpr size_t kChannel = 0;
constexpr double kRateToleranceHz = 1.0;
constexpr double kStreamTimeoutS = 1.0;  // far beyond any command lead; expiry means a real fault
constexpr double kAckTimeoutS = 0.25;
constexpr double kDrainTimeoutS = 0.02;

void require_rate(const char* what, double wanted, double actual) {
    if (std::abs(wanted - actual) > kRateToleranceHz) {
        std::ostringstream message;
        message << what << ": wanted " << wanted << " Hz, device gave " << actual << " Hz";
        throw std::runtime_error(message.str());
    }
}

class UhdRadio final : public Radio {
public:
    UhdRadio(const RunConfig& c, std::shared_ptr<RunLog> log, bool transmit)
        : tick_rate_(c.device.master_clock_rate_hz), log_(std::move(log)) {
        const std::shared_ptr<RunLog> sink = log_;
        uhd::log::add_logger("b210_sar", [sink](const uhd::log::logging_info& info) {
            if (info.verbosity >= uhd::log::warning) {
                sink->add("[UHD " + info.component + "] " + info.message);
            }
        });

        std::ostringstream args;
        args.precision(15);
        args << c.device.args << ",master_clock_rate=" << c.device.master_clock_rate_hz;
        usrp_ = uhd::usrp::multi_usrp::make(uhd::device_addr_t(args.str()));
        require_rate("master clock", c.device.master_clock_rate_hz, usrp_->get_master_clock_rate());

        usrp_->set_rx_rate(c.waveform.sample_rate_hz, kChannel);
        usrp_->set_tx_rate(c.waveform.sample_rate_hz, kChannel);
        require_rate("RX rate", c.waveform.sample_rate_hz, usrp_->get_rx_rate(kChannel));
        require_rate("TX rate", c.waveform.sample_rate_hz, usrp_->get_tx_rate(kChannel));

        usrp_->set_rx_antenna(c.rf.rx_antenna, kChannel);
        usrp_->set_tx_antenna(c.rf.tx_antenna, kChannel);

        // Fixed gains only: no AGC, same values for every step. Some devices have no AGC to disable.
        try {
            usrp_->set_rx_agc(false, kChannel);
        } catch (const uhd::exception&) {
        }
        usrp_->set_rx_gain(c.rf.rx_gain_db, kChannel);
        usrp_->set_tx_gain(c.rf.tx_gain_db, kChannel);
        rx_gain_db_ = usrp_->get_rx_gain(kChannel);
        tx_gain_db_ = usrp_->get_tx_gain(kChannel);

        const double bandwidth = c.waveform.sample_rate_hz;
        usrp_->set_rx_bandwidth(usrp_->get_rx_bandwidth_range(kChannel).clip(bandwidth), kChannel);
        usrp_->set_tx_bandwidth(usrp_->get_tx_bandwidth_range(kChannel).clip(bandwidth), kChannel);

        set_device_time(c.timing.sync_mode);

        uhd::stream_args_t stream_args("sc16", "sc16");
        stream_args.channels = {kChannel};
        rx_stream_ = usrp_->get_rx_stream(stream_args);
        if (transmit) {
            tx_stream_ = usrp_->get_tx_stream(stream_args);
        }
        log_->add(usrp_->get_pp_string());
    }

    Tuned tune(double freq_hz, double lo_offset_hz) override {
        const uhd::tune_request_t request(freq_hz, lo_offset_hz);
        usrp_->set_tx_freq(request, kChannel);
        usrp_->set_rx_freq(request, kChannel);
        return {usrp_->get_tx_freq(kChannel), usrp_->get_rx_freq(kChannel)};
    }

    bool lo_locked() override {
        return usrp_->get_tx_sensor("lo_locked", kChannel).to_bool() &&
               usrp_->get_rx_sensor("lo_locked", kChannel).to_bool();
    }

    int64_t time_now_ticks() override { return usrp_->get_time_now().to_ticks(tick_rate_); }

    uint32_t send_burst(const Sample* samples, std::size_t count, int64_t start_tick) override {
        if (!tx_stream_) {
            throw std::logic_error("send_burst on a radio opened without transmit");
        }
        uhd::tx_metadata_t metadata;
        metadata.start_of_burst = true;
        metadata.end_of_burst = true;
        metadata.has_time_spec = true;
        metadata.time_spec = uhd::time_spec_t::from_ticks(start_tick, tick_rate_);

        uint32_t flags = kBurstOk;
        const std::size_t sent = tx_stream_->send(samples, count, metadata, kStreamTimeoutS);
        if (sent != count) {
            flags |= kBurstTxShort;
            uhd::tx_metadata_t end;
            end.end_of_burst = true;
            tx_stream_->send("", 0, end, kAckTimeoutS);
        }

        uhd::async_metadata_t event;
        while (tx_stream_->recv_async_msg(event, kAckTimeoutS)) {
            switch (event.event_code) {
                case uhd::async_metadata_t::EVENT_CODE_BURST_ACK:
                    return flags;
                case uhd::async_metadata_t::EVENT_CODE_UNDERFLOW:
                case uhd::async_metadata_t::EVENT_CODE_UNDERFLOW_IN_PACKET:
                    flags |= kBurstTxUnderflow;
                    break;
                case uhd::async_metadata_t::EVENT_CODE_SEQ_ERROR:
                case uhd::async_metadata_t::EVENT_CODE_SEQ_ERROR_IN_BURST:
                    flags |= kBurstTxSequenceError;
                    break;
                case uhd::async_metadata_t::EVENT_CODE_TIME_ERROR:
                    flags |= kBurstTxLate;
                    break;
                default:
                    break;
            }
        }
        return flags | kBurstTxNoAck;
    }

    Received receive_burst(Sample* samples, std::size_t count, int64_t start_tick) override {
        uhd::stream_cmd_t command(uhd::stream_cmd_t::STREAM_MODE_NUM_SAMPS_AND_DONE);
        command.num_samps = count;
        command.stream_now = false;
        command.time_spec = uhd::time_spec_t::from_ticks(start_tick, tick_rate_);
        rx_stream_->issue_stream_cmd(command);

        Received result{kBurstOk, 0, -1};
        uhd::rx_metadata_t metadata;
        while (result.num_samples < count) {
            const std::size_t got =
                rx_stream_->recv(samples + result.num_samples, count - result.num_samples, metadata, kStreamTimeoutS);
            if (got > 0 && result.first_tick < 0 && metadata.has_time_spec) {
                result.first_tick = metadata.time_spec.to_ticks(tick_rate_);
            }
            result.num_samples += got;

            switch (metadata.error_code) {
                case uhd::rx_metadata_t::ERROR_CODE_NONE:
                    break;
                case uhd::rx_metadata_t::ERROR_CODE_TIMEOUT:
                    result.error_flags |= kBurstTimeout;
                    break;
                case uhd::rx_metadata_t::ERROR_CODE_LATE_COMMAND:
                    result.error_flags |= kBurstLateCommand;
                    break;
                case uhd::rx_metadata_t::ERROR_CODE_OVERFLOW:
                    result.error_flags |= metadata.out_of_sequence ? kBurstSequenceError : kBurstOverflow;
                    break;
                case uhd::rx_metadata_t::ERROR_CODE_BROKEN_CHAIN:
                    result.error_flags |= kBurstSequenceError;
                    break;
                default:
                    result.error_flags |= kBurstRxBadPacket;
                    break;
            }
            if (result.error_flags != kBurstOk) {
                stop_and_drain();
                break;
            }
        }
        return result;
    }

    double tx_gain_db() override { return tx_gain_db_; }
    double rx_gain_db() override { return rx_gain_db_; }

    void prepare_streaming_thread() override {
        if (!uhd::set_thread_priority_safe()) {
            log_->add("Could not raise streaming thread priority; check the rtprio limit");
        }
    }

private:
    void set_device_time(const std::string& sync_mode) {
        if (sync_mode == "host_clock") {
            usrp_->set_time_now(uhd::time_spec_t(0.0));
            return;
        }
        // PPS: put the device on the host's realtime second, which must itself be disciplined.
        usrp_->set_time_source("external");
        const uhd::time_spec_t last = usrp_->get_time_last_pps();
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (usrp_->get_time_last_pps() == last) {
            if (std::chrono::steady_clock::now() > deadline) {
                throw std::runtime_error("sync_mode is pps but no PPS edge arrived within 3 s");
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        usrp_->set_time_next_pps(uhd::time_spec_t(static_cast<double>(std::time(nullptr) + 1)));
        std::this_thread::sleep_for(std::chrono::milliseconds(1100));
    }

    void stop_and_drain() {
        uhd::stream_cmd_t stop(uhd::stream_cmd_t::STREAM_MODE_STOP_CONTINUOUS);
        stop.stream_now = true;
        rx_stream_->issue_stream_cmd(stop);

        std::vector<Sample> scratch(16384);
        uhd::rx_metadata_t metadata;
        while (rx_stream_->recv(scratch.data(), scratch.size(), metadata, kDrainTimeoutS) > 0 ||
               metadata.error_code != uhd::rx_metadata_t::ERROR_CODE_TIMEOUT) {
        }
    }

    double tick_rate_;
    std::shared_ptr<RunLog> log_;
    uhd::usrp::multi_usrp::sptr usrp_;
    uhd::rx_streamer::sptr rx_stream_;
    uhd::tx_streamer::sptr tx_stream_;
    double tx_gain_db_ = 0;
    double rx_gain_db_ = 0;
};

}  // namespace

std::unique_ptr<Radio> make_uhd_radio(const RunConfig& config, std::shared_ptr<RunLog> log, bool transmit) {
    return std::make_unique<UhdRadio>(config, std::move(log), transmit);
}

}  // namespace sar
