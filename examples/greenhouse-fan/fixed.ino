// Greenhouse fan, after BenchForge: smoothed readings, hysteresis, rollover-safe timing,
// and a fan that fails ON if the sensor wire breaks.
const int TEMP_PIN = A0;
const int FAN_PIN = 7;
const int LED_PIN = 13;

const float FAN_ON_C = 30.0;
const float FAN_OFF_C = 27.0;
const float MAX_PLAUSIBLE_C = 85.0;   // an LM35 in a greenhouse never reads this
const float MAX_JUMP_C = 5.0;         // real air can't change this much in 50 ms

bool fanOn = false;
bool sensorFault = false;
float lastC = NAN;
unsigned long lastRead = 0;
unsigned long lastBlink = 0;
bool ledOn = false;

float readCelsius() {
  long sum = 0;
  for (int i = 0; i < 8; i++) sum += analogRead(TEMP_PIN);
  return (sum / 8.0) * 500.0 / 1024.0;
}

void setup() {
  pinMode(FAN_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(FAN_PIN, LOW);
  Serial.begin(9600);
  Serial.println("greenhouse fan ready");
}

void loop() {
  unsigned long now = millis();

  if (now - lastRead >= 50) {
    lastRead = now;
    float c = readCelsius();
    if (c > MAX_PLAUSIBLE_C || (!isnan(lastC) && fabs(c - lastC) > MAX_JUMP_C)) {
      if (!sensorFault) Serial.println("sensor fault: fan on for safety");
      sensorFault = true;
    }
    lastC = c;
    if (sensorFault) {
      fanOn = true;
    } else if (c > FAN_ON_C) {
      fanOn = true;
    } else if (c < FAN_OFF_C) {
      fanOn = false;
    }
    digitalWrite(FAN_PIN, fanOn ? HIGH : LOW);
  }

  if (now - lastBlink >= 500) {
    lastBlink = now;
    ledOn = !ledOn;
    digitalWrite(LED_PIN, ledOn ? HIGH : LOW);
  }
}
