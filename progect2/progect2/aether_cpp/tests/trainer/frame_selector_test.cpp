// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/frame_selector.h"

#include <cmath>
#include <cstdio>
#include <cstring>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static bool near(float a, float b, float eps = 1e-4f) {
    return std::fabs(a - b) <= eps;
}

static void test_select_highest_gain() {
    aether::trainer::FrameSelectorConfig cfg{};
    cfg.max_cache_frames = 20;
    cfg.blur_threshold = 50.0f;

    aether::trainer::FrameSelector selector(cfg);

    for (int i = 0; i < 10; ++i) {
        aether::trainer::CameraFrame frame{};
        frame.frame_id = static_cast<std::uint64_t>(i);
        frame.information_gain = static_cast<float>(i) * 0.1f;
        frame.blur_score = 100.0f;  // All sharp.
        selector.add_frame(frame);
    }

    const auto* best = selector.select_next();
    CHECK(best != nullptr);
    // Frame 9 has the highest information gain (0.9).
    CHECK(best->frame_id == 9);
    CHECK(near(best->information_gain, 0.9f));
}

static void test_blur_rejection() {
    aether::trainer::FrameSelectorConfig cfg{};
    cfg.max_cache_frames = 20;
    cfg.blur_threshold = 50.0f;

    aether::trainer::FrameSelector selector(cfg);

    for (int i = 0; i < 5; ++i) {
        aether::trainer::CameraFrame frame{};
        frame.frame_id = static_cast<std::uint64_t>(i);
        frame.information_gain = 1.0f;  // All high gain.
        frame.blur_score = 30.0f;       // All blurry (below threshold).
        selector.add_frame(frame);
    }

    const auto* best = selector.select_next();
    // All frames blurry = nothing selectable.
    CHECK(best == nullptr);
}

static void test_cache_size_limit() {
    aether::trainer::FrameSelectorConfig cfg{};
    cfg.max_cache_frames = 5;
    cfg.blur_threshold = 0.0f;

    aether::trainer::FrameSelector selector(cfg);

    // Add more frames than the cache can hold.
    for (int i = 0; i < 20; ++i) {
        aether::trainer::CameraFrame frame{};
        frame.frame_id = static_cast<std::uint64_t>(i);
        frame.information_gain = static_cast<float>(i);
        frame.blur_score = 100.0f;
        selector.add_frame(frame);
    }

    CHECK(selector.cache_size() <= 5);

    // The best frame should still be the highest gain among cached ones.
    const auto* best = selector.select_next();
    CHECK(best != nullptr);
    CHECK(best->frame_id == 19);  // Most recent, highest gain.
}

static void test_clear_empties_cache() {
    aether::trainer::FrameSelectorConfig cfg{};
    cfg.max_cache_frames = 10;
    cfg.blur_threshold = 0.0f;

    aether::trainer::FrameSelector selector(cfg);

    for (int i = 0; i < 5; ++i) {
        aether::trainer::CameraFrame frame{};
        frame.frame_id = static_cast<std::uint64_t>(i);
        frame.information_gain = 1.0f;
        frame.blur_score = 100.0f;
        selector.add_frame(frame);
    }
    CHECK(selector.cache_size() == 5);

    selector.clear();
    CHECK(selector.cache_size() == 0);

    const auto* best = selector.select_next();
    CHECK(best == nullptr);
}

static void test_mixed_sharp_and_blurry() {
    aether::trainer::FrameSelectorConfig cfg{};
    cfg.max_cache_frames = 20;
    cfg.blur_threshold = 50.0f;

    aether::trainer::FrameSelector selector(cfg);

    // Frame 0: blurry but high gain.
    {
        aether::trainer::CameraFrame f{};
        f.frame_id = 0;
        f.information_gain = 10.0f;
        f.blur_score = 20.0f;
        selector.add_frame(f);
    }
    // Frame 1: sharp but lower gain.
    {
        aether::trainer::CameraFrame f{};
        f.frame_id = 1;
        f.information_gain = 5.0f;
        f.blur_score = 80.0f;
        selector.add_frame(f);
    }

    const auto* best = selector.select_next();
    CHECK(best != nullptr);
    // Should pick the sharp frame despite lower gain.
    CHECK(best->frame_id == 1);
}

int main() {
    test_select_highest_gain();
    test_blur_rejection();
    test_cache_size_limit();
    test_clear_empties_cache();
    test_mixed_sharp_and_blurry();

    if (g_failed == 0) {
        std::fprintf(stdout, "frame_selector_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "frame_selector_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
