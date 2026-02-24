// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/gaussian_gradient_buffer.h"

#include <cstring>

namespace aether {
namespace trainer {

core::Status GaussianGradientBuffer::init(std::size_t max_gaussians) {
    if (max_gaussians == 0) {
        return core::Status::kInvalidArgument;
    }

    const std::size_t total_floats = max_gaussians * kGradientsPerGaussian;
    buffer_a_.resize(total_floats, 0.0f);
    buffer_b_.resize(total_floats, 0.0f);
    current_write_ = 0;
    max_gaussians_ = max_gaussians;

    return core::Status::kOk;
}

void GaussianGradientBuffer::swap() {
    current_write_ = (current_write_ == 0) ? 1 : 0;
}

void GaussianGradientBuffer::clear_write_buffer() {
    if (max_gaussians_ == 0) {
        return;
    }
    std::vector<float>& buf = (current_write_ == 0) ? buffer_a_ : buffer_b_;
    std::memset(buf.data(), 0, buf.size() * sizeof(float));
}

float* GaussianGradientBuffer::write_data() {
    if (max_gaussians_ == 0) {
        return nullptr;
    }
    return (current_write_ == 0) ? buffer_a_.data() : buffer_b_.data();
}

const float* GaussianGradientBuffer::read_data() const {
    if (max_gaussians_ == 0) {
        return nullptr;
    }
    return (current_write_ == 0) ? buffer_b_.data() : buffer_a_.data();
}

GradientSlice GaussianGradientBuffer::write_slice(std::size_t gaussian_index) {
    GradientSlice slice{};
    slice.position = nullptr;
    slice.scale = nullptr;
    slice.opacity = nullptr;
    slice.rotation = nullptr;
    slice.sh_dc = nullptr;
    slice.sh_rest = nullptr;

    if (gaussian_index >= max_gaussians_) {
        return slice;
    }

    float* base = write_data();
    if (base == nullptr) {
        return slice;
    }

    float* g = base + gaussian_index * kGradientsPerGaussian;
    slice.position = g + kOffsetPosition;
    slice.scale    = g + kOffsetScale;
    slice.opacity  = g + kOffsetOpacity;
    slice.rotation = g + kOffsetRotation;
    slice.sh_dc    = g + kOffsetSHDC;
    slice.sh_rest  = g + kOffsetSHRest;

    return slice;
}

}  // namespace trainer
}  // namespace aether
