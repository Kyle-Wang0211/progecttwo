// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/core/nan_quarantine.h"

#include <cmath>

namespace aether {
namespace core {

bool detect_nan(const float* data, std::size_t count) {
    if (data == nullptr) {
        return false;
    }
    for (std::size_t i = 0; i < count; ++i) {
        if (std::isnan(data[i]) || std::isinf(data[i])) {
            return true;
        }
    }
    return false;
}

bool detect_nan_strided(const float* data, std::size_t count, std::size_t stride) {
    if (data == nullptr || stride == 0) {
        return false;
    }
    for (std::size_t i = 0; i < count; ++i) {
        const float v = data[i * stride];
        if (std::isnan(v) || std::isinf(v)) {
            return true;
        }
    }
    return false;
}

QuarantineAction scan_buffer(const float* data, std::size_t count) {
    QuarantineAction action{};
    if (data == nullptr) {
        return action;
    }
    for (std::size_t i = 0; i < count; ++i) {
        if (std::isnan(data[i])) {
            ++action.nan_count;
        } else if (std::isinf(data[i])) {
            ++action.inf_count;
        }
    }
    action.triggered = (action.nan_count > 0) || (action.inf_count > 0);
    return action;
}

void NaNQuarantine::check(const float* gradients, std::size_t grad_count,
                           const float* params, std::size_t param_count) {
    QuarantineAction grad_action = scan_buffer(gradients, grad_count);
    QuarantineAction param_action = scan_buffer(params, param_count);

    last_action_.nan_count = grad_action.nan_count + param_action.nan_count;
    last_action_.inf_count = grad_action.inf_count + param_action.inf_count;
    last_action_.triggered = (last_action_.nan_count > 0) || (last_action_.inf_count > 0);
    triggered_ = last_action_.triggered;

    if (triggered_) {
        ++quarantine_count_;
    }
}

void NaNQuarantine::reset() {
    triggered_ = false;
    last_action_ = QuarantineAction{};
    quarantine_count_ = 0;
}

}  // namespace core
}  // namespace aether
