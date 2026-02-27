#include "mood.h"
#include "config.h"

static Mood     s_mood          = Mood::CALM;
static Mood     s_transient     = Mood::CALM;
static bool     s_inTransient   = false;
static uint32_t s_transientEnd  = 0;

namespace MoodEngine {

void begin(Mood initial) {
    s_mood = initial;
}

void update(uint8_t hunger, MotionState motion,
            uint32_t newMacsThisScan, uint32_t recentMacs12h) {

    // Transient override (e.g. angry from shake, pet happy)
    if (s_inTransient) {
        if (millis() < s_transientEnd) {
            s_mood = s_transient;
            return;
        }
        s_inTransient = false;
    }

    // Shake -> angry (highest priority non-transient override)
    if (motion == MotionState::SHAKEN) {
        forceTransient(Mood::ANGRY, 5000);
        return;
    }

    // Night-time sleep (simple heuristic: if STATIONARY for a while
    // and hour is between 23:00-07:00, handled externally with forceTransient)

    // Hunger thresholds drive baseline mood
    if (hunger == 0) {
        s_mood = Mood::SHOCKED;
        return;
    }

    // Rich new environment this scan
    if (newMacsThisScan >= 10) {
        s_mood = Mood::EXCITED;
        return;
    }

    // Some new discoveries
    if (newMacsThisScan >= 3 || recentMacs12h >= 20) {
        s_mood = Mood::HAPPY;
        return;
    }

    // Well-fed at rest
    if (hunger > 60) {
        s_mood = Mood::CALM;
        return;
    }

    // Hungry but not critical
    if (hunger > 20) {
        s_mood = Mood::CALM;
        return;
    }

    // Very hungry
    s_mood = Mood::SHOCKED;
}

Mood getCurrent() {
    if (s_inTransient && millis() < s_transientEnd) return s_transient;
    return s_mood;
}

void forceTransient(Mood m, uint32_t durationMs) {
    s_transient    = m;
    s_transientEnd = millis() + durationMs;
    s_inTransient  = true;
    s_mood         = m;
}

} // namespace MoodEngine
