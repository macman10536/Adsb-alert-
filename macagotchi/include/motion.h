#pragma once
#include <Arduino.h>

enum class MotionState {
    STATIONARY,
    CARRIED,
    IN_TRANSIT,
    SHAKEN
};

namespace Motion {
    // Init MPU6050. If calibrate=true, performs zero-point calibration
    // (~3-5 seconds of stillness required). Saves result to NVS.
    bool begin(bool calibrate);

    // Load previously saved calibration offsets from NVS
    bool loadCalibration();

    // Run calibration routine synchronously (~4 seconds).
    // Returns true on success, writes offsets to NVS.
    bool runCalibration();

    // Call in main loop — updates internal motion state
    void update();

    MotionState getState();

    // Raw acceleration magnitude (g * 1000)
    int32_t getAccelMag();
}
