#pragma once
#include <Arduino.h>
#include "motion.h"

namespace Hunger {
    void    begin(uint8_t initial = 70);
    void    feed(bool isStable);         // +hunger per new MAC
    void    update(MotionState motion);  // call ~once per minute
    uint8_t get();
    void    set(uint8_t val);
}
