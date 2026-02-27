#pragma once
#include <Arduino.h>

// Simple counting-free Bloom filter (~12KB for 10k capacity at 1% FP rate)
// Uses 7 hash functions over a 96KB bit array.
class BloomFilter {
public:
    // Allocate. Call begin() before use.
    BloomFilter();
    ~BloomFilter();

    bool begin(uint32_t capacity = 10000, float fpRate = 0.01f);

    // Add a 6-byte MAC address
    void add(const uint8_t mac[6]);

    // Query — may return true for unseen MACs (~1% rate), never false for seen
    bool contains(const uint8_t mac[6]) const;

    // Serialise/deserialise for NVS persistence
    const uint8_t* data() const { return _bits; }
    size_t         byteSize() const { return _byteSize; }
    bool           loadFrom(const uint8_t *src, size_t len);

    void reset();

private:
    uint8_t  *_bits     = nullptr;
    uint32_t  _byteSize = 0;
    uint32_t  _bitSize  = 0;
    uint8_t   _numHash  = 0;

    uint32_t hash(const uint8_t mac[6], uint8_t seed) const;
};
