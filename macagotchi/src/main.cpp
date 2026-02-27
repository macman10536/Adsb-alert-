/*
 * Macagotchi — BLE environment companion
 * Platform: TTGO T-Beam V1.1 (dev) / XIAO ESP32-S3 (production)
 *
 * Build with PlatformIO. Set PLATFORM_TBEAM or PLATFORM_XIAO_S3 in
 * platformio.ini build_flags.
 */

#include <Arduino.h>
#include <Wire.h>
#include "config.h"
#include "storage.h"
#include "bloom_filter.h"
#include "mac_buffer.h"
#include "ble_scanner.h"
#include "motion.h"
#include "mood.h"
#include "hunger.h"
#include "display.h"
#include "buttons.h"
#include "calibration.h"

#if HAS_AXP192
  #include <axp20x.h>
  static AXP20X_Class axp;
#endif

// ─── Version ─────────────────────────────────────────────────────────────────
static const char *FW_VERSION = "1.0.0";

// ─── Globals ─────────────────────────────────────────────────────────────────
static BloomFilter bloom;
static MacBuffer   macBuf;

// Device state machine
enum class AppState {
    MPU_CALIBRATION,   // First boot: MPU zero-point calibration
    EGG_PHASE,         // 48-hour world calibration
    NORMAL,            // Post-hatch operation
};
static AppState appState = AppState::MPU_CALIBRATION;

// Scan scheduling
static uint32_t s_lastScanMs   = 0;
static uint32_t s_scanInterval = BLE_SCAN_NORMAL_INTERVAL_S * 1000UL;

// Status cycling
static uint8_t  s_statusScreen = 0;  // index into status cycle
static uint32_t s_statusShowMs = 0;  // when status screen was shown
static const uint32_t STATUS_DURATION_MS = 4000;

// Novelty score cache
static uint8_t  s_noveltyScore = 0;
static uint32_t s_noveltyShowUntil = 0;

// MAC count today (resets at midnight conceptually — simplified: resets on boot)
static uint32_t s_macCountToday = 0;
static uint32_t s_macTotal      = 0;

// Last scan result
static ScanResult s_lastScan = {0, 0, 0};

// ─── AXP192 init (T-Beam only) ───────────────────────────────────────────────
#if HAS_AXP192
static void initAxp192() {
    if (axp.begin(Wire, AXP192_SLAVE_ADDRESS) != 0) return;
    axp.setPowerOutPut(AXP192_DCDC1, AXP202_ON);   // ESP32 core
    axp.setPowerOutPut(AXP192_LDO2,  AXP202_ON);   // LoRa (unused, init clean)
    axp.setPowerOutPut(AXP192_LDO3,  AXP202_OFF);  // GPS — off
    axp.setPowerOutPut(AXP192_DCDC2, AXP202_OFF);
    axp.setPowerOutPut(AXP192_EXTEN, AXP202_OFF);
    axp.setChgLEDMode(AXP20X_LED_LOW_LEVEL);
}
#endif

// ─── Novelty score calculation ────────────────────────────────────────────────
static uint8_t computeNoveltyScore() {
    uint32_t stable, random;
    macBuf.countBreakdown(stable, random);

    // Weight: stable full value, random partial
    float weighted = (float)stable + (float)random * 0.3f;

    // Logarithmic curve: score = 10 * log(1 + weighted) / log(1 + 40)
    // 40 new weighted MACs = ~full 10 score
    float score = 10.0f * log(1.0f + weighted) / log(1.0f + 40.0f);
    if (score > 10.0f) score = 10.0f;

    return (uint8_t)(score + 0.5f);
}

// ─── BLE scan cycle ──────────────────────────────────────────────────────────
static void doScan() {
    s_lastScanMs = millis();
    s_lastScan   = BleScanner::scan(BLE_SCAN_DURATION_S);

    // Feed hunger for each new MAC
    for (uint32_t i = 0; i < s_lastScan.newStable; i++) Hunger::feed(true);
    for (uint32_t i = 0; i < s_lastScan.newRandom; i++) Hunger::feed(false);

    s_macCountToday += s_lastScan.newStable + s_lastScan.newRandom;
    s_macTotal      += s_lastScan.newStable + s_lastScan.newRandom;

    // Periodically flush bloom to NVS
    static uint8_t scansSinceFlush = 0;
    if (++scansSinceFlush >= 10) {
        Storage::saveBloom(bloom.data(), bloom.byteSize());
        Storage::setMacTotal(s_macTotal);
        Storage::setHunger(Hunger::get());
        scansSinceFlush = 0;
    }

    // Update novelty score
    s_noveltyScore = computeNoveltyScore();
    macBuf.expire();
}

// ─── Scan interval selection ──────────────────────────────────────────────────
static uint32_t chooseScanInterval() {
    if (Motion::getState() == MotionState::STATIONARY && Hunger::get() < 30) {
        return BLE_SCAN_HUNGRY_INTERVAL_S * 1000UL;
    }
    if (Hunger::get() < 30) {
        return BLE_SCAN_HUNGRY_INTERVAL_S * 1000UL;
    }
    return BLE_SCAN_NORMAL_INTERVAL_S * 1000UL;
}

// ─── Button handler ───────────────────────────────────────────────────────────
static void handleButton(ButtonEvent ev) {
    Display::wake();

    switch (ev) {
        case ButtonEvent::BTN1_SHORT:
            // Cycle status screens: hunger -> time -> BT count -> back to face
            s_statusScreen = (s_statusScreen + 1) % 3;
            s_statusShowMs = millis();
            Display::markDirty();
            break;

        case ButtonEvent::BTN1_HOLD:
            // Novelty score for 3 seconds
            s_noveltyShowUntil = millis() + 3000;
            Display::markDirty();
            break;

        case ButtonEvent::BTN2_SHORT:
            // Pet the creature — brief happy reaction
            MoodEngine::forceTransient(Mood::HAPPY, 2000);
            s_statusScreen = 0;  // back to face
            Display::markDirty();
            break;

        case ButtonEvent::BOTH_HOLD_LONG:
            // Diagnostic screen
            {
                uint32_t calRem = (appState == AppState::EGG_PHASE)
                    ? Calibration::remainingMs() : 0;
                Display::drawDiagnostic(calRem, ESP.getFreeHeap(), s_macTotal, FW_VERSION);
                delay(5000);
                Display::markDirty();
            }
            break;

        default:
            break;
    }
}

// ─── Egg-phase update ─────────────────────────────────────────────────────────
static void updateEggPhase() {
    // Run BLE scans during egg phase to accumulate MACs
    uint32_t now = millis();
    if (now - s_lastScanMs >= s_scanInterval) {
        ScanResult r = BleScanner::scan(BLE_SCAN_DURATION_S);
        for (uint32_t i = 0; i < r.newStable + r.newRandom; i++) {
            Calibration::onMacDiscovered(i < r.newStable);
        }
        s_lastScanMs = now;
    }

    // Animate egg
    uint8_t crack   = Calibration::progressPercent();
    bool wobble     = (Motion::getState() == MotionState::CARRIED);
    bool showEyes   = (s_lastScan.newStable + s_lastScan.newRandom > 3);
    bool heartbeat  = true;
    Display::drawEgg(crack, wobble, showEyes, heartbeat);

    // Check hatch condition
    if (Calibration::isComplete()) {
        Calibration::lock();
        appState = AppState::NORMAL;
        Display::markDirty();
    }
}

// ─── Normal-phase update ──────────────────────────────────────────────────────
static void updateNormalPhase() {
    uint32_t now = millis();

    // BLE scan on schedule
    s_scanInterval = chooseScanInterval();
    if (now - s_lastScanMs >= s_scanInterval) {
        doScan();
    }

    // Motion + hunger + mood update
    Motion::update();
    Hunger::update(Motion::getState());

    uint32_t recent = macBuf.countRecent();
    MoodEngine::update(Hunger::get(), Motion::getState(),
                       s_lastScan.newStable + s_lastScan.newRandom,
                       recent);

    // Display logic
    if (!Display::isAwake()) {
        Display::checkAutoOff();
        return;
    }
    Display::checkAutoOff();

    // If novelty override
    if (now < s_noveltyShowUntil) {
        Display::drawNoveltyScore(s_noveltyScore);
        return;
    }

    // Status screen cycling
    if (s_statusScreen > 0 && (now - s_statusShowMs < STATUS_DURATION_MS)) {
        switch (s_statusScreen) {
            case 1: Display::drawHungerIndicator(Hunger::get()); break;
            case 2: Display::drawBtCount(s_macCountToday, s_macTotal); break;
        }
        return;
    }
    if (s_statusScreen > 0 && (now - s_statusShowMs >= STATUS_DURATION_MS)) {
        s_statusScreen = 0;
        Display::markDirty();
    }

    // Default: face
    if (Display::isDirty()) {
        Display::drawFace(MoodEngine::getCurrent());
    }
}

// ─── MPU calibration phase ────────────────────────────────────────────────────
static void runMpuCalibration() {
    // Show egg dropping animation first
    Display::wake();
    Display::drawEgg(0, false, false, false);
    delay(500);

    // Run calibration (blocks ~4 seconds)
    Motion::runCalibration();

    // Start calibration phase
    Calibration::begin();

    appState = AppState::EGG_PHASE;
    Display::markDirty();
}

// ─── setup() ─────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);

#if HAS_AXP192
    Wire.begin(PIN_SDA, PIN_SCL);
    initAxp192();
#else
    Wire.begin(PIN_SDA, PIN_SCL);
#endif

    Storage::begin();
    Display::begin();
    Buttons::begin();

    // Init bloom filter (~12KB)
    bloom.begin(BLOOM_CAPACITY, BLOOM_FP_RATE);

    // Try to load persisted bloom state
    {
        uint8_t *tmp = (uint8_t*)malloc(bloom.byteSize());
        if (tmp) {
            size_t loaded = Storage::loadBloom(tmp, bloom.byteSize());
            if (loaded == bloom.byteSize()) bloom.loadFrom(tmp, loaded);
            free(tmp);
        }
    }

    BleScanner::begin(&bloom, &macBuf);

    s_macTotal = Storage::getMacTotal();

    // Determine boot state
    bool hatched   = Storage::getHatched();
    bool hasMpuCal = Storage::hasMpuOffsets();

    if (!hasMpuCal) {
        // First ever boot — need MPU calibration first
        appState = AppState::MPU_CALIBRATION;
    } else if (!hatched) {
        // MPU calibrated but still in egg phase
        Motion::begin(false);  // load calibration from NVS
        Calibration::begin();
        Hunger::begin(Storage::getHunger());
        appState = AppState::EGG_PHASE;
    } else {
        // Normal operation — fully hatched
        Motion::begin(false);
        Hunger::begin(Storage::getHunger());
        MoodEngine::begin((Mood)Storage::getMood());
        appState = AppState::NORMAL;
    }

    Display::wake();
}

// ─── loop() ──────────────────────────────────────────────────────────────────
void loop() {
    ButtonEvent ev = Buttons::poll();
    if (ev != ButtonEvent::NONE) {
        if (appState == AppState::EGG_PHASE && ev == ButtonEvent::BOTH_HOLD_LONG) {
            // Show calibration countdown
            Display::drawEggCalibration(Calibration::remainingMs());
            delay(3000);
        } else {
            handleButton(ev);
        }
    }

    switch (appState) {
        case AppState::MPU_CALIBRATION:
            runMpuCalibration();
            break;

        case AppState::EGG_PHASE:
            updateEggPhase();
            break;

        case AppState::NORMAL:
            updateNormalPhase();
            break;
    }

    // Save state every 5 minutes
    static uint32_t s_lastSaveMs = 0;
    if (millis() - s_lastSaveMs > 300000UL) {
        Storage::setHunger(Hunger::get());
        Storage::setMood((uint8_t)MoodEngine::getCurrent());
        s_lastSaveMs = millis();
    }

    delay(ANIM_FRAME_MS);
}
