// Toggle a lamp with a button, first draft: reads the button directly.
const int BUTTON_PIN = 2;
const int LAMP_PIN = 13;

bool lampOn = false;
int lastReading = HIGH;

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LAMP_PIN, OUTPUT);
}

void loop() {
  int reading = digitalRead(BUTTON_PIN);
  if (reading == LOW && lastReading == HIGH) {
    lampOn = !lampOn;
    digitalWrite(LAMP_PIN, lampOn ? HIGH : LOW);
  }
  lastReading = reading;
}
