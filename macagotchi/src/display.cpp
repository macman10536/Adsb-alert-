#include "display.h"
#include "config.h"
#include <Wire.h>

// U8g2 constructor for SSD1306/SSD1315 128x64 I2C
static U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

static bool     s_awake        = false;
static uint32_t s_lastActivity = 0;
static bool     s_dirty        = true;

// ─── Internal face helpers ──────────────────────────────────────────────────

// Draw the common structural elements (body, eye sockets, brow base, bags)
static void drawFaceBase() {
    // Rounded body
    u8g2.drawRFrame(2, 2, 124, 60, 10);

    // Eye socket outlines (two large ovals close together)
    // Left eye socket: centred around x=44, y=28
    u8g2.drawEllipse(44, 28, 16, 14, U8G2_DRAW_ALL);
    // Right eye socket: centred around x=84, y=28
    u8g2.drawEllipse(84, 28, 16, 14, U8G2_DRAW_ALL);

    // Bags under each eye (drooping arc)
    // Left bag
    for (int i = -14; i <= 14; i++) {
        u8g2.drawPixel(44 + i, 43 + (i * i) / 25);
    }
    // Right bag
    for (int i = -14; i <= 14; i++) {
        u8g2.drawPixel(84 + i, 43 + (i * i) / 25);
    }

    // Nose bridge between eyes
    u8g2.drawLine(60, 28, 68, 28);
    u8g2.drawLine(61, 29, 67, 29);
}

// Draw pupils — shape varies by mood
static void drawPupils(Mood mood) {
    switch (mood) {
        case Mood::CALM:
            // Heavy-lidded: pupils dropped low
            u8g2.drawDisc(44, 33, 5);
            u8g2.drawDisc(84, 33, 5);
            break;

        case Mood::HAPPY:
            // Heart shaped pupils
            // Left heart
            u8g2.drawDisc(41, 27, 3);
            u8g2.drawDisc(47, 27, 3);
            u8g2.drawTriangle(38, 29, 50, 29, 44, 35);
            // Right heart
            u8g2.drawDisc(81, 27, 3);
            u8g2.drawDisc(87, 27, 3);
            u8g2.drawTriangle(78, 29, 90, 29, 84, 35);
            break;

        case Mood::EXCITED:
            // Pupils shot upward, slightly outward
            u8g2.drawDisc(43, 22, 5);
            u8g2.drawDisc(85, 22, 5);
            break;

        case Mood::SHOCKED:
            // Tiny pinprick dots
            u8g2.drawDisc(44, 28, 2);
            u8g2.drawDisc(84, 28, 2);
            break;

        case Mood::SLEEPING:
            // Closed eyes — curved line
            u8g2.drawLine(32, 28, 56, 28);
            u8g2.drawLine(33, 29, 55, 29);
            u8g2.drawLine(34, 30, 54, 30);
            u8g2.drawLine(72, 28, 96, 28);
            u8g2.drawLine(73, 29, 95, 29);
            u8g2.drawLine(74, 30, 94, 30);
            break;

        case Mood::ANGRY:
            // Cross-eyed, pupils pulled to inner corners
            u8g2.drawDisc(50, 28, 5);
            u8g2.drawDisc(78, 28, 5);
            break;
    }
}

// Draw unibrow spanning both eyes
static void drawBrow(Mood mood) {
    int lx1 = 28, lx2 = 60;   // brow x range left side
    int rx1 = 68, rx2 = 100;   // brow x range right side

    switch (mood) {
        case Mood::CALM:
        case Mood::SLEEPING:
            // Flat brow
            u8g2.drawLine(lx1, 11, lx2, 11);
            u8g2.drawLine(rx1, 11, rx2, 11);
            break;

        case Mood::HAPPY:
            // Gently raised arches
            for (int x = lx1; x <= lx2; x++) {
                int y = 10 - (int)(4.0f * sin(M_PI * (x - lx1) / (lx2 - lx1)));
                u8g2.drawPixel(x, y);
            }
            for (int x = rx1; x <= rx2; x++) {
                int y = 10 - (int)(4.0f * sin(M_PI * (x - rx1) / (rx2 - rx1)));
                u8g2.drawPixel(x, y);
            }
            break;

        case Mood::EXCITED:
            // Raised arches, more pronounced
            for (int x = lx1; x <= lx2; x++) {
                int y = 9 - (int)(5.0f * sin(M_PI * (x - lx1) / (lx2 - lx1)));
                u8g2.drawPixel(x, y);
            }
            for (int x = rx1; x <= rx2; x++) {
                int y = 9 - (int)(5.0f * sin(M_PI * (x - rx1) / (rx2 - rx1)));
                u8g2.drawPixel(x, y);
            }
            break;

        case Mood::SHOCKED:
        case Mood::ANGRY:
            // Sharp angry V — inner ends lower
            u8g2.drawLine(lx1, 8,  lx2, 14);  // left side: outer high, inner low
            u8g2.drawLine(lx1, 9,  lx2, 15);
            u8g2.drawLine(rx1, 14, rx2, 8);   // right side: inner low, outer high
            u8g2.drawLine(rx1, 15, rx2, 9);
            break;
    }
}

static void drawMouth(Mood mood) {
    int cx = 64, my = 52;  // mouth centre x, y baseline
    switch (mood) {
        case Mood::CALM:
        case Mood::SLEEPING:
            // Flat pill
            u8g2.drawRBox(cx - 10, my - 2, 20, 5, 2);
            break;

        case Mood::HAPPY:
            // Gentle smile
            for (int x = -10; x <= 10; x++) {
                int y = my + (x * x) / 25;
                u8g2.drawPixel(cx + x, y);
                u8g2.drawPixel(cx + x, y + 1);
            }
            break;

        case Mood::EXCITED:
            // Big grin with teeth line
            for (int x = -14; x <= 14; x++) {
                int y = my + (x * x) / 30;
                u8g2.drawPixel(cx + x, y);
                u8g2.drawPixel(cx + x, y + 1);
            }
            // Teeth divider
            u8g2.drawLine(cx - 10, my + 1, cx + 10, my + 1);
            break;

        case Mood::SHOCKED:
            // Wide open oval
            u8g2.drawEllipse(cx, my, 10, 6, U8G2_DRAW_ALL);
            break;

        case Mood::ANGRY:
            // Downturned frown
            for (int x = -10; x <= 10; x++) {
                int y = my - (x * x) / 25;
                u8g2.drawPixel(cx + x, y);
                u8g2.drawPixel(cx + x, y - 1);
            }
            break;
    }
}

// Mood extras (sparkles, ZZZs, stress lines, etc.)
static void drawExtras(Mood mood, uint32_t tick) {
    switch (mood) {
        case Mood::SLEEPING: {
            // Animated ZZZs floating up
            uint8_t phase = (tick / 500) % 3;
            u8g2.setFont(u8g2_font_5x7_tf);
            u8g2.drawStr(100,  20 - phase * 4, "z");
            u8g2.drawStr(108,  14 - phase * 4, "Z");
            u8g2.drawStr(116,   8 - phase * 4, "Z");
            break;
        }
        case Mood::SHOCKED:
            // Stress lines above head
            u8g2.drawLine(55, 2, 50, 0);
            u8g2.drawLine(64, 2, 64, 0);
            u8g2.drawLine(73, 2, 78, 0);
            break;
        case Mood::ANGRY:
            // Shake lines on body sides
            u8g2.drawLine(2,  20, 6,  24);
            u8g2.drawLine(2,  30, 6,  34);
            u8g2.drawLine(122, 20, 118, 24);
            u8g2.drawLine(122, 30, 118, 34);
            break;
        case Mood::HAPPY: {
            // Small BT symbols floating
            u8g2.setFont(u8g2_font_5x7_tf);
            uint8_t bx = 10 + ((tick / 800) % 4) * 2;
            u8g2.drawStr(bx, 30, "B");
            u8g2.drawStr(110 - bx, 35, "B");
            break;
        }
        case Mood::EXCITED: {
            // Sparkle dots + cheek marks
            uint8_t sp = (tick / 200) % 4;
            if (sp == 0 || sp == 2) {
                u8g2.drawPixel(20, 20); u8g2.drawPixel(22, 18); u8g2.drawPixel(18, 18);
                u8g2.drawPixel(108, 20); u8g2.drawPixel(110, 18); u8g2.drawPixel(106, 18);
            }
            // Cheek marks
            u8g2.drawLine(28, 38, 34, 36);
            u8g2.drawLine(30, 42, 36, 40);
            u8g2.drawLine(94, 38, 100, 36);
            u8g2.drawLine(96, 42, 102, 40);
            break;
        }
        default:
            break;
    }
}

// ─── Public API ──────────────────────────────────────────────────────────────

namespace Display {

void begin() {
    u8g2.begin();
    u8g2.setContrast(128);
    s_awake = true;
    s_lastActivity = millis();
}

void wake() {
    u8g2.setPowerSave(0);
    s_awake = true;
    s_lastActivity = millis();
    s_dirty = true;
}

void sleep() {
    u8g2.setPowerSave(1);
    s_awake = false;
}

bool isAwake() { return s_awake; }

void checkAutoOff() {
    if (s_awake && (millis() - s_lastActivity > DISPLAY_TIMEOUT_MS)) {
        sleep();
    }
}

void markDirty() {
    s_lastActivity = millis();
    s_dirty = true;
}

bool isDirty() { return s_dirty; }

void drawFace(Mood mood) {
    static uint32_t s_tick = 0;
    s_tick = millis();
    s_dirty = false;

    u8g2.clearBuffer();
    drawFaceBase();
    drawBrow(mood);
    drawPupils(mood);
    drawMouth(mood);
    drawExtras(mood, s_tick);
    u8g2.sendBuffer();
}

void drawEgg(uint8_t crackPercent, bool wobble, bool showEyes, bool heartbeat) {
    u8g2.clearBuffer();

    // Egg body — centred at (64, 35)
    int cx = 64, cy = 35;
    int wobbleX = wobble ? (int)(sin(millis() / 150.0f) * 3) : 0;
    int wobbleY = wobble ? (int)(cos(millis() / 200.0f) * 1) : 0;

    // Egg outline (tall ellipse)
    u8g2.drawEllipse(cx + wobbleX, cy + wobbleY, 20, 26, U8G2_DRAW_ALL);

    // Progressive crack lines
    if (crackPercent >= 25) {
        u8g2.drawLine(cx - 5, cy - 10, cx,     cy - 5);
        u8g2.drawLine(cx,     cy - 5,  cx + 3, cy - 12);
    }
    if (crackPercent >= 50) {
        u8g2.drawLine(cx + 8, cy - 5,  cx + 12, cy);
        u8g2.drawLine(cx + 12, cy,     cx + 7,  cy + 6);
    }
    if (crackPercent >= 75) {
        u8g2.drawLine(cx - 12, cy + 2,  cx - 8,  cy + 8);
        u8g2.drawLine(cx - 8,  cy + 8,  cx - 14, cy + 14);
    }

    // Heartbeat pulse (outer ring flicker)
    if (heartbeat) {
        uint32_t phase = millis() % 1200;
        if (phase < 200 || (phase > 400 && phase < 500)) {
            u8g2.drawEllipse(cx + wobbleX, cy + wobbleY, 23, 29, U8G2_DRAW_ALL);
        }
    }

    // Eyes peeking through (question marks or blinking eyes)
    if (showEyes) {
        u8g2.setFont(u8g2_font_5x7_tf);
        u8g2.drawStr(cx - 6, cy + 4, "?");
    }

    // Status text at bottom
    u8g2.setFont(u8g2_font_5x7_tf);
    u8g2.drawStr(30, 60, "keep still");

    u8g2.sendBuffer();
    s_dirty = false;
}

void drawEggCalibration(uint32_t remainingMs) {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x13_tf);
    u8g2.drawStr(10, 20, "Calibrating...");

    uint32_t hours   = remainingMs / 3600000;
    uint32_t minutes = (remainingMs % 3600000) / 60000;
    char buf[16];
    snprintf(buf, sizeof(buf), "%02uh %02um", (unsigned)hours, (unsigned)minutes);
    u8g2.setFont(u8g2_font_10x20_tf);
    u8g2.drawStr(20, 45, buf);
    u8g2.sendBuffer();
    s_dirty = false;
}

void drawHungerIndicator(uint8_t hunger) {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x13_tf);

    const char *label;
    if (hunger > 75)      label = "FULL";
    else if (hunger > 50) label = "Content";
    else if (hunger > 30) label = "Hungry";
    else if (hunger > 10) label = "STARVING";
    else                  label = "CRITICAL";

    u8g2.drawStr(10, 20, "Hunger:");
    u8g2.drawStr(10, 35, label);

    // Bar
    u8g2.drawFrame(10, 45, 108, 10);
    u8g2.drawBox(10, 45, (uint8_t)(108 * hunger / 100), 10);
    u8g2.sendBuffer();
    s_dirty = false;
}

void drawBtCount(uint32_t today, uint32_t lifetime) {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x13_tf);
    u8g2.drawStr(5, 15, "BLE devices");
    char buf[24];
    snprintf(buf, sizeof(buf), "Today: %lu", (unsigned long)today);
    u8g2.drawStr(5, 30, buf);
    snprintf(buf, sizeof(buf), "Total: %lu", (unsigned long)lifetime);
    u8g2.drawStr(5, 45, buf);
    u8g2.sendBuffer();
    s_dirty = false;
}

void drawNoveltyScore(uint8_t score) {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x13_tf);
    u8g2.drawStr(25, 15, "Novelty");

    // Large number
    u8g2.setFont(u8g2_font_logisoso38_tf);
    char buf[4];
    snprintf(buf, sizeof(buf), "%u", score);
    u8g2.drawStr(score < 10 ? 48 : 34, 55, buf);

    // Bar indicator
    u8g2.setFont(u8g2_font_5x7_tf);
    u8g2.drawFrame(10, 56, 108, 8);
    u8g2.drawBox(10, 56, (uint8_t)(108 * score / 10), 8);
    u8g2.sendBuffer();
    s_dirty = false;
}

void drawDiagnostic(uint32_t calRemMs, uint32_t freeRam, uint32_t macTotal,
                    const char *version) {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_5x7_tf);
    char buf[32];
    snprintf(buf, sizeof(buf), "FW: %s", version);
    u8g2.drawStr(2, 8, buf);

    uint32_t calH = calRemMs / 3600000;
    uint32_t calM = (calRemMs % 3600000) / 60000;
    snprintf(buf, sizeof(buf), "Cal: %uh%um", (unsigned)calH, (unsigned)calM);
    u8g2.drawStr(2, 18, buf);

    snprintf(buf, sizeof(buf), "RAM: %lu B", (unsigned long)freeRam);
    u8g2.drawStr(2, 28, buf);

    snprintf(buf, sizeof(buf), "MACs: %lu", (unsigned long)macTotal);
    u8g2.drawStr(2, 38, buf);

    u8g2.sendBuffer();
    s_dirty = false;
}

} // namespace Display
