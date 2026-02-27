#pragma once
#include <Arduino.h>
#include "bloom_filter.h"
#include "mac_buffer.h"

struct ScanResult {
    uint32_t newStable;    // new OUI-registered MACs this scan
    uint32_t newRandom;    // new randomised MACs this scan
    uint32_t totalSeen;    // total devices seen (including known)
};

namespace BleScanner {
    // Call once at startup — pass shared bloom filter and MAC buffer
    void begin(BloomFilter *bloom, MacBuffer *macBuf);

    // Trigger a BLE scan of durationSec seconds (blocking-style via callback)
    // Returns counts of newly discovered MACs
    ScanResult scan(uint8_t durationSec = BLE_SCAN_DURATION_S);

    // True if a scan is currently in progress
    bool isScanning();

    // Is a MAC address randomised? (checks locally-administered bit)
    bool isRandomised(const uint8_t mac[6]);
}
