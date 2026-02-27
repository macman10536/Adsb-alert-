#pragma once
#include <Arduino.h>
#include "motion.h"

enum class Mood : uint8_t {
    CALM    = 0,
    HAPPY   = 1,
    EXCITED = 2,
    SHOCKED = 3,
    SLEEPING= 4,
    ANGRY   = 5
};

namespace MoodEngine {
    void begin(Mood initial = Mood::CALM);

    // Update mood based on hunger, motion, recent discoveries.
    // newMacsThisScan: fresh MAC count from last BLE scan.
    // recentMacs12h: total new MACs seen in the last 12 hours.
    void update(uint8_t hunger,
                MotionState motion,
                uint32_t newMacsThisScan,
                uint32_t recentMacs12h);

    Mood getCurrent();

    // Force a transient mood (e.g. on button pet), reverts after duration
    void forceTransient(Mood m, uint32_t durationMs);
}
