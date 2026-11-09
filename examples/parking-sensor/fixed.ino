// Parking sensor: beeps when the car is close, stays quiet when it's far, and treats
// a sensor that stops answering as a reason to beep, not to go quiet.
const int TRIG_PIN = 9;
const int ECHO_PIN = 10;
const int BUZZER_PIN = 8;
const int MISSES_BEFORE_ALARM = 3;

bool beeping = false;
int misses = 0;

void setup() {
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
}

long readCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long us = pulseIn(ECHO_PIN, HIGH, 30000);
  return us == 0 ? -1 : us / 58;     // -1: no echo at all
}

void loop() {
  long cm = readCm();
  bool wasBeeping = beeping;
  if (cm < 0) {
    if (++misses >= MISSES_BEFORE_ALARM) beeping = true;
  } else {
    misses = 0;
    if (cm < 30) beeping = true;
    else if (cm > 40) beeping = false;
  }
  if (beeping && !wasBeeping) tone(BUZZER_PIN, 1000);
  if (!beeping && wasBeeping) noTone(BUZZER_PIN);
  delay(60);
}
