// Servo follows a knob, smoothed, and parks at 90 degrees if the knob's readings stop making sense.
#include <Servo.h>

Servo arm;
const int PARK_ANGLE = 90;
const int MAX_STEP = 120;        // a hand can't move the knob this far in 15 ms

int lastReading = -1;
int wild = 0;
bool parked = false;

void setup() {
  arm.attach(9);
  arm.write(PARK_ANGLE);
}

int readKnob() {
  long sum = 0;
  for (int i = 0; i < 4; i++) sum += analogRead(A0);
  return sum / 4;
}

void loop() {
  int reading = readKnob();
  if (lastReading >= 0 && abs(reading - lastReading) > MAX_STEP) {
    wild += 2;
  } else if (wild > 0) {
    wild--;
  }
  lastReading = reading;
  if (wild >= 4) parked = true;
  if (parked) {
    arm.write(PARK_ANGLE);
  } else {
    arm.write(map(reading, 0, 1023, 0, 180));
  }
  delay(15);
}
