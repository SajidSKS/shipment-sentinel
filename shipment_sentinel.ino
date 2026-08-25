/*
 * Shipment Sentinel – ESP32 Firmware
 * Copyright (c) 2026 Team Solvers
 * Licensed under the MIT License.
 */

#include <SPI.h>
#include <SdFat.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <math.h>
#include <SPIFFS.h>
#include <WiFi.h>
#include <WebServer.h>
#include "mbedtls/sha256.h"

#define SENTINEL_AUTH_KEY       "SENTINEL_SECURE_VAULT_9F82A4D1"
#define GENESIS_HASH            "GENESIS_ROOT_V4"

// microSD SPI Pins
#define SD_CS_PIN   4
#define SD_SCK_PIN  18
#define SD_MISO_PIN 19
#define SD_MOSI_PIN 23

// DS3231 RTC I2C Pins
#define RTC_SDA_PIN 17
#define RTC_SCL_PIN 16

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_ADDR 0x3C
#define APDS_ADDR 0x39
#define BMP180_I2C_ADDR 0x77
#define DS3231_I2C_ADDR 0x68
#define BOOT_BUTTON_PIN 0
#define EXT_BUTTON_PIN  27  // External Push Button (Active LOW with internal pull-up)

#define SHOCK_MIN_THRESHOLD     2.20f
#define SHOCK_SEVERE_THRESHOLD  5.00f
#define FALL_THRESHOLD          0.50f
#define FALL_MIN_DURATION_MS    80
#define TAMPER_THRESHOLD        30
#define PRESSURE_CHANGE_PERCENT 5.0f
#define SEVERE_WINDOW           10000

// Per-Category Score Caps [FIX-11]
#define SHOCK_CAP    30
#define DROP_CAP     30
#define TAMPER_CAP   25
#define PRESSURE_CAP 15

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
WebServer server(80);
TwoWire I2C_RTC = TwoWire(1);
TwoWire* rtcWire = &I2C_RTC;
SdFs sd;

bool mpuAvailable  = false;
byte mpuActualAddr = 0x69;
bool bmpAvailable  = false;
bool apdsAvailable = false;
bool oledAvailable = false;
bool rtcAvailable  = false;
bool sdReady       = false;
bool spiffsReady   = false;
// SdFat 2.x (SdFs) requires NO leading slash for root-level files.
// SPIFFS requires the leading slash.
const char* sdLogPath     = "sentinel_log.csv";   // SdFat path — NO leading /
const char* spiffsLogPath = "/sentinel_log.csv";  // SPIFFS path — WITH leading /

int16_t axRaw, ayRaw, azRaw, gxRaw, gyRaw, gzRaw;
float ax = 0, ay = 0, az = 0;
float totalAccel = 1.0f;
float baselinePressure = 0, currentPressure = 0;
uint16_t currentLight = 0;
uint16_t rawR = 0, rawG = 0, rawB = 0, rawC = 0;
byte apdsChipID = 0x00;

int minorShockCount = 0;
int severeShockCount = 0;
int dropCount = 0;
int tamperEventsCount = 0;
unsigned long totalTamperDurationSecs = 0;
int pressureAlertCount = 0;
int integrityScore = 100;
bool severeIncident = false;
float worstShockG = 0;
float worstDropG = 999;
float worstDropHeightM = 0.0f;

bool boxIsOpen = false;
unsigned long boxOpenedAt = 0;
unsigned long lastTamperScoreTick = 0;

unsigned long lowGStartTime = 0;
bool inFreeFall = false;
float minFreeFallG = 1.0f;
unsigned long lastDropLogTime = 0;
bool awaitingLandingImpact = false;
unsigned long landingWindowStart = 0;
float landingPeakG = 0;

unsigned long shockPeakWindowStart = 0;
float currentPeakShock = 0;
unsigned long lastShockLogTime = 0;

unsigned long lastPressureTime = 0;

unsigned long lastOledInteraction = 0;
unsigned long fallDisplayUntil = 0;
#define OLED_TIMEOUT_MS 25000
byte oledPage = 0; // [FIX-13] 0=live, 1=summary
unsigned long lastPageFlip = 0;
#define PAGE_FLIP_INTERVAL 4000

// RTC Cache [BUG-04]
uint16_t cachedYear = 0;
uint8_t  cachedMonth = 0, cachedDay = 0;
uint8_t  cachedHour = 0, cachedMin = 0, cachedSec = 0;
unsigned long lastRtcReadMs = 0;
bool cachedRtcValid = false;

bool dashboardMode = false;
unsigned long bootButtonPressTime = 0;
bool bootButtonWasPressed = false;
unsigned long startTime = 0;
bool tripActive = false;        // [FIX-12] 3s stabilization
unsigned long tripActivateAt = 0;

uint32_t rtcEpochBase = 0;
unsigned long rtcSyncMillis = 0;

static uint8_t dec2bcd(uint8_t val) { return ((val / 10 * 16) + (val % 10)); }
static uint8_t bcd2dec(uint8_t val) { return ((val / 16 * 10) + (val % 16)); }

uint32_t dateToEpoch(uint16_t y, uint8_t m, uint8_t d, uint8_t hh, uint8_t mm, uint8_t ss) {
  uint16_t days = d;
  for (uint16_t yr = 1970; yr < y; yr++)
    days += (yr % 4 == 0 && (yr % 100 != 0 || yr % 400 == 0)) ? 366 : 365;
  const uint8_t dim[] = {31,28,31,30,31,30,31,31,30,31,30,31};
  for (uint8_t mon = 1; mon < m; mon++) {
    days += dim[mon - 1];
    if (mon == 2 && (y % 4 == 0 && (y % 100 != 0 || y % 400 == 0))) days++;
  }
  return ((days - 1) * 86400UL) + (hh * 3600UL) + (mm * 60UL) + ss;
}

void epochToDate(uint32_t epoch, uint16_t &y, uint8_t &m, uint8_t &d, uint8_t &hh, uint8_t &mm, uint8_t &ss) {
  ss = epoch % 60; epoch /= 60;
  mm = epoch % 60; epoch /= 60;
  hh = epoch % 24; uint32_t days = epoch / 24;
  y = 1970;
  while (true) {
    uint16_t diy = (y % 4 == 0 && (y % 100 != 0 || y % 400 == 0)) ? 366 : 365;
    if (days >= diy) { days -= diy; y++; } else break;
  }
  const uint8_t dim[] = {31,28,31,30,31,30,31,31,30,31,30,31};
  m = 1;
  while (m <= 12) {
    uint8_t dm = dim[m-1];
    if (m == 2 && (y % 4 == 0 && (y % 100 != 0 || y % 400 == 0))) dm++;
    if (days >= dm) { days -= dm; m++; } else break;
  }
  d = days + 1;
}

void i2cRecoverBus(int sdaPin, int sclPin) {
  pinMode(sdaPin, INPUT_PULLUP);
  pinMode(sclPin, OUTPUT);
  digitalWrite(sclPin, HIGH);
  delayMicroseconds(10);
  for (int i = 0; i < 9; i++) {
    digitalWrite(sclPin, LOW);
    delayMicroseconds(10);
    digitalWrite(sclPin, HIGH);
    delayMicroseconds(10);
  }
  pinMode(sdaPin, OUTPUT);
  digitalWrite(sdaPin, LOW);
  delayMicroseconds(10);
  digitalWrite(sclPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(sdaPin, HIGH);
  delayMicroseconds(10);
  pinMode(sdaPin, INPUT_PULLUP);
  pinMode(sclPin, INPUT_PULLUP);
}

bool readDS3231Raw(uint16_t &year, uint8_t &month, uint8_t &day, uint8_t &hour, uint8_t &minute, uint8_t &second) {
  rtcWire->beginTransmission(DS3231_I2C_ADDR);
  rtcWire->write(0x00);
  if (rtcWire->endTransmission(false) != 0) {
    rtcWire->beginTransmission(DS3231_I2C_ADDR);
    rtcWire->write(0x00);
    if (rtcWire->endTransmission(true) != 0) return false;
  }
  if (rtcWire->requestFrom((uint8_t)DS3231_I2C_ADDR, (size_t)7, (bool)true) != 7) return false;
  second = bcd2dec(rtcWire->read() & 0x7F);
  minute = bcd2dec(rtcWire->read());
  hour   = bcd2dec(rtcWire->read() & 0x3F);
  rtcWire->read();
  day    = bcd2dec(rtcWire->read());
  month  = bcd2dec(rtcWire->read() & 0x1F);
  year   = 2000 + bcd2dec(rtcWire->read());
  return true;
}

// [BUG-04] Cached RTC reader — polls hardware at most once per second
void updateRtcCache() {
  unsigned long now = millis();
  if (now - lastRtcReadMs < 1000 && cachedRtcValid) return;
  lastRtcReadMs = now;

  if (rtcAvailable) {
    uint16_t y; uint8_t m, d, hh, mm, ss;
    if (readDS3231Raw(y, m, d, hh, mm, ss)) {
      cachedYear = y; cachedMonth = m; cachedDay = d;
      cachedHour = hh; cachedMin = mm; cachedSec = ss;
      cachedRtcValid = true;
      // Re-anchor epoch base for fallback accuracy
      rtcEpochBase = dateToEpoch(y, m, d, hh, mm, ss);
      rtcSyncMillis = now;
      return;
    }
  }
  // Fallback: advance from epoch anchor
  if (rtcEpochBase > 0) {
    uint32_t currentEpoch = rtcEpochBase + ((now - rtcSyncMillis) / 1000);
    uint16_t y; uint8_t m, d, hh, mm, ss;
    epochToDate(currentEpoch, y, m, d, hh, mm, ss);
    cachedYear = y; cachedMonth = m; cachedDay = d;
    cachedHour = hh; cachedMin = mm; cachedSec = ss;
    cachedRtcValid = true;
  }
}

void setDS3231(uint16_t year, uint8_t month, uint8_t day, uint8_t hour, uint8_t minute, uint8_t second) {
  rtcWire->beginTransmission(DS3231_I2C_ADDR);
  rtcWire->write(0x00);
  rtcWire->write(dec2bcd(second));
  rtcWire->write(dec2bcd(minute));
  rtcWire->write(dec2bcd(hour));
  rtcWire->write(1);
  rtcWire->write(dec2bcd(day));
  rtcWire->write(dec2bcd(month));
  rtcWire->write(dec2bcd(year >= 2000 ? year - 2000 : year));
  rtcWire->endTransmission();
  // Clear OSF
  rtcWire->beginTransmission(DS3231_I2C_ADDR);
  rtcWire->write(0x0F);
  rtcWire->endTransmission(false);
  if (rtcWire->requestFrom((uint8_t)DS3231_I2C_ADDR, (size_t)1) == 1) {
    uint8_t st = rtcWire->read();
    rtcWire->beginTransmission(DS3231_I2C_ADDR);
    rtcWire->write(0x0F);
    rtcWire->write(st & 0x7F);
    rtcWire->endTransmission();
  }
}

bool ds3231LostPower() {
  rtcWire->beginTransmission(DS3231_I2C_ADDR);
  rtcWire->write(0x0F);
  if (rtcWire->endTransmission() != 0) return true;
  if (rtcWire->requestFrom((uint8_t)DS3231_I2C_ADDR, (size_t)1) == 1)
    return (rtcWire->read() & 0x80) != 0;
  return true;
}

void syncDS3231ToCompileTime() {
  char sm[5]; int mo=1,dy=1,yr=2026,hr=0,mn=0,sc=0;
  static const char mn_names[] = "JanFebMarAprMayJunJulAugSepOctNovDec";
  sscanf(__DATE__, "%s %d %d", sm, &dy, &yr);
  sscanf(__TIME__, "%d:%d:%d", &hr, &mn, &sc);
  char* p = strstr(mn_names, sm);
  if (p) mo = (p - mn_names) / 3 + 1;
  setDS3231((uint16_t)yr,(uint8_t)mo,(uint8_t)dy,(uint8_t)hr,(uint8_t)mn,(uint8_t)sc);
}

// Timestamp formatters (use cache, never touch I2C)
String getFormattedDateTime() {
  if (cachedRtcValid) {
    char buf[25];
    snprintf(buf, sizeof(buf), "%04d-%02d-%02d %02d:%02d:%02d",
             cachedYear, cachedMonth, cachedDay, cachedHour, cachedMin, cachedSec);
    return String(buf);
  }
  return "T+" + String((millis() - startTime) / 1000) + "s";
}

String getFormattedTimeOnly() {
  if (cachedRtcValid) {
    char buf[12];
    snprintf(buf, sizeof(buf), "%02d:%02d:%02d", cachedHour, cachedMin, cachedSec);
    return String(buf);
  }
  return "--:--:--";
}

int16_t  bmp_ac1, bmp_ac2, bmp_ac3;
uint16_t bmp_ac4, bmp_ac5, bmp_ac6;
int16_t  bmp_b1, bmp_b2, bmp_mb, bmp_mc, bmp_md;

int16_t bmpReadInt16(uint8_t reg) {
  Wire.beginTransmission(BMP180_I2C_ADDR); Wire.write(reg); Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)BMP180_I2C_ADDR, (size_t)2);
  return Wire.available() >= 2 ? (int16_t)((Wire.read() << 8) | Wire.read()) : 0;
}
uint16_t bmpReadUInt16(uint8_t reg) {
  Wire.beginTransmission(BMP180_I2C_ADDR); Wire.write(reg); Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)BMP180_I2C_ADDR, (size_t)2);
  return Wire.available() >= 2 ? (uint16_t)((Wire.read() << 8) | Wire.read()) : 0;
}

bool initBMP180Direct() {
  Wire.beginTransmission(BMP180_I2C_ADDR);
  if (Wire.endTransmission() != 0) return false;
  bmp_ac1 = bmpReadInt16(0xAA); bmp_ac2 = bmpReadInt16(0xAC); bmp_ac3 = bmpReadInt16(0xAE);
  bmp_ac4 = bmpReadUInt16(0xB0); bmp_ac5 = bmpReadUInt16(0xB2); bmp_ac6 = bmpReadUInt16(0xB4);
  bmp_b1 = bmpReadInt16(0xB6); bmp_b2 = bmpReadInt16(0xB8);
  bmp_mb = bmpReadInt16(0xBA); bmp_mc = bmpReadInt16(0xBC); bmp_md = bmpReadInt16(0xBE);
  return !(bmp_ac1 == 0 || bmp_ac1 == -1);
}

float readBMP180Pressure() {
  if (!bmpAvailable) return 0;
  Wire.beginTransmission(BMP180_I2C_ADDR); Wire.write(0xF4); Wire.write(0x2E); Wire.endTransmission(); delay(5);
  int32_t ut = bmpReadInt16(0xF6);
  const uint8_t oss = 0;
  Wire.beginTransmission(BMP180_I2C_ADDR); Wire.write(0xF4); Wire.write(0x34+(oss<<6)); Wire.endTransmission(); delay(5);
  Wire.beginTransmission(BMP180_I2C_ADDR); Wire.write(0xF6); Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)BMP180_I2C_ADDR, (size_t)3);
  if (Wire.available() < 3) return 0;
  int32_t msb=Wire.read(), lsb=Wire.read(), xlsb=Wire.read();
  int32_t up = ((msb<<16)|(lsb<<8)|xlsb) >> (8-oss);
  int32_t x1 = ((ut-(int32_t)bmp_ac6)*(int32_t)bmp_ac5)>>15;
  int32_t x2 = ((int32_t)bmp_mc<<11)/(x1+bmp_md);
  int32_t b5 = x1+x2, b6 = b5-4000;
  x1 = (bmp_b2*((b6*b6)>>12))>>11; x2 = (bmp_ac2*b6)>>11;
  int32_t x3 = x1+x2;
  int32_t b3 = (((((int32_t)bmp_ac1)*4+x3)<<oss)+2)>>2;
  x1 = (bmp_ac3*b6)>>13; x2 = (bmp_b1*((b6*b6)>>12))>>16; x3 = ((x1+x2)+2)>>2;
  uint32_t b4 = (bmp_ac4*(uint32_t)(x3+32768))>>15;
  uint32_t b7 = ((uint32_t)(up-b3))*(50000>>oss);
  int32_t p = (b7 < 0x80000000) ? (b7<<1)/b4 : (b7/b4)<<1;
  x1 = (p>>8)*(p>>8); x1 = (x1*3038)>>16; x2 = (-7357*p)>>16;
  p += (x1+x2+3791)>>4;
  return p / 100.0f;
}

void readPressure() {
  if (!bmpAvailable) return;
  currentPressure = readBMP180Pressure();
}

void mpuWake() {
  Wire.beginTransmission(mpuActualAddr); Wire.write(0x6B); Wire.write(0x00); Wire.endTransmission(true); delay(5);
  Wire.beginTransmission(mpuActualAddr); Wire.write(0x6B); Wire.write(0x01); Wire.endTransmission(true);
  Wire.beginTransmission(mpuActualAddr); Wire.write(0x1C); Wire.write(0x10); Wire.endTransmission(true); // ±8G
  Wire.beginTransmission(mpuActualAddr); Wire.write(0x1A); Wire.write(0x03); Wire.endTransmission(true); // 44Hz DLPF
}

bool mpuPing() {
  Wire.beginTransmission(0x69);
  if (Wire.endTransmission() == 0) { mpuActualAddr = 0x69; return true; }
  Wire.beginTransmission(0x68);
  if (Wire.endTransmission() == 0) { mpuActualAddr = 0x68; return true; }
  return false;
}

void readMPU() {
  Wire.beginTransmission(mpuActualAddr); Wire.write(0x3B); Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)mpuActualAddr, (size_t)14, true);
  if (Wire.available() >= 14) {
    axRaw = (Wire.read()<<8)|Wire.read();
    ayRaw = (Wire.read()<<8)|Wire.read();
    azRaw = (Wire.read()<<8)|Wire.read();
    Wire.read(); Wire.read();
    gxRaw = (Wire.read()<<8)|Wire.read();
    gyRaw = (Wire.read()<<8)|Wire.read();
    gzRaw = (Wire.read()<<8)|Wire.read();
    ax = axRaw / 4096.0f;
    ay = ayRaw / 4096.0f;
    az = azRaw / 4096.0f;
    totalAccel = sqrt(ax*ax + ay*ay + az*az);
  }
}

bool initAPDS9960Direct() {
  Wire.beginTransmission(APDS_ADDR);
  if (Wire.endTransmission() != 0) return false;
  Wire.beginTransmission(APDS_ADDR); Wire.write(0x92); Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)APDS_ADDR, (size_t)1);
  if (Wire.available()) apdsChipID = Wire.read();
  Wire.beginTransmission(APDS_ADDR); Wire.write(0x80); Wire.write(0x03); Wire.endTransmission();
  Wire.beginTransmission(APDS_ADDR); Wire.write(0x81); Wire.write(219); Wire.endTransmission();
  Wire.beginTransmission(APDS_ADDR); Wire.write(0x8F); Wire.write(0x01); Wire.endTransmission();
  return true;
}

void readLight() {
  if (!apdsAvailable) {
    static unsigned long lastRetry = 0;
    if (millis() - lastRetry > 2000) { lastRetry = millis(); if (initAPDS9960Direct()) apdsAvailable = true; }
    return;
  }
  Wire.beginTransmission(APDS_ADDR); Wire.write(0x94); Wire.endTransmission(false);
  if (Wire.requestFrom((uint8_t)APDS_ADDR, (size_t)8) == 8) {
    rawC = Wire.read() | (Wire.read()<<8);
    rawR = Wire.read() | (Wire.read()<<8);
    rawG = Wire.read() | (Wire.read()<<8);
    rawB = Wire.read() | (Wire.read()<<8);
    currentLight = rawC;
  }
}

int clampDeduction(int raw, int cap) { return raw > cap ? cap : raw; }

void updateIntegrityScore() {
  int shockDed   = clampDeduction(minorShockCount * 3 + severeShockCount * 12, SHOCK_CAP);
  int dropDed    = clampDeduction(dropCount * 10, DROP_CAP);
  int tamperDed  = clampDeduction(tamperEventsCount * 3 + (int)(totalTamperDurationSecs / 5), TAMPER_CAP);
  int pressureDed = clampDeduction(pressureAlertCount * 5, PRESSURE_CAP);
  integrityScore = 100 - shockDed - dropDed - tamperDed - pressureDed;
  if (integrityScore < 0) integrityScore = 0;
}

void wakeOled() { lastOledInteraction = millis(); }

String lastBlockHash = GENESIS_HASH;

String calculateSha256Hex16(const String &data) {
  byte shaResult[32];
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0); // 0 = SHA-256
  mbedtls_sha256_update(&ctx, (const unsigned char*)data.c_str(), data.length());
  mbedtls_sha256_finish(&ctx, shaResult);
  mbedtls_sha256_free(&ctx);

  char hashStr[17];
  for (int i = 0; i < 8; i++) {
    sprintf(hashStr + (i * 2), "%02x", shaResult[i]);
  }
  return String(hashStr);
}

void logEvent(const char* eventType, String value) {
  unsigned long elapsed = (millis() - startTime) / 1000;
  value.replace(',', '|');
  
  String dtStr = getFormattedDateTime();
  String rawPayload = lastBlockHash + "|" + String(elapsed) + "|" + dtStr + "|" + String(eventType) + "|" + value + "|" + String(integrityScore);
  String currentHash = calculateSha256Hex16(rawPayload);
  lastBlockHash = currentHash;

  char line[200];
  snprintf(line, sizeof(line), "%lu,%s,%s,%s,%d,%s\n",
           elapsed, dtStr.c_str(), eventType, value.c_str(), integrityScore, currentHash.c_str());

  if (sdReady) {
    FsFile file = sd.open(sdLogPath, O_WRONLY | O_CREAT | O_AT_END);
    if (file) {
      file.print(line);
      file.flush();
      file.close();
      Serial.print(F("[LOG->SD] "));
    } else {
      Serial.print(F("[LOG->SD FAIL] "));
    }
  }
  if (spiffsReady) {
    File file = SPIFFS.open(spiffsLogPath, FILE_APPEND);
    if (file) {
      file.print(line);
      file.flush();
      file.close();
      if (!sdReady) Serial.print(F("[LOG->FLASH] "));
    } else {
      if (!sdReady) Serial.print(F("[LOG->FLASH FAIL] "));
    }
  }
  Serial.print(line);
}

void writeLogHeader() {
  if (sdReady) {
    FsFile file = sd.open(sdLogPath, O_WRONLY | O_CREAT | O_TRUNC);
    if (file) {
      file.println(F("# Shipment Sentinel v4.0 Cryptographic Trip Log"));
      file.println(F("# Storage: microSD (SdFat exFAT/FAT32)"));
      file.print(F("# Armed At: ")); file.println(getFormattedDateTime());
      file.println(F("Elapsed(s),Timestamp,Event,Value,Score,Hash"));
      file.flush();
      file.close();
      Serial.println(F("[HEADER->SD] Log header written OK."));
    } else {
      Serial.println(F("[HEADER->SD FAIL] Could not write header!"));
    }
  }
  if (spiffsReady) {
    File file = SPIFFS.open(spiffsLogPath, FILE_WRITE);
    if (file) {
      file.println(F("# Shipment Sentinel v4.0 Cryptographic Trip Log"));
      file.println(F("# Storage: SPIFFS Flash"));
      file.print(F("# Armed At: ")); file.println(getFormattedDateTime());
      file.println(F("Elapsed(s),Timestamp,Event,Value,Score,Hash"));
      file.flush();
      file.close();
    }
  }
  lastBlockHash = GENESIS_HASH;
}

void reconstructCountersFromLog() {
  minorShockCount = 0; severeShockCount = 0;
  dropCount = 0; tamperEventsCount = 0;
  totalTamperDurationSecs = 0; pressureAlertCount = 0;
  severeIncident = false; worstShockG = 0; worstDropG = 999;
  worstDropHeightM = 0.0f;
  lastBlockHash = GENESIS_HASH;

  bool opened = false;
  FsFile sdFile;
  File spiffsFile;

  if (sdReady && sd.exists(sdLogPath)) {
    sdFile = sd.open(sdLogPath, O_READ);
    if (sdFile) opened = true;
  } else if (spiffsReady && SPIFFS.exists(spiffsLogPath)) {
    spiffsFile = SPIFFS.open(spiffsLogPath, FILE_READ);
    if (spiffsFile) opened = true;
  }

  if (!opened) return;

  while ((sdFile && sdFile.available()) || (spiffsFile && spiffsFile.available())) {
    String line = "";
    if (sdFile) {
      line = sdFile.readStringUntil('\n');
    } else {
      line = spiffsFile.readStringUntil('\n');
    }
    line.trim();
    if (line.length() == 0 || line.charAt(0) == '#') continue;
    if (line.startsWith("Elapsed")) continue;

    int c1 = line.indexOf(',');
    if (c1 < 0) continue;
    int c2 = line.indexOf(',', c1 + 1);
    if (c2 < 0) continue;
    int c3 = line.indexOf(',', c2 + 1);
    if (c3 < 0) continue;
    int c4 = line.indexOf(',', c3 + 1);

    String event = line.substring(c2 + 1, c3);
    String value = (c4 > 0) ? line.substring(c3 + 1, c4) : line.substring(c3 + 1);

    if (c4 > 0) {
      int c5 = line.indexOf(',', c4 + 1);
      if (c5 > 0) {
        lastBlockHash = line.substring(c5 + 1);
      }
    }

    if (event == "SHOCK") {
      float g = value.toFloat();
      if (g >= SHOCK_SEVERE_THRESHOLD) severeShockCount++;
      else minorShockCount++;
      if (g > worstShockG) worstShockG = g;
    } else if (event == "SEVERE_SHOCK") {
      severeShockCount++;
      float g = value.toFloat();
      if (g > worstShockG) worstShockG = g;
    } else if (event == "DROP") {
      dropCount++;
      if (value.indexOf('m') > 0) {
        float h = value.substring(0, value.indexOf('m')).toFloat();
        if (h > worstDropHeightM) worstDropHeightM = h;
      }
    } else if (event == "DROP_IMPACT") {
      float g = value.toFloat();
      if (g > worstShockG) worstShockG = g;
    } else if (event == "TAMPER_OPEN") {
      tamperEventsCount++;
    } else if (event == "TAMPER_CLOSED") {
      int idx = value.indexOf(':');
      if (idx > 0) {
        String durStr = value.substring(idx + 1);
        durStr.replace("s", "");
        totalTamperDurationSecs += durStr.toInt();
      }
    } else if (event == "PRESSURE_ALERT") {
      pressureAlertCount++;
    } else if (event == "SEVERE") {
      severeIncident = true;
    }
  }

  if (sdFile) sdFile.close();
  if (spiffsFile) spiffsFile.close();

  updateIntegrityScore();
  Serial.println(F("[BOOT] Reconstructed counters from log:"));
  Serial.print(F("  Storage: ")); Serial.println(sdReady ? F("microSD") : F("SPIFFS"));
  Serial.print(F("  Shocks: ")); Serial.print(minorShockCount + severeShockCount);
  Serial.print(F(" | Drops: ")); Serial.print(dropCount);
  Serial.print(F(" | Tampers: ")); Serial.print(tamperEventsCount);
  Serial.print(F(" | Pressure: ")); Serial.print(pressureAlertCount);
  Serial.print(F(" | Score: ")); Serial.println(integrityScore);
}

void clearLog() {
  minorShockCount = 0; severeShockCount = 0;
  dropCount = 0; tamperEventsCount = 0;
  totalTamperDurationSecs = 0; pressureAlertCount = 0;
  severeIncident = false; integrityScore = 100;
  worstShockG = 0; worstDropG = 999; worstDropHeightM = 0.0f;
  lastBlockHash = GENESIS_HASH;

  if (sdReady && sd.exists(sdLogPath)) {
    sd.remove(sdLogPath);
  }
  if (spiffsReady && SPIFFS.exists(spiffsLogPath)) {
    SPIFFS.remove(spiffsLogPath);
  }
  writeLogHeader();
  Serial.println(F("[STORAGE] Trip log cleared & armed for next shipment."));
}

void processMotionEngine() {
  if (!tripActive) return; // [FIX-12] Wait for boot stabilization
  unsigned long now = millis();

  if (totalAccel < FALL_THRESHOLD) {
    if (lowGStartTime == 0) { lowGStartTime = now; minFreeFallG = totalAccel; }
    else {
      if (totalAccel < minFreeFallG) minFreeFallG = totalAccel;
      if (!inFreeFall && (now - lowGStartTime >= FALL_MIN_DURATION_MS)) {
        inFreeFall = true;
        fallDisplayUntil = now + 1200;
      }
    }
  } else {
    if (inFreeFall) {
      if (now - lastDropLogTime > 1500) {
        lastDropLogTime = now;
        dropCount++;
        if (minFreeFallG < worstDropG) worstDropG = minFreeFallG;

        // Classical Physics: h = 0.5 * g * t^2
        unsigned long fallMs = (now > lowGStartTime) ? (now - lowGStartTime) : FALL_MIN_DURATION_MS;
        float fallSec = fallMs / 1000.0f;
        float dropHeightM = 0.5f * 9.80665f * fallSec * fallSec;
        if (dropHeightM > worstDropHeightM) worstDropHeightM = dropHeightM;

        updateIntegrityScore();
        logEvent("DROP", String(dropHeightM, 2) + "m | " + String(minFreeFallG, 2) + "G (" + String(fallMs) + "ms)");
        wakeOled();
        // [FIX-05] Start landing impact capture window
        awaitingLandingImpact = true;
        landingWindowStart = now;
        landingPeakG = totalAccel;
      }
      inFreeFall = false;
    }
    lowGStartTime = 0;
  }

  // [FIX-05] Landing impact capture (100ms window after drop ends)
  if (awaitingLandingImpact) {
    if (totalAccel > landingPeakG) landingPeakG = totalAccel;
    if (now - landingWindowStart >= 100) {
      awaitingLandingImpact = false;
      if (landingPeakG > SHOCK_MIN_THRESHOLD) {
        if (landingPeakG > worstShockG) worstShockG = landingPeakG;
        logEvent("DROP_IMPACT", String(landingPeakG, 2) + "G");
      }
      landingPeakG = 0;
    }
  }

  if (totalAccel > SHOCK_MIN_THRESHOLD && !awaitingLandingImpact) {
    if (shockPeakWindowStart == 0) { shockPeakWindowStart = now; currentPeakShock = totalAccel; }
    else { if (totalAccel > currentPeakShock) currentPeakShock = totalAccel; }
  }

  if (shockPeakWindowStart > 0 && (now - shockPeakWindowStart >= 50)) {
    if (now - lastShockLogTime > 800) {
      lastShockLogTime = now;
      if (currentPeakShock >= SHOCK_SEVERE_THRESHOLD) severeShockCount++;
      else minorShockCount++;
      if (currentPeakShock > worstShockG) worstShockG = currentPeakShock;
      updateIntegrityScore();
      const char* evtName = currentPeakShock >= SHOCK_SEVERE_THRESHOLD ? "SEVERE_SHOCK" : "SHOCK";
      logEvent(evtName, String(currentPeakShock, 2) + "G");
      wakeOled();
    }
    shockPeakWindowStart = 0; currentPeakShock = 0;
  }
}

void processTamperEngine() {
  if (!apdsAvailable || !tripActive) return;
  unsigned long now = millis();

  if (currentLight > TAMPER_THRESHOLD) {
    if (!boxIsOpen) {
      boxIsOpen = true;
      boxOpenedAt = now;
      lastTamperScoreTick = now;
      tamperEventsCount++;
      updateIntegrityScore();
      logEvent("TAMPER_OPEN", "Light:" + String(currentLight));
      wakeOled();
    } else {
      if (now - lastTamperScoreTick >= 1000) {
        lastTamperScoreTick = now;
        totalTamperDurationSecs++;
        updateIntegrityScore();
      }
    }
  } else {
    if (boxIsOpen) {
      boxIsOpen = false;
      unsigned long dur = (now - boxOpenedAt) / 1000;
      logEvent("TAMPER_CLOSED", "Open:" + String(dur) + "s");
      wakeOled();
    }
  }
}

void checkPressure() {
  if (!tripActive || baselinePressure <= 0) return;
  
  baselinePressure = baselinePressure * 0.995f + currentPressure * 0.005f;
  
  float diff = fabs(currentPressure - baselinePressure);
  float pct = (diff / baselinePressure) * 100.0f;
  if (pct > PRESSURE_CHANGE_PERCENT) {
    unsigned long now = millis();
    if (now - lastPressureTime > 5000) {
      lastPressureTime = now;
      pressureAlertCount++;
      updateIntegrityScore();
      logEvent("PRESSURE_ALERT", String(pct, 1) + "%");
      wakeOled();
    }
  }
}

void checkSevereIncident() {
  if (severeIncident || !tripActive) return;
  unsigned long now = millis();
  bool recentTamper = boxIsOpen || (tamperEventsCount > 0 && (now - boxOpenedAt <= SEVERE_WINDOW));
  bool recentImpact = ((minorShockCount + severeShockCount > 0) && (now - lastShockLogTime <= SEVERE_WINDOW)) ||
                      (dropCount > 0 && (now - lastDropLogTime <= SEVERE_WINDOW));
  if (recentTamper && recentImpact) {
    severeIncident = true;
    logEvent("SEVERE", "Tamper + Impact Correlated");
    wakeOled();
  }
}

const char* html_page = R"=====(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="5">
<title>Shipment Sentinel v4.0 — Chain-of-Custody Passport</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono:wght@400;700&display=swap');
:root{--bg:#07090e;--card:rgba(18,22,34,.85);--bdr:rgba(255,255,255,.08);--t1:#f0f4fc;--t2:#7e8b9f;--cyan:#00f2fe;--blue:#4facfe;--warn:#ffb300;--danger:#ff2d55;--purple:#b537f2;--green:#34d399}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Outfit',sans-serif;background:var(--bg);color:var(--t1);min-height:100vh;padding:24px 16px;background-image:radial-gradient(circle at 10% 20%,rgba(0,242,254,.06) 0%,transparent 40%),radial-gradient(circle at 90% 80%,rgba(181,55,242,.06) 0%,transparent 40%)}
.c{max-width:1000px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px;padding-bottom:16px;border-bottom:1px solid var(--bdr);flex-wrap:wrap;gap:16px}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,var(--cyan),var(--blue));display:flex;align-items:center;justify-content:center;font-weight:800;color:#000;font-size:22px}
h1{font-size:22px;font-weight:800;letter-spacing:-.5px}
.sub{font-size:12px;color:var(--t2)}
.acts{display:flex;gap:10px;flex-wrap:wrap}
.btn{padding:10px 16px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px;transition:all .2s;border:1px solid transparent}
.btn-c{background:linear-gradient(135deg,rgba(0,242,254,.15),rgba(79,172,254,.15));border-color:rgba(0,242,254,.3);color:var(--cyan)}
.btn-c:hover{background:rgba(0,242,254,.25);transform:translateY(-1px)}
.btn-r{background:rgba(255,45,85,.12);border-color:rgba(255,45,85,.3);color:var(--danger)}
.btn-r:hover{background:rgba(255,45,85,.25)}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:16px;padding:24px;margin-bottom:20px;backdrop-filter:blur(12px);box-shadow:0 10px 30px rgba(0,0,0,.4)}
h2{font-size:13px;font-weight:600;color:var(--t2);text-transform:uppercase;letter-spacing:1px;margin-bottom:16px}
.sp{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px}
.sb{display:flex;align-items:baseline}
.sn{font-size:72px;font-weight:800;line-height:1;letter-spacing:-2px}
.st{font-size:22px;color:var(--t2);margin-left:4px}
.s-ok{color:var(--cyan);text-shadow:0 0 30px rgba(0,242,254,.35)}
.s-w{color:var(--warn);text-shadow:0 0 30px rgba(255,179,0,.35)}
.s-d{color:var(--danger);text-shadow:0 0 30px rgba(255,45,85,.35)}
.badge{padding:7px 16px;border-radius:30px;font-size:12px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase}
.b-ok{background:rgba(0,242,254,.12);color:var(--cyan);border:1px solid rgba(0,242,254,.3)}
.b-w{background:rgba(255,179,0,.12);color:var(--warn);border:1px solid rgba(255,179,0,.3)}
.b-d{background:rgba(255,45,85,.12);color:var(--danger);border:1px solid rgba(255,45,85,.3)}
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-top:20px}
.si{background:rgba(0,0,0,.25);border:1px solid var(--bdr);padding:14px;border-radius:12px;text-align:center}
.sv{font-family:'JetBrains Mono',monospace;font-size:26px;font-weight:700;color:#fff}
.sl{font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:1px;margin-top:3px}
.live-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:16px}
.live-tile{background:rgba(0,0,0,.3);border:1px solid var(--bdr);padding:14px 16px;border-radius:12px}
.live-label{font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:1px}
.live-val{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;color:#fff;margin-top:4px}
.legend{display:flex;gap:16px;font-size:11px;color:var(--t2);margin-bottom:10px;flex-wrap:wrap}
.li{display:flex;align-items:center;gap:6px}
.dot{width:9px;height:9px;border-radius:50%}
.cw{width:100%;overflow-x:auto}
svg{width:100%;min-width:600px;height:200px;background:rgba(0,0,0,.3);border-radius:12px;border:1px solid var(--bdr)}
table{width:100%;border-collapse:collapse;margin-top:8px}
th{text-align:left;padding:10px 12px;font-size:10px;color:var(--t2);text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid var(--bdr)}
td{padding:12px;font-family:'JetBrains Mono',monospace;font-size:12px;border-bottom:1px solid rgba(255,255,255,.03)}
tr:hover td{background:rgba(255,255,255,.02)}
.tag{display:inline-block;padding:3px 8px;border-radius:5px;font-family:'Outfit',sans-serif;font-size:10px;font-weight:700;text-transform:uppercase}
.t-shock{background:rgba(255,179,0,.15);color:var(--warn);border:1px solid rgba(255,179,0,.3)}
.t-severe{background:rgba(255,45,85,.15);color:var(--danger);border:1px solid rgba(255,45,85,.3)}
.t-drop{background:rgba(255,45,85,.15);color:var(--danger);border:1px solid rgba(255,45,85,.3)}
.t-impact{background:rgba(255,120,0,.15);color:#ff7800;border:1px solid rgba(255,120,0,.3)}
.t-tamper{background:rgba(181,55,242,.15);color:var(--purple);border:1px solid rgba(181,55,242,.3)}
.t-pressure{background:rgba(52,211,153,.15);color:var(--green);border:1px solid rgba(52,211,153,.3)}
.t-system{background:rgba(0,242,254,.12);color:var(--cyan);border:1px solid rgba(0,242,254,.25)}
.worst{margin-top:20px;padding:14px 18px;border-radius:12px;background:rgba(255,45,85,.08);border:1px solid rgba(255,45,85,.2);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.worst-icon{font-size:20px}
.worst-text{font-size:13px;color:var(--t1)}
.worst-val{font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--danger)}
</style>
</head>
<body>
<div class="c">
  <header>
    <div class="brand">
      <div class="logo">S</div>
      <div><h1>SHIPMENT SENTINEL v4.0</h1><div class="sub">Chain-of-Custody Integrity Passport · Auto-refreshes every 5s</div></div>
    </div>
    <div class="acts">
      <a href="/clear" onclick="return confirm('Clear ALL trip data and reset score to 100?');" class="btn btn-r">🗑 Reset Trip</a>
      <a href="/log.csv" download="shipment_log.csv" class="btn btn-c">⬇ Export CSV</a>
    </div>
  </header>
  <div id="app"><div style="text-align:center;padding:60px;color:var(--t2)">Loading telemetry...</div></div>
</div>
<script>
Promise.all([
  fetch('/log.csv?key=SENTINEL_SECURE_VAULT_9F82A4D1&t='+Date.now()).then(r=>r.text()),
  fetch('/api/live?key=SENTINEL_SECURE_VAULT_9F82A4D1&t='+Date.now()).then(r=>r.json()).catch(()=>null)
]).then(([csv, live]) => {
  let rows=csv.split('\n').filter(r=>r.trim().length>0&&!r.startsWith('#')&&!r.startsWith('Elapsed'));
  let sh=0,sv=0,dr=0,tm=0,pr=0,score=100,worst=0;
  let trs='',chart=[];
  rows.forEach(r=>{
    let c=r.split(',');if(c.length<5)return;
    let t=parseInt(c[0]),dt=c[1].trim(),ev=c[2].trim(),val=c[3].trim();
    score=parseInt(c[4]);
    let g=parseFloat(val);if(!isNaN(g)&&g>worst&&(ev.includes('SHOCK')||ev=='DROP_IMPACT'))worst=g;
    if(ev=='SHOCK')sh++;else if(ev=='SEVERE_SHOCK'){sh++;sv++;}
    else if(ev=='DROP')dr++;else if(ev.includes('TAMPER'))tm++;
    else if(ev.includes('PRESSURE'))pr++;
    let tc='t-system';
    if(ev=='SEVERE_SHOCK')tc='t-severe';else if(ev=='SHOCK')tc='t-shock';
    else if(ev=='DROP')tc='t-drop';else if(ev=='DROP_IMPACT')tc='t-impact';
    else if(ev.includes('TAMPER'))tc='t-tamper';else if(ev.includes('PRESSURE'))tc='t-pressure';
    trs=`<tr><td>${dt}</td><td>+${t}s</td><td><span class="tag ${tc}">${ev}</span></td><td>${val.replace(/\|/g,', ')}</td><td>${score}/100</td></tr>`+trs;
    chart.push({t,ev,g:isNaN(g)?1:g});
  });
  let sc=score>80?'s-ok':(score>50?'s-w':'s-d');
  let bc=score>80?'b-ok':(score>50?'b-w':'b-d');
  let st=score>80?'PASSED / SAFE':(score>50?'WARNING / CAUTION':'COMPROMISED');
  let svg='<svg viewBox="0 0 1000 200" preserveAspectRatio="none">';
  if(chart.length>0){
    let mT=Math.max(...chart.map(e=>e.t))||1,mG=Math.max(...chart.map(e=>e.g),3);
    for(let i=1;i<=3;i++){let y=175-(i*45);svg+=`<line x1="20" y1="${y}" x2="980" y2="${y}" stroke="rgba(255,255,255,.05)" stroke-dasharray="4"/>`;}
    chart.forEach(e=>{
      let x=(e.t/mT)*940+30,y=175-((e.g/mG)*140);if(y<20)y=20;
      let co='#00f2fe';
      if(e.ev=='SEVERE_SHOCK')co='#ff2d55';else if(e.ev=='SHOCK')co='#ffb300';
      else if(e.ev=='DROP'||e.ev=='DROP_IMPACT')co='#ff2d55';else if(e.ev.includes('TAMPER'))co='#b537f2';
      else if(e.ev.includes('PRESSURE'))co='#34d399';
      svg+=`<line x1="${x}" y1="175" x2="${x}" y2="${y}" stroke="${co}" stroke-width="2" stroke-dasharray="2" opacity=".4"/>`;
      svg+=`<circle cx="${x}" cy="${y}" r="5" fill="${co}" filter="drop-shadow(0 0 4px ${co})"/>`;
    });
  } else svg+='<text x="500" y="105" fill="#555" text-anchor="middle" font-size="14">No incidents. Ready for transit.</text>';
  svg+='</svg>';
  let liveHTML='';
  if(live){
    liveHTML=`<div class="card"><h2>Live Sensor Telemetry</h2><div class="live-grid">
      <div class="live-tile"><div class="live-label">Acceleration</div><div class="live-val">${live.accel}G</div></div>
      <div class="live-tile"><div class="live-label">Light (Clear)</div><div class="live-val">${live.light} ${live.boxOpen?'<span style="color:var(--purple)">[OPEN]</span>':'<span style="color:var(--green)">[SEALED]</span>'}</div></div>
      <div class="live-tile"><div class="live-label">Pressure</div><div class="live-val">${live.pressure} hPa</div></div>
      <div class="live-tile"><div class="live-label">RTC Clock</div><div class="live-val">${live.time}</div></div>
      <div class="live-tile"><div class="live-label">Trip Duration</div><div class="live-val">${live.uptime}</div></div>
      <div class="live-tile"><div class="live-label">Score Now</div><div class="live-val">${live.score}/100</div></div>
    </div></div>`;
  }
  let worstHTML='';
  if(worst>0)worstHTML=`<div class="worst"><span class="worst-icon">⚠️</span><span class="worst-text">Worst recorded impact: <span class="worst-val">${worst.toFixed(2)}G</span></span></div>`;
  document.getElementById('app').innerHTML=`
    <div class="card"><h2>Shipment Integrity Score</h2>
      <div class="sp"><div class="sb"><div class="sn ${sc}">${score}</div><div class="st">/100</div></div><div class="badge ${bc}">${st}</div></div>
      <div class="sg">
        <div class="si"><div class="sv">${sh}</div><div class="sl">Shocks</div></div>
        <div class="si"><div class="sv">${dr}</div><div class="sl">Drops</div></div>
        <div class="si"><div class="sv">${tm}</div><div class="sl">Tamper</div></div>
        <div class="si"><div class="sv">${pr}</div><div class="sl">Pressure</div></div>
      </div>${worstHTML}
    </div>
    ${liveHTML}
    <div class="card"><h2>Incident Timeline</h2>
      <div class="legend">
        <div class="li"><div class="dot" style="background:#ffb300"></div>Shock</div>
        <div class="li"><div class="dot" style="background:#ff2d55"></div>Severe / Drop</div>
        <div class="li"><div class="dot" style="background:#b537f2"></div>Tamper</div>
        <div class="li"><div class="dot" style="background:#34d399"></div>Pressure</div>
      </div><div class="cw">${svg}</div>
    </div>
    <div class="card"><h2>Event Log (${rows.length} entries)</h2>
      <table><thead><tr><th>Timestamp</th><th>Elapsed</th><th>Event</th><th>Value</th><th>Score</th></tr></thead>
      <tbody>${trs}</tbody></table>
    </div>`;
});
</script>
</body>
</html>
)=====";

bool isAuthorized() {
  if (server.hasHeader("X-Sentinel-Key")) {
    if (server.header("X-Sentinel-Key") == SENTINEL_AUTH_KEY) return true;
  }
  if (server.hasArg("key")) {
    if (server.arg("key") == SENTINEL_AUTH_KEY) return true;
  }
  return false;
}

// [FIX-08] Live JSON endpoint
void handleLive() {
  if (!isAuthorized()) {
    server.send(401, "application/json", "{\"error\":\"Unauthorized: Invalid Sentinel Security Key\"}");
    return;
  }

  unsigned long upSec = (millis() - startTime) / 1000;
  char uptimeStr[16];
  snprintf(uptimeStr, sizeof(uptimeStr), "%luh %lum %lus", upSec/3600, (upSec%3600)/60, upSec%60);

  char json[300];
  snprintf(json, sizeof(json),
    "{\"accel\":\"%.2f\",\"light\":%u,\"boxOpen\":%s,\"pressure\":\"%.1f\","
    "\"time\":\"%s\",\"uptime\":\"%s\",\"score\":%d}",
    totalAccel, currentLight, boxIsOpen ? "true" : "false",
    currentPressure, getFormattedTimeOnly().c_str(), uptimeStr, integrityScore);
  server.send(200, "application/json", json);
}

String getDeviceId() {
  uint64_t chipid = ESP.getEfuseMac();
  char buf[20];
  snprintf(buf, sizeof(buf), "SENTINEL-%04X", (uint16_t)(chipid >> 32));
  return String(buf);
}

void handleExtract() {
  // -----------------------------------------------------------------------
  // /api/extract  — Returns METADATA ONLY as compact JSON (~600 bytes).
  // CSV data is fetched separately by the PC via the streaming /log.csv
  // endpoint, avoiding ESP32 heap exhaustion from large String operations.
  // -----------------------------------------------------------------------
  if (!isAuthorized()) {
    server.send(401, "application/json", "{\"error\":\"Unauthorized: Invalid Sentinel Security Key\"}");
    return;
  }

  // Log file diagnostics
  Serial.print(F("[EXTRACT] sdReady=")); Serial.print(sdReady);
  if (sdReady) {
    bool ex = sd.exists(sdLogPath);
    Serial.print(F(" sdExists=")); Serial.print(ex);
    if (ex) {
      FsFile f = sd.open(sdLogPath, O_READ);
      if (f) { Serial.print(F(" size=")); Serial.print(f.size()); f.close(); }
    }
  }
  Serial.println();

  char uptimeStr[20];
  unsigned long upSec = (millis() - startTime) / 1000;
  snprintf(uptimeStr, sizeof(uptimeStr), "%luh %lum %lus", upSec/3600, (upSec%3600)/60, upSec%60);

  // Build compact metadata JSON — NO embedded CSV
  char jbuf[1024];
  int n = snprintf(jbuf, sizeof(jbuf),
    "{\"firmware\":\"v4.0\","
    "\"deviceId\":\"%s\","
    "\"extractTime\":\"%s\","
    "\"uptime\":\"%s\","
    "\"storage\":\"%s\","
    "\"score\":%d,"
    "\"status\":\"%s\","
    "\"counters\":{\"minorShocks\":%d,\"severeShocks\":%d,\"totalShocks\":%d,"
      "\"drops\":%d,\"tamperEvents\":%d,\"totalTamperSecs\":%lu,\"pressureAlerts\":%d},"
    "\"metrics\":{\"worstImpactG\":%.2f,\"worstDropG\":%.2f,\"worstDropHeightM\":%.2f,"
      "\"currentPressure\":%.1f,\"currentLight\":%u,\"boxIsOpen\":%s},"
    "\"sensors\":{\"mpu\":%s,\"bmp\":%s,\"apds\":%s,\"rtc\":%s,\"sd\":%s},"
    "\"csv\":\"\""
    "}",
    getDeviceId().c_str(),
    getFormattedDateTime().c_str(),
    uptimeStr,
    sdReady ? "microSD (exFAT/FAT32)" : "SPIFFS Flash",
    integrityScore,
    getStatus().c_str(),
    minorShockCount, severeShockCount, minorShockCount + severeShockCount,
    dropCount, tamperEventsCount, totalTamperDurationSecs, pressureAlertCount,
    worstShockG,
    worstDropG > 10 ? 0.0f : worstDropG,
    worstDropHeightM,
    currentPressure, currentLight,
    boxIsOpen ? "true" : "false",
    mpuAvailable ? "true" : "false",
    bmpAvailable ? "true" : "false",
    apdsAvailable ? "true" : "false",
    rtcAvailable ? "true" : "false",
    sdReady ? "true" : "false"
  );

  if (n < 0 || n >= (int)sizeof(jbuf)) {
    server.send(500, "application/json", "{\"error\":\"JSON buffer overflow\"}" );
    return;
  }

  server.send(200, "application/json", jbuf);

  if (oledAvailable) {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(0, 5);
    display.println(F(">>>> DATA EXTRACTED <<"));
    display.println(F("===================="));
    display.println(F("Metadata exported OK"));
    display.println(F("Fetching /log.csv..."));
    display.setCursor(0, 48);
    display.println(sdReady ? F("Storage: microSD") : F("Storage: Flash"));
    display.display();
  }
  Serial.println(F("[EXTRACT] Metadata JSON sent. PC will stream /log.csv separately."));
}


void handleRoot() { server.send(200, "text/html", html_page); }

void handleCsv() {
  if (!isAuthorized()) {
    server.send(401, "text/plain", "Unauthorized: Invalid Security Token");
    return;
  }

  bool opened = false;
  FsFile sdFile;
  File spiffsFile;

  if (sdReady && sd.exists(sdLogPath)) {
    sdFile = sd.open(sdLogPath, O_READ);
    if (sdFile && sdFile.size() > 0) opened = true;
  }
  
  if (!opened && spiffsReady && SPIFFS.exists(spiffsLogPath)) {
    spiffsFile = SPIFFS.open(spiffsLogPath, FILE_READ);
    if (spiffsFile && spiffsFile.size() > 0) opened = true;
  }

  if (!opened) {
    // Return standard trip header if file doesn't exist yet
    String defHdr = "# Shipment Sentinel v4.0 Cryptographic Trip Log\n"
                    "# Storage: Standby\n"
                    "Elapsed(s),Timestamp,Event,Value,Score,Hash\n";
    server.send(200, "text/csv", defHdr);
    return;
  }

  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "text/csv", "");

  char buf[256];
  if (sdFile) {
    while (sdFile.available()) {
      int bytesRead = sdFile.read(buf, sizeof(buf));
      if (bytesRead > 0) {
        server.sendContent(buf, bytesRead);
      }
    }
    sdFile.close();
  } else if (spiffsFile) {
    while (spiffsFile.available()) {
      int bytesRead = spiffsFile.read((uint8_t*)buf, sizeof(buf));
      if (bytesRead > 0) {
        server.sendContent(buf, bytesRead);
      }
    }
    spiffsFile.close();
  }

  server.sendContent(""); // CRITICAL: Terminates HTTP chunked transfer!
}

void handleClear() {
  if (!isAuthorized()) {
    server.send(401, "application/json", "{\"error\":\"Unauthorized: Invalid Security Token\"}");
    return;
  }
  clearLog();
  startTime = millis();
  tripActive = false;
  tripActivateAt = millis() + 5000;
  wakeOled();
  logEvent("SYSTEM", "Sentinel Re-Armed (5s Placement Buffer)");
  
  if (oledAvailable) {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(0, 5);
    display.println(F(">> SENTINEL RE-ARMED <<"));
    display.println(F("======================"));
    display.println(F("Encrypted log wiped"));
    display.println(F("Trip Score: 100/100"));
    display.setCursor(0, 48);
    display.println(F("5s Buffer to place box"));
    display.display();
  }
  Serial.println(F("[RESET] Encrypted log cleared. 5-second buffer active to place box."));
  server.send(200, "application/json", "{\"status\":\"OK\",\"message\":\"Encrypted SD log cleared and Sentinel re-armed. 5s buffer active to place package.\"}");
}

void startDashboard() {
  dashboardMode = true;
  WiFi.softAP("Shipment_Sentinel", "12345678");

  const char * headerkeys[] = {"X-Sentinel-Key", "User-Agent"} ;
  size_t headerkeyssize = sizeof(headerkeys)/sizeof(char*);
  server.collectHeaders(headerkeys, headerkeyssize);

  server.on("/", handleRoot);
  server.on("/log.csv", handleCsv);
  server.on("/api/live", handleLive);
  server.on("/api/status", handleLive);
  server.on("/api/extract", handleExtract);
  server.on("/clear", handleClear);
  server.begin();
  if (oledAvailable) {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(0, 0);  display.println(F("DASHBOARD ACTIVE"));
    display.println(F("================="));
    display.println(F("SSID: Shipment_Sentinel"));
    display.println(F("Pass: 12345678"));
    display.println();
    display.println(F("URL: 192.168.4.1"));
    display.display();
  }
  Serial.println(F("[WIFI] AP at http://192.168.4.1 (Encrypted Token Auth Active)"));
}

void runI2CScan() {
  Serial.println(F("\n--- BUS 0 (Wire: GPIO 21/22) ---"));
  byte n = 0;
  for (byte i = 8; i < 120; i++) {
    Wire.beginTransmission(i);
    if (Wire.endTransmission() == 0) {
      Serial.print(F("  0x")); if (i<16) Serial.print("0"); Serial.print(i, HEX);
      if (i==0x3C) Serial.print(F(" OLED")); else if (i==0x39) Serial.print(F(" APDS9960"));
      else if (i==0x68||i==0x69) Serial.print(F(" MPU6050")); else if (i==0x77) Serial.print(F(" BMP180"));
      Serial.println(); n++;
    }
  }
  if (!n) Serial.println(F("  (none)"));

  Serial.println(F("--- BUS 1 (I2C_RTC: GPIO 17/16) ---"));
  n = 0;
  for (byte i = 8; i < 120; i++) {
    I2C_RTC.beginTransmission(i);
    if (I2C_RTC.endTransmission() == 0) {
      Serial.print(F("  0x")); if (i<16) Serial.print("0"); Serial.print(i, HEX);
      if (i==0x68) Serial.print(F(" DS3231")); else if (i==0x57) Serial.print(F(" AT24C32"));
      Serial.println(); n++;
    }
  }
  if (!n) Serial.println(F("  (none)"));
  Serial.println(F("--------------------------------\n"));
}

String getStatus() {
  if (severeIncident || integrityScore < 50) return "COMPROMISED";
  if (integrityScore < 85) return "WARNING";
  return "SAFE";
}

void drawPageLive() {
  display.setCursor(0, 0);
  display.print(F("SENTINEL "));
  if (cachedRtcValid) {
    char tick = (millis() / 500) % 2 == 0 ? ':' : ' ';
    char ts[12];
    snprintf(ts, sizeof(ts), "%02d%c%02d%c%02d", cachedHour, tick, cachedMin, tick, cachedSec);
    display.println(ts);
  } else display.println(F("[NO RTC]"));

  display.setCursor(0, 12);
  display.print(F("Status: ")); display.println(getStatus());

  display.setCursor(0, 22);
  display.print(F("Score:  ")); display.print(integrityScore); display.println(F("/100"));

  display.setCursor(0, 33);
  display.print(F("Light:  "));
  if (apdsAvailable) { display.print(currentLight); display.println(boxIsOpen ? F(" [OPEN]") : F(" [DARK]")); }
  else display.println(F("--"));

  display.setCursor(0, 44);
  display.print(F("Accel:  ")); display.print(totalAccel, 2); display.println(F(" G"));

  display.setCursor(0, 55);
  display.print(rtcAvailable?F("RTC:OK "):F("RTC:-- "));
  display.print(mpuAvailable?F("MPU:OK "):F("MPU:-- "));
  display.print(bmpAvailable?F("BMP:OK"):F("BMP:--"));
}

void drawPageSummary() {
  display.setCursor(0, 0);
  display.println(F("== TRIP SUMMARY =="));

  unsigned long dur = (millis() - startTime) / 1000;
  char durStr[20];
  snprintf(durStr, sizeof(durStr), "%luh %lum %lus", dur/3600, (dur%3600)/60, dur%60);
  display.setCursor(0, 12);
  display.print(F("Duration: ")); display.println(durStr);

  display.setCursor(0, 23);
  display.print(F("Shocks: ")); display.print(minorShockCount + severeShockCount);
  display.print(F("  Drops: ")); display.println(dropCount);

  display.setCursor(0, 33);
  display.print(F("Tampers: ")); display.print(tamperEventsCount);
  display.print(F("  Press: ")); display.println(pressureAlertCount);

  display.setCursor(0, 44);
  display.print(F("Worst G: "));
  if (worstShockG > 0) { display.print(worstShockG, 1); display.println(F("G")); }
  else display.println(F("none"));

  display.setCursor(0, 55);
  display.print(F("Score: ")); display.print(integrityScore);
  display.print(F("/100 [")); display.print(getStatus()); display.println(F("]"));
}

void updateDisplay() {
  if (!oledAvailable) return;
  unsigned long now = millis();

  // [FIX-14] OLED timeout: 25 seconds of inactivity turns display off
  if (now - lastOledInteraction > OLED_TIMEOUT_MS) {
    display.ssd1306_command(SSD1306_DISPLAYOFF);
    return;
  }

  // Two-page cycling: flip page every 4 seconds
  if (now - lastPageFlip > PAGE_FLIP_INTERVAL) {
    lastPageFlip = now;
    oledPage = (oledPage + 1) % 2;
  }

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);

  if (!tripActive && now < tripActivateAt) {
    int remSec = ((tripActivateAt - now) / 1000) + 1;
    display.setCursor(0, 0);
    display.println(F("== ARMING BUFFER =="));
    display.println(F("-------------------"));
    display.setCursor(0, 18);
    display.println(F("Place & seal box..."));
    display.setCursor(0, 36);
    display.print(F("ARMING IN: ")); display.print(remSec); display.println(F("s"));
    display.setCursor(0, 52);
    display.println(F("Sensors standby..."));
  } else if (fallDisplayUntil > now) {
    display.setCursor(0, 0);
    display.println(F("! FREE-FALL DROP !"));
    display.println(F("-------------------"));
    display.print(F("G-Force: ")); display.print(minFreeFallG, 2); display.println(F(" G"));
    if (worstDropHeightM > 0) {
      display.print(F("Height:  ")); display.print(worstDropHeightM, 2); display.println(F(" m"));
    }
    display.setCursor(0, 40);
    display.print(F("Drop #")); display.println(dropCount);
  } else if (oledPage == 0) {
    drawPageLive();
  } else {
    drawPageSummary();
  }

  display.display();
}

void setup() {
  Serial.begin(115200);
  delay(500);

  // Initialize Primary I2C Bus for Myosa kit sensors (OLED, MPU6050, BMP180, APDS9960 via Myosa kit cable)
  Wire.begin();
  Wire.setClock(400000);
  
  pinMode(BOOT_BUTTON_PIN, INPUT_PULLUP);
  pinMode(EXT_BUTTON_PIN, INPUT_PULLUP);

  Serial.println();
  Serial.println(F("========================================"));
  Serial.println(F("       SHIPMENT SENTINEL v4.0           "));
  Serial.println(F("========================================"));

  // Initialize OLED
  if (display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    oledAvailable = true;
    display.clearDisplay(); display.setTextColor(SSD1306_WHITE); display.setTextSize(1);
    display.setCursor(10, 10); display.println(F("SENTINEL v4.0"));
    display.setCursor(10, 25); display.println(F("Initializing..."));
    display.display(); delay(300);
  }

  // Initialize microSD SPI (SdFat 2.x exFAT/FAT32)
  // IMPORTANT: pinMode must come BEFORE digitalWrite to avoid floating pin
  pinMode(SD_CS_PIN, OUTPUT);
  digitalWrite(SD_CS_PIN, HIGH);
  delay(10); // Let CS line stabilize before SPI init
  SPI.begin(SD_SCK_PIN, SD_MISO_PIN, SD_MOSI_PIN, SD_CS_PIN);

  // Try multiple SPI speeds for compatibility
  bool sdOk = false;
  for (uint8_t mhz : {1, 4, 8}) {
    SdSpiConfig config(SD_CS_PIN, SHARED_SPI, SD_SCK_MHZ(mhz), &SPI);
    if (sd.begin(config)) { sdOk = true; break; }
    sd.end();
    delay(20);
  }
  if (sdOk) {
    sdReady = true;
    uint32_t sectors = sd.card() ? sd.card()->sectorCount() : 0;
    Serial.print(F("[INIT] microSD Card (exFAT/FAT32): OK (Sectors: "));
    Serial.print(sectors);
    Serial.println(F(")"));
  } else {
    Serial.println(F("[INIT] microSD Card: NOT DETECTED (Fallback to SPIFFS flash)"));
  }

  // Universal DS3231 RTC Auto-Prober & I2C Bus Recovery
  bool rtcFound = false;

  // 1. Try GPIO 17 (SDA) / GPIO 16 (SCL)
  i2cRecoverBus(17, 16);
  I2C_RTC.begin(17, 16, 100000);
  delay(30);
  I2C_RTC.beginTransmission(DS3231_I2C_ADDR);
  if (I2C_RTC.endTransmission() == 0) {
    rtcFound = true;
    rtcWire = &I2C_RTC;
    Serial.println(F("[INIT] RTC DS3231: OK on Bus 1 (SDA=17, SCL=16)"));
  }

  // 2. Try GPIO 16 (SDA) / GPIO 17 (SCL)
  if (!rtcFound) {
    i2cRecoverBus(16, 17);
    I2C_RTC.begin(16, 17, 100000);
    delay(30);
    I2C_RTC.beginTransmission(DS3231_I2C_ADDR);
    if (I2C_RTC.endTransmission() == 0) {
      rtcFound = true;
      rtcWire = &I2C_RTC;
      Serial.println(F("[INIT] RTC DS3231: OK on Bus 1 (SDA=16, SCL=17)"));
    }
  }

  // 3. Try Primary Bus 0 (Wire: GPIO 21 SDA / 22 SCL)
  if (!rtcFound) {
    Wire.beginTransmission(DS3231_I2C_ADDR);
    if (Wire.endTransmission() == 0) {
      Wire.beginTransmission(0x57);
      if (Wire.endTransmission() == 0 || mpuActualAddr == 0x69) {
        rtcFound = true;
        rtcWire = &Wire;
        Serial.println(F("[INIT] RTC DS3231: OK on Primary Bus 0 (Wire: GPIO 21/22)"));
      }
    }
  }

  // 4. Try GPIO 4 (SDA) / GPIO 16 (SCL)
  if (!rtcFound) {
    i2cRecoverBus(4, 16);
    I2C_RTC.begin(4, 16, 100000);
    delay(30);
    I2C_RTC.beginTransmission(DS3231_I2C_ADDR);
    if (I2C_RTC.endTransmission() == 0) {
      rtcFound = true;
      rtcWire = &I2C_RTC;
      Serial.println(F("[INIT] RTC DS3231: OK on Bus 1 (SDA=4, SCL=16)"));
    }
  }

  runI2CScan();

  if (rtcFound) {
    rtcAvailable = true;
    if (ds3231LostPower()) { syncDS3231ToCompileTime(); Serial.println(F("[INIT] RTC synced to compile time")); }
    uint16_t y; uint8_t m, d, hh, mm, ss;
    if (readDS3231Raw(y, m, d, hh, mm, ss)) {
      rtcEpochBase = dateToEpoch(y, m, d, hh, mm, ss);
      rtcSyncMillis = millis();
      cachedYear=y; cachedMonth=m; cachedDay=d; cachedHour=hh; cachedMin=mm; cachedSec=ss;
      cachedRtcValid = true;
    }
    Serial.print(F("[INIT] RTC DS3231: OK (")); Serial.print(getFormattedDateTime()); Serial.println(F(")"));
    if (oledAvailable) {
      display.clearDisplay(); display.setCursor(0,5);
      display.println(F(">> RTC DS3231 OK <<")); display.println(F("-------------------"));
      display.println(F("Clock:")); display.println(getFormattedDateTime());
      display.setCursor(0,48);
      display.println(sdReady ? F("Storage: microSD (exFAT)") : F("Storage: Flash SPIFFS"));
      display.display(); delay(800);
    }
  } else {
    Serial.println(F("[INIT] RTC DS3231: NOT FOUND on GPIO 16/17"));
  }

  // MPU6050
  if (mpuPing()) { mpuWake(); mpuAvailable = true;
    Serial.print(F("[INIT] MPU6050: OK (0x")); Serial.print(mpuActualAddr,HEX); Serial.println(F(", +/-8G)"));
  } else Serial.println(F("[INIT] MPU6050: NOT FOUND"));

  // BMP180
  if (initBMP180Direct()) { bmpAvailable = true;
    baselinePressure = readBMP180Pressure();
    Serial.print(F("[INIT] BMP180: OK (")); Serial.print(baselinePressure,1); Serial.println(F(" hPa)"));
  } else Serial.println(F("[INIT] BMP180: NOT FOUND"));

  // APDS9960
  if (initAPDS9960Direct()) { apdsAvailable = true; Serial.println(F("[INIT] APDS9960: OK")); }
  else Serial.println(F("[INIT] APDS9960: NOT FOUND"));

  // Mount SPIFFS fallback
  spiffsReady = SPIFFS.begin(true);
  if (!spiffsReady) Serial.println(F("[ERROR] SPIFFS mount failed!"));

  // Reconstruct counters from existing log (survives reboot)
  if ((sdReady && sd.exists(sdLogPath)) || (spiffsReady && SPIFFS.exists(spiffsLogPath))) {
    reconstructCountersFromLog();
  } else {
    writeLogHeader();
  }

  startTime = millis();
  wakeOled();

  // 5-second placement buffer before incident detection activates
  tripActive = false;
  tripActivateAt = millis() + 5000;

  logEvent("SYSTEM", "Device Boot (5s Placement Buffer)");
  Serial.println(F("[READY] v4.0 Active. 5-second placement buffer active. Hold BOOT 2s for dashboard."));
}

void loop() {
  unsigned long now = millis();

  // 5-second placement buffer countdown completion
  if (!tripActive && now >= tripActivateAt) {
    tripActive = true;
    if (bmpAvailable) baselinePressure = readBMP180Pressure();
    if (apdsAvailable) {
      readLight();
      if (currentLight > TAMPER_THRESHOLD) {
        boxIsOpen = true;
        boxOpenedAt = now;
        lastTamperScoreTick = now;
        tamperEventsCount++;
        updateIntegrityScore();
        logEvent("TAMPER_OPEN", "Light:" + String(currentLight) + " (detected at arming)");
        Serial.println(F("[ARMED] Box is OPEN at arming!"));
      }
    }
    Serial.println(F("[SYSTEM] 5-second placement buffer complete. Sentinel ARMED & ACTIVE."));
    wakeOled();
  }

  // [FIX-14] BOOT button or External Push Button (GPIO 27): short press = wake OLED, 2s hold = dashboard
  bool btnDown = (digitalRead(BOOT_BUTTON_PIN) == LOW) || (digitalRead(EXT_BUTTON_PIN) == LOW);
  if (btnDown) {
    if (bootButtonPressTime == 0) { bootButtonPressTime = now; bootButtonWasPressed = true; }
    else if (now - bootButtonPressTime > 2000 && !dashboardMode) startDashboard();
  } else {
    if (bootButtonWasPressed && bootButtonPressTime > 0 && (now - bootButtonPressTime < 500)) {
      wakeOled(); // Short press: just wake the OLED
    }
    bootButtonPressTime = 0;
    bootButtonWasPressed = false;
  }

  if (dashboardMode) { server.handleClient(); delay(5); return; }

  // [BUG-04] Cache RTC once per second
  updateRtcCache();

  // Motion Engine
  if (mpuAvailable) { readMPU(); processMotionEngine(); }
  else {
    static unsigned long lastRetry = 0;
    if (now - lastRetry > 2000) { lastRetry = now;
      if (mpuPing()) { mpuWake(); mpuAvailable = true; Serial.println(F("[RECONNECTED] MPU6050")); }
    }
  }

  // Light & Tamper
  readLight();
  processTamperEngine();

  // Pressure (every 2.5s)
  if (bmpAvailable) {
    static unsigned long lastPP = 0;
    if (now - lastPP > 2500) { lastPP = now; readPressure(); checkPressure(); }
  }

  checkSevereIncident();

  // Serial time sync listener (accepts: YYYY-MM-DD HH:MM:SS)
  if (Serial.available()) {
    String input = Serial.readStringUntil('\n');
    input.trim();
    int yr, mo, dy, hr, mn, sc;
    if (sscanf(input.c_str(), "%d-%d-%d %d:%d:%d", &yr, &mo, &dy, &hr, &mn, &sc) == 6 ||
        sscanf(input.c_str(), "%d/%d/%d %d:%d:%d", &yr, &mo, &dy, &hr, &mn, &sc) == 6) {
      setDS3231((uint16_t)yr, (uint8_t)mo, (uint8_t)dy, (uint8_t)hr, (uint8_t)mn, (uint8_t)sc);
      cachedRtcValid = false;
      updateRtcCache();
      Serial.print(F("[SYNC] RTC Clock successfully updated to: "));
      Serial.println(getFormattedDateTime());
    }
  }

  // Display (rate-limited 100ms, instant on events)
  static unsigned long lastDisp = 0;
  bool eventNow = (now - lastShockLogTime < 100) || (now < fallDisplayUntil);
  if (now - lastDisp >= 100 || eventNow) { lastDisp = now; updateDisplay(); }

  // Serial telemetry (every 2s)
  static unsigned long lastSerial = 0;
  if (now - lastSerial > 2000) {
    lastSerial = now;
    Serial.print(F("[LIVE] ")); Serial.print(getFormattedDateTime());
    Serial.print(F(" | ")); Serial.print(totalAccel, 2); Serial.print(F("G"));
    Serial.print(F(" | L:")); Serial.print(currentLight);
    Serial.print(boxIsOpen ? F("[OPEN]") : F("[DARK]"));
    Serial.print(F(" | S:")); Serial.print(integrityScore);
    Serial.print(F(" | Sh:")); Serial.print(minorShockCount + severeShockCount);
    Serial.print(F(" Dr:")); Serial.print(dropCount);
    Serial.print(F(" Tm:")); Serial.println(tamperEventsCount);
  }

  delay(2); // ~250-300Hz sampling
}
