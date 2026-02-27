#include "buttons.h"
#include "config.h"

struct BtnState {
    bool     pressed      = false;
    bool     prevRaw      = false;
    uint32_t pressedAt    = 0;
    bool     holdFired    = false;
};

static BtnState s_btn1, s_btn2;
static uint32_t s_bothPressedAt  = 0;
static bool     s_bothHoldFired  = false;

namespace Buttons {

void begin() {
    pinMode(PIN_BTN1, INPUT_PULLUP);
    pinMode(PIN_BTN2, INPUT_PULLUP);
}

ButtonEvent poll() {
    uint32_t now = millis();

    // Read raw (active-low with pull-up)
    bool raw1 = (digitalRead(PIN_BTN1) == LOW);
    bool raw2 = (digitalRead(PIN_BTN2) == LOW);

    // Debounce via simple edge tracking
    // Btn1 edge
    if (raw1 != s_btn1.prevRaw) {
        if (raw1) {
            s_btn1.pressedAt = now;
            s_btn1.holdFired = false;
            s_btn1.pressed   = false;
        }
        s_btn1.prevRaw = raw1;
    }
    // Btn2 edge
    if (raw2 != s_btn2.prevRaw) {
        if (raw2) {
            s_btn2.pressedAt = now;
            s_btn2.holdFired = false;
            s_btn2.pressed   = false;
        }
        s_btn2.prevRaw = raw2;
    }

    // Both-button combined hold
    if (raw1 && raw2) {
        if (s_bothPressedAt == 0) {
            s_bothPressedAt = now;
            s_bothHoldFired = false;
        } else if (!s_bothHoldFired && (now - s_bothPressedAt >= BTN_HOLD_DIAG_MS)) {
            s_bothHoldFired = true;
            return ButtonEvent::BOTH_HOLD_LONG;
        }
        return ButtonEvent::NONE;
    } else {
        s_bothPressedAt = 0;
    }

    // Btn1 short release
    if (!raw1 && s_btn1.prevRaw == false && s_btn1.pressed == false) {
        // This path won't trigger — use release detection
    }

    // Btn1 — detect short press on release, hold on duration
    if (!raw1 && s_btn1.pressedAt > 0 && !s_btn1.pressed) {
        uint32_t held = now - s_btn1.pressedAt;
        s_btn1.pressed = true;
        s_btn1.pressedAt = 0;
        if (held >= BTN_HOLD_SHORT_MS) {
            return ButtonEvent::BTN1_HOLD;
        } else if (held >= BTN_DEBOUNCE_MS) {
            return ButtonEvent::BTN1_SHORT;
        }
    }
    if (raw1 && !s_btn1.holdFired &&
        s_btn1.pressedAt > 0 &&
        (now - s_btn1.pressedAt >= BTN_HOLD_SHORT_MS)) {
        s_btn1.holdFired = true;
        // We fire hold on duration reached while still pressed
        return ButtonEvent::BTN1_HOLD;
    }

    // Btn2 — mirror logic
    if (!raw2 && s_btn2.pressedAt > 0 && !s_btn2.pressed) {
        uint32_t held = now - s_btn2.pressedAt;
        s_btn2.pressed = true;
        s_btn2.pressedAt = 0;
        if (held >= BTN_HOLD_SHORT_MS) {
            return ButtonEvent::BTN2_HOLD;
        } else if (held >= BTN_DEBOUNCE_MS) {
            return ButtonEvent::BTN2_SHORT;
        }
    }
    if (raw2 && !s_btn2.holdFired &&
        s_btn2.pressedAt > 0 &&
        (now - s_btn2.pressedAt >= BTN_HOLD_SHORT_MS)) {
        s_btn2.holdFired = true;
        return ButtonEvent::BTN2_HOLD;
    }

    // Reset pressed flag when released
    if (!raw1) { s_btn1.pressed = false; if (s_btn1.pressedAt == 0) {} }
    if (!raw2) { s_btn2.pressed = false; }

    return ButtonEvent::NONE;
}

} // namespace Buttons
