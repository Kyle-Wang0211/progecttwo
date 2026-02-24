// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/training_checkpoint.h"

#include <cstdio>
#include <cstring>
#include <string>

namespace aether {
namespace trainer {
namespace {

/// Construct the "best checkpoint" file path from a directory.
std::string best_checkpoint_path(const char* dir) {
    std::string path(dir);
    if (!path.empty() && path.back() != '/') {
        path += '/';
    }
    path += "best_checkpoint.aeth";
    return path;
}

/// RAII wrapper for FILE* to ensure fclose is called.
class FileGuard {
public:
    explicit FileGuard(std::FILE* f) : f_(f) {}
    ~FileGuard() {
        if (f_ != nullptr) {
            std::fclose(f_);
        }
    }
    FileGuard(const FileGuard&) = delete;
    FileGuard& operator=(const FileGuard&) = delete;

    std::FILE* get() const { return f_; }
    bool valid() const { return f_ != nullptr; }

private:
    std::FILE* f_{nullptr};
};

/// Write raw bytes to file.
bool write_bytes(std::FILE* f, const void* data, std::size_t size) {
    if (size == 0) {
        return true;
    }
    return std::fwrite(data, 1, size, f) == size;
}

/// Read raw bytes from file.
bool read_bytes(std::FILE* f, void* data, std::size_t size) {
    if (size == 0) {
        return true;
    }
    return std::fread(data, 1, size, f) == size;
}

/// Write a uint64 size prefix followed by data.
bool write_sized_blob(std::FILE* f, const std::vector<std::uint8_t>& data) {
    const std::uint64_t size = data.size();
    if (!write_bytes(f, &size, sizeof(size))) {
        return false;
    }
    if (size > 0) {
        return write_bytes(f, data.data(), static_cast<std::size_t>(size));
    }
    return true;
}

/// Read a uint64 size prefix followed by data.
bool read_sized_blob(std::FILE* f, std::vector<std::uint8_t>* data) {
    std::uint64_t size = 0;
    if (!read_bytes(f, &size, sizeof(size))) {
        return false;
    }

    // Sanity check: reject absurdly large blobs (>1 GiB).
    constexpr std::uint64_t kMaxBlobSize = 1ULL << 30;
    if (size > kMaxBlobSize) {
        return false;
    }

    data->resize(static_cast<std::size_t>(size));
    if (size > 0) {
        return read_bytes(f, data->data(), static_cast<std::size_t>(size));
    }
    return true;
}

}  // namespace

core::Status CheckpointManager::save(
    const char* path,
    const TrainingCheckpoint& checkpoint) {

    if (path == nullptr) {
        return core::Status::kInvalidArgument;
    }

    FileGuard file(std::fopen(path, "wb"));
    if (!file.valid()) {
        return core::Status::kResourceExhausted;
    }

    // Write header.
    if (!write_bytes(file.get(), &checkpoint.header, sizeof(CheckpointHeader))) {
        return core::Status::kResourceExhausted;
    }

    // Write gaussian data blob.
    if (!write_sized_blob(file.get(), checkpoint.gaussian_data)) {
        return core::Status::kResourceExhausted;
    }

    // Write optimizer state blob.
    if (!write_sized_blob(file.get(), checkpoint.optimizer_state)) {
        return core::Status::kResourceExhausted;
    }

    // Write metrics array.
    if (!write_bytes(file.get(), checkpoint.metrics,
                     sizeof(float) * kCheckpointMetricSlots)) {
        return core::Status::kResourceExhausted;
    }

    return core::Status::kOk;
}

core::Status CheckpointManager::load(
    const char* path,
    TrainingCheckpoint* checkpoint) {

    if (path == nullptr || checkpoint == nullptr) {
        return core::Status::kInvalidArgument;
    }

    FileGuard file(std::fopen(path, "rb"));
    if (!file.valid()) {
        return core::Status::kResourceExhausted;
    }

    // Read header.
    CheckpointHeader header{};
    if (!read_bytes(file.get(), &header, sizeof(CheckpointHeader))) {
        return core::Status::kInvalidArgument;
    }

    // Validate magic number.
    if (header.magic != kCheckpointMagic) {
        return core::Status::kInvalidArgument;
    }

    // Validate version (forward compatibility: allow loading older versions).
    if (header.version > kCheckpointVersion) {
        return core::Status::kInvalidArgument;
    }

    checkpoint->header = header;

    // Read gaussian data blob.
    if (!read_sized_blob(file.get(), &checkpoint->gaussian_data)) {
        return core::Status::kInvalidArgument;
    }

    // Read optimizer state blob.
    if (!read_sized_blob(file.get(), &checkpoint->optimizer_state)) {
        return core::Status::kInvalidArgument;
    }

    // Read metrics array.
    if (!read_bytes(file.get(), checkpoint->metrics,
                    sizeof(float) * kCheckpointMetricSlots)) {
        return core::Status::kInvalidArgument;
    }

    return core::Status::kOk;
}

core::Status CheckpointManager::save_best(
    const char* dir,
    const TrainingCheckpoint& checkpoint) {

    if (dir == nullptr) {
        return core::Status::kInvalidArgument;
    }

    // Only save if this checkpoint has a better PSNR than the current best.
    if (checkpoint.header.best_psnr <= best_psnr_) {
        return core::Status::kOk;
    }

    const std::string path = best_checkpoint_path(dir);
    core::Status status = save(path.c_str(), checkpoint);
    if (core::is_ok(status)) {
        best_psnr_ = checkpoint.header.best_psnr;
    }
    return status;
}

core::Status CheckpointManager::load_best(
    const char* dir,
    TrainingCheckpoint* checkpoint) {

    if (dir == nullptr || checkpoint == nullptr) {
        return core::Status::kInvalidArgument;
    }

    const std::string path = best_checkpoint_path(dir);
    core::Status status = load(path.c_str(), checkpoint);
    if (core::is_ok(status)) {
        best_psnr_ = checkpoint->header.best_psnr;
    }
    return status;
}

}  // namespace trainer
}  // namespace aether
