#pragma once
#include <Arduino.h>
#include <U8g2lib.h>
#include "mood.h"
#include "config.h"

// Screen IDs for status cycling
enum class Screen {
    FACE,
    HUNGER_INDICATOR,
    TIME_DISPLAY,
    BT_COUNT,
    NOVELTY_SCORE,
    DIAGNOSTIC,
    EGG,
    EGG_CALIBRATION_TIMER
};

namespace Display {
    void begin();
    void wake();
    void sleep();
    bool isAwake();
    void checkAutoOff();       // call every loop tick

    // Draw current face for current mood
    void drawFace(Mood mood);

    // Egg phase rendering
    void drawEgg(uint8_t crackPercent, bool wobble, bool showEyes, bool heartbeat);
    void drawEggCalibration(uint32_t remainingMs);

    // Status screens
    void drawHungerIndicator(uint8_t hunger);
    void drawBtCount(uint32_t today, uint32_t lifetime);
    void drawNoveltyScore(uint8_t score);
    void drawDiagnostic(uint32_t calRemMs, uint32_t freeRam, uint32_t macTotal,
                        const char *version);

    // Wipe and signal a redraw is needed
    void markDirty();
    bool isDirty();
}
