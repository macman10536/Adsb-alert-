#include "mac_buffer.h"

bool MacBuffer::isRecent(uint32_t ts) const {
    uint32_t now = millis();
    uint32_t age = now - ts;  // wraps correctly with unsigned arithmetic
    return age < (uint32_t)NOVELTY_WINDOW_MS;
}

uint32_t MacBuffer::macHash(const uint8_t mac[6]) const {
    uint32_t h = 2166136261UL;
    for (int i = 0; i < 6; i++) {
        h ^= mac[i];
        h *= 16777619UL;
    }
    return h;
}

void MacBuffer::add(const uint8_t mac[6], bool isStable) {
    _buf[_head].hash      = macHash(mac);
    _buf[_head].timestamp = millis();
    _buf[_head].isStable  = isStable;
    _head = (_head + 1) % MAC_BUFFER_SIZE;
    _count++;
}

uint32_t MacBuffer::countRecent(bool stableOnly) const {
    uint32_t n = 0;
    for (uint16_t i = 0; i < MAC_BUFFER_SIZE; i++) {
        if (_buf[i].timestamp == 0) continue;
        if (stableOnly && !_buf[i].isStable) continue;
        if (isRecent(_buf[i].timestamp)) n++;
    }
    return n;
}

void MacBuffer::countBreakdown(uint32_t &stable, uint32_t &random) const {
    stable = 0; random = 0;
    for (uint16_t i = 0; i < MAC_BUFFER_SIZE; i++) {
        if (_buf[i].timestamp == 0) continue;
        if (!isRecent(_buf[i].timestamp)) continue;
        if (_buf[i].isStable) stable++;
        else                  random++;
    }
}

void MacBuffer::expire() {
    for (uint16_t i = 0; i < MAC_BUFFER_SIZE; i++) {
        if (_buf[i].timestamp != 0 && !isRecent(_buf[i].timestamp)) {
            _buf[i].timestamp = 0;
        }
    }
}
