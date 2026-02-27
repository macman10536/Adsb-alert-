#pragma once
#include <Arduino.h>
#include <Preferences.h>

// Persistent storage wrappers around ESP32 NVS (Preferences)
namespace Storage {
    void begin();
    void end();

    // Calibration
    void     setCalibrationStart(uint64_t ts);
    uint64_t getCalibrationStart();
    void     setHatched(bool hatched);
    bool     getHatched();

    // MPU offsets — stored as raw bytes
    void setMpuOffsets(int16_t ax, int16_t ay, int16_t az,
                       int16_t gx, int16_t gy, int16_t gz);
    bool getMpuOffsets(int16_t &ax, int16_t &ay, int16_t &az,
                       int16_t &gx, int16_t &gy, int16_t &gz);
    bool hasMpuOffsets();

    // Baseline
    void  setRandRatio(float ratio);
    float getRandRatio();

    // Bloom filter
    bool   saveBloom(const uint8_t *data, size_t len);
    size_t loadBloom(uint8_t *data, size_t maxLen);

    // Gameplay state
    void    setHunger(uint8_t hunger);
    uint8_t getHunger();
    void    setMood(uint8_t mood);
    uint8_t getMood();
    void    setMacTotal(uint32_t count);
    uint32_t getMacTotal();
}
