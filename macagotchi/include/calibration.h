#pragma once
#include <Arduino.h>

// Egg / world calibration phase logic.
// The device spends 48 hours observing the environment to build a baseline.
namespace Calibration {
    // Call at boot. Returns true if calibration is complete (hatched).
    bool begin();

    // Record a new MAC discovery during calibration.
    // isStable: true for OUI-registered MAC.
    void onMacDiscovered(bool isStable);

    // Returns true when 48h elapsed AND minimum MAC threshold met.
    bool isComplete();

    // 0-100 percentage progress (for crack animations)
    uint8_t progressPercent();

    // Remaining milliseconds
    uint32_t remainingMs();

    // Current baseline randomised ratio (updated incrementally)
    float randRatio();

    // Total MACs seen during calibration
    uint32_t macCount();

    // Lock in the baseline and mark as hatched in NVS
    void lock();
}
