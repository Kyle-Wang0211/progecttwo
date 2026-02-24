// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_CORE_NAN_QUARANTINE_H
#define AETHER_CORE_NAN_QUARANTINE_H

#ifdef __cplusplus

#include <cstddef>
#include <cstdint>

namespace aether {
namespace core {

// ---------------------------------------------------------------------------
// Free functions: lightweight NaN/Inf detection
// ---------------------------------------------------------------------------

/// Scan a contiguous float buffer for any NaN or Inf values.
bool detect_nan(const float* data, std::size_t count);

/// Scan a strided float buffer for any NaN or Inf values.
/// Checks elements at indices 0, stride, 2*stride, ... up to count elements.
bool detect_nan_strided(const float* data, std::size_t count, std::size_t stride);

// ---------------------------------------------------------------------------
// Detailed scan result
// ---------------------------------------------------------------------------

struct QuarantineAction {
    bool triggered{false};
    std::uint32_t nan_count{0};
    std::uint32_t inf_count{0};
};

/// Perform a detailed scan returning separate NaN and Inf counts.
QuarantineAction scan_buffer(const float* data, std::size_t count);

// ---------------------------------------------------------------------------
// NaNQuarantine: stateful checker for gradient + parameter buffers
// ---------------------------------------------------------------------------

class NaNQuarantine {
public:
    NaNQuarantine() = default;

    /// Check both gradient and parameter buffers for NaN/Inf.
    /// If either contains bad values, the quarantine is triggered.
    void check(const float* gradients, std::size_t grad_count,
               const float* params, std::size_t param_count);

    /// Returns true if the last check found NaN/Inf values.
    bool triggered() const { return triggered_; }

    /// Returns the detailed action from the last check.
    QuarantineAction last_action() const { return last_action_; }

    /// Returns total number of times quarantine has been triggered.
    std::uint32_t quarantine_count() const { return quarantine_count_; }

    /// Reset all state.
    void reset();

private:
    bool triggered_{false};
    QuarantineAction last_action_{};
    std::uint32_t quarantine_count_{0};
};

}  // namespace core
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_CORE_NAN_QUARANTINE_H
