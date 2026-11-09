// Parking sensor, first draft.
const int TRIG_PIN = 9;
const int ECHO_PIN = 10;
const int BUZZER_PIN = 8;

bool beeping = false;

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
  return us / 58;
}

void loop() {
  long cm = readCm();
  if (cm > 0 && cm < 30) beeping = true;
  else if (cm == 0 || cm > 40) beeping = false;
  if (beeping) tone(BUZZER_PIN, 1000);
  else noTone(BUZZER_PIN);
  delay(60);
}
