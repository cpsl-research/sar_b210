#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace sar {

// Lowercase hex SHA-256, matching sha256sum.
std::string sha256_hex(const void* data, std::size_t size);
std::string sha256_file_hex(const std::string& path);

}  // namespace sar
