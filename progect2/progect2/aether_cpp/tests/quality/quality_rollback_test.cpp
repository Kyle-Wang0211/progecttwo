// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/quality_rollback.h"

#include <cmath>
#include <cstdio>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static aether::quality::RollbackConfig make_default_config() {
    aether::quality::RollbackConfig cfg{};
    cfg.drop_threshold = 2.0f;
    cfg.consecutive_frames = 10;
    return cfg;
}

static void test_no_rollback_stable_psnr() {
    aether::quality::QualityRollbackMonitor monitor(make_default_config());

    // Feed 20 frames with stable PSNR.
    for (int i = 0; i < 20; ++i) {
        monitor.update(32.0f);
    }
    CHECK(!monitor.triggered());
}

static void test_no_rollback_few_drops() {
    aether::quality::QualityRollbackMonitor monitor(make_default_config());

    // Start with good PSNR.
    for (int i = 0; i < 5; ++i) {
        monitor.update(35.0f);
    }
    // A few frames of drop, but not 10 consecutive.
    for (int i = 0; i < 5; ++i) {
        monitor.update(32.5f);
    }
    // Recover.
    monitor.update(35.0f);

    CHECK(!monitor.triggered());
}

static void test_rollback_triggers_after_10_consecutive_drops() {
    aether::quality::QualityRollbackMonitor monitor(make_default_config());

    // Establish baseline at 35 dB.
    for (int i = 0; i < 5; ++i) {
        monitor.update(35.0f);
    }

    // 10 consecutive frames with PSNR drop > 2 dB.
    for (int i = 0; i < 10; ++i) {
        monitor.update(32.0f);  // 3 dB drop from 35.
    }

    CHECK(monitor.triggered());
}

static void test_triggered_stays_until_reset() {
    aether::quality::QualityRollbackMonitor monitor(make_default_config());

    // Establish baseline.
    for (int i = 0; i < 5; ++i) {
        monitor.update(35.0f);
    }
    // Trigger rollback.
    for (int i = 0; i < 10; ++i) {
        monitor.update(30.0f);
    }
    CHECK(monitor.triggered());

    // Even if PSNR recovers, triggered should stay true.
    for (int i = 0; i < 10; ++i) {
        monitor.update(35.0f);
    }
    CHECK(monitor.triggered());

    // Only reset clears it.
    monitor.reset();
    CHECK(!monitor.triggered());
}

static void test_gradual_decline_does_not_trigger() {
    aether::quality::QualityRollbackMonitor monitor(make_default_config());

    // Very gradual decline: < 2 dB per step.
    float psnr = 35.0f;
    for (int i = 0; i < 20; ++i) {
        monitor.update(psnr);
        psnr -= 0.1f;  // Only 0.1 dB drop each frame.
    }
    CHECK(!monitor.triggered());
}

int main() {
    test_no_rollback_stable_psnr();
    test_no_rollback_few_drops();
    test_rollback_triggers_after_10_consecutive_drops();
    test_triggered_stays_until_reset();
    test_gradual_decline_does_not_trigger();

    if (g_failed == 0) {
        std::fprintf(stdout, "quality_rollback_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "quality_rollback_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
