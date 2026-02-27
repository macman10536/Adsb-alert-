#include "hunger.h"
#include "config.h"

static uint8_t  s_hunger       = 70;
static uint32_t s_lastDecayMs  = 0;
static const uint32_t DECAY_INTERVAL_MS = 60000;  // apply decay once per minute

namespace Hunger {

void begin(uint8_t initial) {
    s_hunger      = initial;
    s_lastDecayMs = millis();
}

void feed(bool isStable) {
    int points = isStable
        ? random(HUNGER_STABLE_MAC_FEED - 2, HUNGER_STABLE_MAC_FEED + 3)
        : random(HUNGER_RAND_MAC_FEED   - 1, HUNGER_RAND_MAC_FEED   + 2);

    s_hunger = (uint8_t)min((int)s_hunger + points, (int)HUNGER_MAX);
}

void update(MotionState motion) {
    uint32_t now = millis();
    if (now - s_lastDecayMs < DECAY_INTERVAL_MS) return;
    s_lastDecayMs = now;

    uint8_t decay = (motion == MotionState::STATIONARY)
        ? HUNGER_DECAY_IDLE_PER_MIN
        : HUNGER_DECAY_ACTIVE_PER_MIN;

    if (s_hunger > decay) s_hunger -= decay;
    else                  s_hunger  = 0;
}

uint8_t get()           { return s_hunger; }
void    set(uint8_t v)  { s_hunger = min(v, (uint8_t)HUNGER_MAX); }

} // namespace Hunger
