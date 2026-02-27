#pragma once
#include <Arduino.h>

enum class ButtonEvent {
    NONE,
    BTN1_SHORT,
    BTN1_HOLD,      // 2-second hold
    BTN2_SHORT,
    BTN2_HOLD,      // 2-second hold
    BOTH_HOLD_LONG  // 10-second combined hold
};

namespace Buttons {
    void begin();
    ButtonEvent poll();   // call every loop tick, returns event if one fired
}
