#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <ArduinoJson.h>

Adafruit_PWMServoDriver pca = Adafruit_PWMServoDriver(0x40);

#define SERVO_FREQ   50
#define SERVO_MIN   150
#define SERVO_MAX   600

// 基準位置(起動時の角度)を軸に ±60度 を可動域とする
#define ARM_CENTER 90
#define ARM_RANGE  60
#define ARM_MIN (ARM_CENTER - ARM_RANGE)
#define ARM_MAX (ARM_CENTER + ARM_RANGE)

#define CH_RIGHT_ARM 4
#define CH_LEFT_ARM  3

// チャンネル3に取り付けたサーボ自体が逆向きのため、このチャンネル番号を軸に反転させる
#define CH_REVERSED  3

// 実際にサーボへ送る角度（clamp後）を返す。呼び出し側はこれを応答の angle として使う。
int setServoAngle(uint8_t channel, int angle) {
  angle = constrain(angle, ARM_MIN, ARM_MAX);

  int physicalAngle = angle;
  if (channel == CH_REVERSED) {
    physicalAngle = ARM_CENTER - (angle - ARM_CENTER);
  }

  uint16_t pulse = map(physicalAngle, 0, 180, SERVO_MIN, SERVO_MAX);
  pca.setPWM(channel, 0, pulse);
  return angle;
}

bool isValidChannel(uint8_t channel) {
  return channel == CH_LEFT_ARM || channel == CH_RIGHT_ARM;
}

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  pca.begin();
  pca.setOscillatorFrequency(25000000);
  pca.setPWMFreq(SERVO_FREQ);
  delay(100);

  // 基準位置に戻す
  setServoAngle(CH_RIGHT_ARM, ARM_CENTER);
  setServoAngle(CH_LEFT_ARM, ARM_CENTER);

  Serial.println("READY");
}

void loop() {
  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.isEmpty()) return;

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) {
    Serial.println("{\"status\":\"error\",\"message\":\"invalid json\"}");
    return;
  }

  uint8_t channel = doc["channel"];
  int angle       = doc["angle"];

  if (!isValidChannel(channel)) {
    Serial.println("{\"status\":\"error\",\"message\":\"invalid channel\"}");
    return;
  }

  int clamped = setServoAngle(channel, angle);

  Serial.printf("{\"status\":\"ok\",\"channel\":%d,\"angle\":%d}\n", channel, clamped);
}
