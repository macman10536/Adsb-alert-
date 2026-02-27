#include "calibration.h"
#include "storage.h"
#include "config.h"

static uint64_t s_startMs    = 0;
static uint32_t s_macCount   = 0;
static uint32_t s_stableCount= 0;
static uint32_t s_randCount  = 0;
static float    s_randRatio  = 0.5f;

namespace Calibration {

bool begin() {
    if (Storage::getHatched()) return true;  // already complete

    s_startMs = Storage::getCalibrationStart();
    if (s_startMs == 0) {
        // Very first boot — record start timestamp as millis() offset
        // Use ESP32 epoch: store as ms since boot, adjusted later
        s_startMs = (uint64_t)millis();
        Storage::setCalibrationStart(s_startMs);
    }

    s_randRatio = Storage::getRandRatio();
    s_macCount  = Storage::getMacTotal();

    return false;
}

void onMacDiscovered(bool isStable) {
    s_macCount++;
    if (isStable) s_stableCount++;
    else          s_randCount++;

    // Update rolling ratio
    uint32_t total = s_stableCount + s_randCount;
    if (total > 0) {
        s_randRatio = (float)s_randCount / (float)total;
    }
}

bool isComplete() {
    if (remainingMs() > 0) return false;
    return s_macCount >= CALIBRATION_MIN_MACS;
}

uint8_t progressPercent() {
    uint64_t elapsed = (uint64_t)millis() - s_startMs;
    if (elapsed >= CALIBRATION_DURATION_MS) return 100;
    return (uint8_t)(elapsed * 100ULL / CALIBRATION_DURATION_MS);
}

uint32_t remainingMs() {
    uint64_t elapsed = (uint64_t)millis() - s_startMs;
    if (elapsed >= CALIBRATION_DURATION_MS) return 0;
    return (uint32_t)(CALIBRATION_DURATION_MS - elapsed);
}

float randRatio() {
    return s_randRatio;
}

uint32_t macCount() {
    return s_macCount;
}

void lock() {
    Storage::setRandRatio(s_randRatio);
    Storage::setMacTotal(s_macCount);
    Storage::setHatched(true);
}

} // namespace Calibration
