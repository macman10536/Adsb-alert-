#include "bloom_filter.h"
#include <math.h>

BloomFilter::BloomFilter() {}

BloomFilter::~BloomFilter() {
    if (_bits) free(_bits);
}

bool BloomFilter::begin(uint32_t capacity, float fpRate) {
    // m = -n*ln(p) / (ln2)^2
    double m = -(double)capacity * log((double)fpRate) / (log(2.0) * log(2.0));
    _bitSize  = (uint32_t)ceil(m);
    _byteSize = (_bitSize + 7) / 8;

    // k = (m/n) * ln2
    double k = ((double)_bitSize / (double)capacity) * log(2.0);
    _numHash  = (uint8_t)round(k);
    if (_numHash < 1) _numHash = 1;
    if (_numHash > 20) _numHash = 20;

#if defined(BOARD_HAS_PSRAM)
    _bits = (uint8_t*)ps_malloc(_byteSize);
#else
    _bits = (uint8_t*)malloc(_byteSize);
#endif

    if (!_bits) return false;
    memset(_bits, 0, _byteSize);
    return true;
}

void BloomFilter::reset() {
    if (_bits) memset(_bits, 0, _byteSize);
}

// FNV-1a variant seeded via XOR with seed byte
uint32_t BloomFilter::hash(const uint8_t mac[6], uint8_t seed) const {
    uint32_t h = 2166136261UL ^ seed;
    for (int i = 0; i < 6; i++) {
        h ^= mac[i];
        h *= 16777619UL;
    }
    return h % _bitSize;
}

void BloomFilter::add(const uint8_t mac[6]) {
    for (uint8_t i = 0; i < _numHash; i++) {
        uint32_t bit = hash(mac, i);
        _bits[bit / 8] |= (1 << (bit % 8));
    }
}

bool BloomFilter::contains(const uint8_t mac[6]) const {
    for (uint8_t i = 0; i < _numHash; i++) {
        uint32_t bit = hash(mac, i);
        if (!(_bits[bit / 8] & (1 << (bit % 8)))) return false;
    }
    return true;
}

bool BloomFilter::loadFrom(const uint8_t *src, size_t len) {
    if (len != _byteSize || !_bits) return false;
    memcpy(_bits, src, _byteSize);
    return true;
}
