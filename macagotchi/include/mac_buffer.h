#pragma once
#include <Arduino.h>
#include "config.h"

// 12-hour rolling circular buffer of newly-seen MAC hashes + timestamps.
// Answers: "how many new MACs have I seen in the last 12 hours?"
struct MacEntry {
    uint32_t hash;       // truncated hash of MAC (collision-tolerant)
    uint32_t timestamp;  // millis() at discovery — wraps at ~49 days
    bool     isStable;   // true = OUI-registered, false = randomised
};

class MacBuffer {
public:
    MacBuffer() : _head(0), _count(0) {}

    // Add a new entry (call only for truly new MACs)
    void add(const uint8_t mac[6], bool isStable);

    // Count entries within the novelty window (12 hours)
    uint32_t countRecent(bool stableOnly = false) const;

    // Stable vs random breakdown in last 12h
    void countBreakdown(uint32_t &stable, uint32_t &random) const;

    // Remove expired entries (called periodically — O(n) but buffer is small)
    void expire();

    uint32_t total() const { return _count; }

private:
    MacEntry _buf[MAC_BUFFER_SIZE];
    uint16_t _head;    // next write position
    uint32_t _count;   // total ever added (not just active)

    bool isRecent(uint32_t ts) const;
    uint32_t macHash(const uint8_t mac[6]) const;
};
