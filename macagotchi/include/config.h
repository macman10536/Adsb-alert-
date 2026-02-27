#pragma once
#include <Arduino.h>

// ─── Platform pin assignments ──────────────────────────────────────────────

#if defined(PLATFORM_TBEAM)
  #define PIN_SDA          21
  #define PIN_SCL          22
  #define PIN_BTN1         38
  #define PIN_BTN2         39
  #define HAS_AXP192       1

#elif defined(PLATFORM_XIAO_S3)
  #define PIN_SDA           5
  #define PIN_SCL           6
  #define PIN_BTN1          1
  #define PIN_BTN2          2
  #define HAS_AXP192        0

#else
  #error "Define PLATFORM_TBEAM or PLATFORM_XIAO_S3"
#endif

// ─── I2C addresses ─────────────────────────────────────────────────────────
#define I2C_OLED_ADDR    0x3C
#define I2C_MPU_ADDR     0x68

// ─── BLE scan timing (seconds) ────────────────────────────────────────────
#define BLE_SCAN_NORMAL_INTERVAL_S   90
#define BLE_SCAN_HUNGRY_INTERVAL_S   60
#define BLE_SCAN_SLEEP_INTERVAL_S   300
#define BLE_SCAN_DURATION_S           9

// ─── Hunger ────────────────────────────────────────────────────────────────
#define HUNGER_MAX                  100
#define HUNGER_STABLE_MAC_FEED       10   // +points per new stable MAC
#define HUNGER_RAND_MAC_FEED          3   // +points per new random MAC
#define HUNGER_DECAY_IDLE_PER_MIN     2   // points/min stationary, known env
#define HUNGER_DECAY_ACTIVE_PER_MIN   1   // points/min while carried

// ─── Novelty window ────────────────────────────────────────────────────────
#define NOVELTY_WINDOW_MS   (12UL * 3600UL * 1000UL)  // 12 hours in ms
#define MAC_BUFFER_SIZE     2000

// ─── Bloom filter ──────────────────────────────────────────────────────────
#define BLOOM_CAPACITY      10000
#define BLOOM_FP_RATE       0.01f   // 1% false positive

// ─── Egg / calibration ─────────────────────────────────────────────────────
#define CALIBRATION_DURATION_MS   (48UL * 3600UL * 1000UL)  // 48 hours
#define CALIBRATION_MIN_MACS      50

// ─── Display ────────────────────────────────────────────────────────────────
#define DISPLAY_TIMEOUT_MS   30000   // 30 seconds auto-off
#define ANIM_FPS             10
#define ANIM_FRAME_MS        (1000 / ANIM_FPS)

// ─── Button hold thresholds (ms) ───────────────────────────────────────────
#define BTN_HOLD_SHORT_MS    2000
#define BTN_HOLD_DIAG_MS    10000
#define BTN_DEBOUNCE_MS        50

// ─── NVS keys ───────────────────────────────────────────────────────────────
#define NVS_NS               "macagotchi"
#define NVS_KEY_CAL_START    "cal_start"
#define NVS_KEY_HATCHED      "hatched"
#define NVS_KEY_MPU_OFF      "mpu_offsets"
#define NVS_KEY_RAND_RATIO   "rand_ratio"
#define NVS_KEY_BLOOM        "bloom_data"
#define NVS_KEY_HUNGER       "hunger"
#define NVS_KEY_MOOD         "mood"
#define NVS_KEY_MAC_TOTAL    "mac_total"
