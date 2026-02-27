#include "ble_scanner.h"
#include "config.h"
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>

static BloomFilter *s_bloom   = nullptr;
static MacBuffer   *s_macBuf  = nullptr;
static BLEScan     *s_bleScan = nullptr;
static volatile bool s_scanning = false;

// Scan result accumulators (reset each scan)
static uint32_t s_newStable = 0;
static uint32_t s_newRandom = 0;
static uint32_t s_totalSeen = 0;

// ─── Advertised device callback ──────────────────────────────────────────────

class MacCallback : public BLEAdvertisedDeviceCallbacks {
    void onResult(BLEAdvertisedDevice dev) override {
        s_totalSeen++;

        const uint8_t *mac = dev.getAddress().getNative();
        bool random = BleScanner::isRandomised(mac);

        if (s_bloom->contains(mac)) return;  // seen before

        // New device
        s_bloom->add(mac);
        s_macBuf->add(mac, !random);

        if (random) s_newRandom++;
        else        s_newStable++;
    }
};

static MacCallback s_callback;

// ─── Public API ──────────────────────────────────────────────────────────────

namespace BleScanner {

void begin(BloomFilter *bloom, MacBuffer *macBuf) {
    s_bloom  = bloom;
    s_macBuf = macBuf;

    BLEDevice::init("");
    s_bleScan = BLEDevice::getScan();
    s_bleScan->setAdvertisedDeviceCallbacks(&s_callback, false);
    s_bleScan->setActiveScan(false);  // passive — less power
    s_bleScan->setInterval(100);
    s_bleScan->setWindow(99);
}

ScanResult scan(uint8_t durationSec) {
    s_newStable = 0;
    s_newRandom = 0;
    s_totalSeen = 0;
    s_scanning  = true;

    s_bleScan->start(durationSec, false);
    s_bleScan->clearResults();

    s_scanning = false;

    return {s_newStable, s_newRandom, s_totalSeen};
}

bool isScanning() {
    return s_scanning;
}

bool isRandomised(const uint8_t mac[6]) {
    // Locally administered bit = bit 1 of first octet
    return (mac[0] & 0x02) != 0;
}

} // namespace BleScanner
