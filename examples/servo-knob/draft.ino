// Servo follows a knob, first draft.
#include <Servo.h>

Servo arm;

void setup() {
  arm.attach(9);
}

void loop() {
  int reading = analogRead(A0);
  arm.write(map(reading, 0, 1023, 0, 180));
  delay(15);
}
