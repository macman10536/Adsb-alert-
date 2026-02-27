#include "storage.h"
#include "config.h"

static Preferences prefs;

namespace Storage {

void begin() {
    prefs.begin(NVS_NS, false);
}

void end() {
    prefs.end();
}

// ─── Calibration ────────────────────────────────────────────────────────────

void setCalibrationStart(uint64_t ts) {
    prefs.putULong64(NVS_KEY_CAL_START, ts);
}

uint64_t getCalibrationStart() {
    return prefs.getULong64(NVS_KEY_CAL_START, 0);
}

void setHatched(bool hatched) {
    prefs.putBool(NVS_KEY_HATCHED, hatched);
}

bool getHatched() {
    return prefs.getBool(NVS_KEY_HATCHED, false);
}

// ─── MPU offsets ─────────────────────────────────────────────────────────────

struct MpuOffsets {
    int16_t ax, ay, az, gx, gy, gz;
};

void setMpuOffsets(int16_t ax, int16_t ay, int16_t az,
                   int16_t gx, int16_t gy, int16_t gz) {
    MpuOffsets off = {ax, ay, az, gx, gy, gz};
    prefs.putBytes(NVS_KEY_MPU_OFF, &off, sizeof(off));
}

bool getMpuOffsets(int16_t &ax, int16_t &ay, int16_t &az,
                   int16_t &gx, int16_t &gy, int16_t &gz) {
    MpuOffsets off;
    size_t len = prefs.getBytes(NVS_KEY_MPU_OFF, &off, sizeof(off));
    if (len != sizeof(off)) return false;
    ax = off.ax; ay = off.ay; az = off.az;
    gx = off.gx; gy = off.gy; gz = off.gz;
    return true;
}

bool hasMpuOffsets() {
    return prefs.getBytesLength(NVS_KEY_MPU_OFF) == sizeof(MpuOffsets);
}

// ─── Baseline ────────────────────────────────────────────────────────────────

void setRandRatio(float ratio) {
    prefs.putFloat(NVS_KEY_RAND_RATIO, ratio);
}

float getRandRatio() {
    return prefs.getFloat(NVS_KEY_RAND_RATIO, 0.5f);
}

// ─── Bloom filter ────────────────────────────────────────────────────────────

bool saveBloom(const uint8_t *data, size_t len) {
    return prefs.putBytes(NVS_KEY_BLOOM, data, len) == len;
}

size_t loadBloom(uint8_t *data, size_t maxLen) {
    return prefs.getBytes(NVS_KEY_BLOOM, data, maxLen);
}

// ─── Gameplay state ───────────────────────────────────────────────────────────

void setHunger(uint8_t hunger) {
    prefs.putUChar(NVS_KEY_HUNGER, hunger);
}

uint8_t getHunger() {
    return prefs.getUChar(NVS_KEY_HUNGER, 70);
}

void setMood(uint8_t mood) {
    prefs.putUChar(NVS_KEY_MOOD, mood);
}

uint8_t getMood() {
    return prefs.getUChar(NVS_KEY_MOOD, 0);
}

void setMacTotal(uint32_t count) {
    prefs.putULong(NVS_KEY_MAC_TOTAL, count);
}

uint32_t getMacTotal() {
    return prefs.getULong(NVS_KEY_MAC_TOTAL, 0);
}

} // namespace Storage
