// Toggle a lamp with a button, debounced: a new button state counts only after it has held for 25 ms.
const int BUTTON_PIN = 2;
const int LAMP_PIN = 13;
const unsigned long DEBOUNCE_MS = 25;

bool lampOn = false;
int stableState = HIGH;
int lastReading = HIGH;
unsigned long lastChange = 0;

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LAMP_PIN, OUTPUT);
}

void loop() {
  int reading = digitalRead(BUTTON_PIN);
  unsigned long now = millis();
  if (reading != lastReading) {
    lastChange = now;
    lastReading = reading;
  }
  if (now - lastChange >= DEBOUNCE_MS && reading != stableState) {
    stableState = reading;
    if (stableState == LOW) {
      lampOn = !lampOn;
      digitalWrite(LAMP_PIN, lampOn ? HIGH : LOW);
    }
  }
}
