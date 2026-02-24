// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/training_checkpoint.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static bool near(float a, float b, float eps = 1e-5f) {
    return std::fabs(a - b) <= eps;
}

static std::string temp_path(const char* suffix) {
    const char* tmp = std::getenv("TMPDIR");
    if (!tmp) tmp = "/tmp";
    std::string path = tmp;
    if (path.back() != '/') path += '/';
    path += "aether_ckpt_test_";
    path += suffix;
    path += ".bin";
    return path;
}

static void test_save_and_load_roundtrip() {
    const std::string path = temp_path("roundtrip");

    aether::trainer::TrainingCheckpoint ckpt{};
    ckpt.header.num_gaussians = 50;
    ckpt.header.optimizer_step = 42;
    ckpt.header.best_psnr = 32.5f;

    // Fill gaussian data.
    ckpt.gaussian_data.resize(50 * 96);  // 96 bytes per packed gaussian.
    for (std::size_t i = 0; i < ckpt.gaussian_data.size(); ++i) {
        ckpt.gaussian_data[i] = static_cast<std::uint8_t>(i & 0xFF);
    }
    // Fill optimizer state.
    ckpt.optimizer_state.resize(50 * 4);
    for (std::size_t i = 0; i < ckpt.optimizer_state.size(); ++i) {
        ckpt.optimizer_state[i] = static_cast<std::uint8_t>((i * 3) & 0xFF);
    }
    // Fill metrics.
    ckpt.metrics[0] = 32.5f;
    ckpt.metrics[1] = 0.95f;

    aether::trainer::CheckpointManager mgr;
    auto status = mgr.save(path.c_str(), ckpt);
    CHECK(aether::core::is_ok(status));

    aether::trainer::TrainingCheckpoint loaded{};
    status = mgr.load(path.c_str(), &loaded);
    CHECK(aether::core::is_ok(status));

    CHECK(loaded.header.num_gaussians == 50);
    CHECK(loaded.header.optimizer_step == 42);
    CHECK(near(loaded.header.best_psnr, 32.5f));
    CHECK(loaded.gaussian_data.size() == ckpt.gaussian_data.size());
    CHECK(loaded.optimizer_state.size() == ckpt.optimizer_state.size());
    CHECK(near(loaded.metrics[0], 32.5f));
    CHECK(near(loaded.metrics[1], 0.95f));

    // Verify gaussian data byte-for-byte.
    for (std::size_t i = 0; i < ckpt.gaussian_data.size(); ++i) {
        CHECK(loaded.gaussian_data[i] == ckpt.gaussian_data[i]);
    }

    std::remove(path.c_str());
}

static void test_load_nonexistent_file() {
    aether::trainer::CheckpointManager mgr;
    aether::trainer::TrainingCheckpoint ckpt{};
    auto status = mgr.load("/tmp/aether_nonexistent_checkpoint_xyz.bin", &ckpt);
    CHECK(!aether::core::is_ok(status));
}

static void test_save_best_only_improves() {
    const std::string dir = std::string(std::getenv("TMPDIR") ? std::getenv("TMPDIR") : "/tmp");

    aether::trainer::CheckpointManager mgr;

    // First checkpoint with PSNR 30.
    aether::trainer::TrainingCheckpoint ckpt1{};
    ckpt1.header.num_gaussians = 10;
    ckpt1.header.optimizer_step = 100;
    ckpt1.header.best_psnr = 30.0f;
    ckpt1.gaussian_data.resize(10);
    auto status = mgr.save_best(dir.c_str(), ckpt1);
    CHECK(aether::core::is_ok(status));

    // Second with lower PSNR: should not update.
    aether::trainer::TrainingCheckpoint ckpt2 = ckpt1;
    ckpt2.header.best_psnr = 28.0f;
    ckpt2.header.optimizer_step = 200;
    status = mgr.save_best(dir.c_str(), ckpt2);
    // This may return kOk even if it didn't update (depends on implementation),
    // but if we load, the loaded checkpoint should have psnr = 30 still.

    aether::trainer::TrainingCheckpoint loaded{};
    status = mgr.load_best(dir.c_str(), &loaded);
    CHECK(aether::core::is_ok(status));
    CHECK(near(loaded.header.best_psnr, 30.0f));
    CHECK(loaded.header.optimizer_step == 100);

    // Third with higher PSNR: should update.
    aether::trainer::TrainingCheckpoint ckpt3 = ckpt1;
    ckpt3.header.best_psnr = 35.0f;
    ckpt3.header.optimizer_step = 300;
    status = mgr.save_best(dir.c_str(), ckpt3);
    CHECK(aether::core::is_ok(status));

    status = mgr.load_best(dir.c_str(), &loaded);
    CHECK(aether::core::is_ok(status));
    CHECK(near(loaded.header.best_psnr, 35.0f));
    CHECK(loaded.header.optimizer_step == 300);

    // Clean up.
    std::string best_path = dir + "/best_checkpoint.aeth";
    std::remove(best_path.c_str());
}

static void test_header_magic_and_version() {
    aether::trainer::CheckpointHeader header{};
    CHECK(header.magic == aether::trainer::kCheckpointMagic);
    CHECK(header.version == aether::trainer::kCheckpointVersion);
}

int main() {
    test_save_and_load_roundtrip();
    test_load_nonexistent_file();
    test_save_best_only_improves();
    test_header_magic_and_version();

    if (g_failed == 0) {
        std::fprintf(stdout, "training_checkpoint_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "training_checkpoint_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
