#include "motion.h"
#include "storage.h"
#include "config.h"
#include <MPU6050.h>

static MPU6050    s_mpu;
static MotionState s_state    = MotionState::STATIONARY;
static int32_t    s_accelMag  = 0;

// Rolling variance for motion classification
static const uint8_t HISTORY  = 16;
static int32_t s_magHistory[HISTORY] = {};
static uint8_t s_histIdx = 0;

// Thresholds (empirical — tunable)
static const int32_t SHAKE_THRESHOLD   = 2500;  // >2.5g instantaneous
static const int32_t CARRIED_VARIANCE  = 800;
static const int32_t TRANSIT_VARIANCE  = 200;

namespace Motion {

bool begin(bool calibrate) {
    Wire.begin(PIN_SDA, PIN_SCL);
    s_mpu.initialize();
    if (!s_mpu.testConnection()) return false;

    if (calibrate) {
        return runCalibration();
    }
    return loadCalibration();
}

bool loadCalibration() {
    int16_t ax, ay, az, gx, gy, gz;
    if (!Storage::getMpuOffsets(ax, ay, az, gx, gy, gz)) return false;
    s_mpu.setXAccelOffset(ax); s_mpu.setYAccelOffset(ay); s_mpu.setZAccelOffset(az);
    s_mpu.setXGyroOffset(gx);  s_mpu.setYGyroOffset(gy);  s_mpu.setZGyroOffset(gz);
    return true;
}

bool runCalibration() {
    // Simple average-based zero calibration over ~4 seconds
    const int SAMPLES = 200;
    long sumAx = 0, sumAy = 0, sumAz = 0;
    long sumGx = 0, sumGy = 0, sumGz = 0;

    int16_t ax, ay, az, gx, gy, gz;
    for (int i = 0; i < SAMPLES; i++) {
        s_mpu.getMotion6(&ax, &ay, &az, &gx, &gy, &gz);
        sumAx += ax; sumAy += ay; sumAz += az;
        sumGx += gx; sumGy += gy; sumGz += gz;
        delay(20);
    }

    // Offsets to zero: accelerometer should read (0,0,16384) at rest (1g on Z)
    int16_t offAx = -(sumAx / SAMPLES);
    int16_t offAy = -(sumAy / SAMPLES);
    int16_t offAz = -(sumAz / SAMPLES) + 16384;
    int16_t offGx = -(sumGx / SAMPLES);
    int16_t offGy = -(sumGy / SAMPLES);
    int16_t offGz = -(sumGz / SAMPLES);

    s_mpu.setXAccelOffset(offAx); s_mpu.setYAccelOffset(offAy); s_mpu.setZAccelOffset(offAz);
    s_mpu.setXGyroOffset(offGx);  s_mpu.setYGyroOffset(offGy);  s_mpu.setZGyroOffset(offGz);

    Storage::setMpuOffsets(offAx, offAy, offAz, offGx, offGy, offGz);
    return true;
}

void update() {
    int16_t ax, ay, az, gx, gy, gz;
    s_mpu.getMotion6(&ax, &ay, &az, &gx, &gy, &gz);

    // Magnitude of acceleration vector (in raw units ~16384 = 1g)
    int32_t mag = (int32_t)sqrt((float)ax*ax + (float)ay*ay + (float)az*az);
    s_accelMag = mag;

    // Store in history
    s_magHistory[s_histIdx] = mag;
    s_histIdx = (s_histIdx + 1) % HISTORY;

    // Check for shake (sudden spike above threshold above 1g)
    int32_t deviation = mag - 16384;
    if (abs(deviation) > SHAKE_THRESHOLD) {
        s_state = MotionState::SHAKEN;
        return;
    }

    // Decay shaken state after 3 seconds by re-evaluating variance
    // Compute variance of recent history
    long mean = 0;
    for (int i = 0; i < HISTORY; i++) mean += s_magHistory[i];
    mean /= HISTORY;
    long variance = 0;
    for (int i = 0; i < HISTORY; i++) {
        long d = s_magHistory[i] - mean;
        variance += d * d;
    }
    variance /= HISTORY;

    if (variance > CARRIED_VARIANCE * CARRIED_VARIANCE) {
        s_state = MotionState::CARRIED;
    } else if (variance > TRANSIT_VARIANCE * TRANSIT_VARIANCE) {
        s_state = MotionState::IN_TRANSIT;
    } else {
        s_state = MotionState::STATIONARY;
    }
}

MotionState getState() { return s_state; }
int32_t     getAccelMag() { return s_accelMag; }

} // namespace Motion
