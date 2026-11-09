// Greenhouse fan, first draft: the kind of sketch a quick answer gives you.
const int TEMP_PIN = A0;
const int FAN_PIN = 7;
const int LED_PIN = 13;

unsigned long nextBlink = 0;
bool ledOn = false;

void setup() {
  pinMode(FAN_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  Serial.begin(9600);
  Serial.println("greenhouse fan ready");
}

void loop() {
  float celsius = analogRead(TEMP_PIN) * 500.0 / 1024.0;
  if (celsius > 30.0) {
    digitalWrite(FAN_PIN, HIGH);
  } else if (celsius < 27.0) {
    digitalWrite(FAN_PIN, LOW);
  }

  if (millis() >= nextBlink) {
    ledOn = !ledOn;
    digitalWrite(LED_PIN, ledOn);
    nextBlink = millis() + 500;
  }
}
