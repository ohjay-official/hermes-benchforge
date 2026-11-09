// Incubator heater: averaged readings, hysteresis, and the heater turns off if the thermistor reads like a
// broken wire.
const int THERM_PIN = A1;
const int HEATER_PIN = 8;      // relay module: LOW = heater on

bool heating = false;
bool fault = false;

void setup() {
  pinMode(HEATER_PIN, OUTPUT);
  digitalWrite(HEATER_PIN, HIGH);
}

int readAverage() {
  long sum = 0;
  for (int i = 0; i < 16; i++) sum += analogRead(THERM_PIN);
  return sum / 16;
}

void loop() {
  int adc = readAverage();
  // An open thermistor pulls the pin to 5 V (about 1023); a short pulls it to 0. Neither is a temperature.
  if (adc > 1000 || adc < 20) {
    fault = true;
  }
  if (fault) {
    heating = false;
  } else {
    float r = 10000.0 * adc / (1023.0 - adc);
    float c = 1.0 / (1.0 / 298.15 + log(r / 10000.0) / 3950.0) - 273.15;
    if (c < 37.0) heating = true;
    else if (c > 38.0) heating = false;
  }
  digitalWrite(HEATER_PIN, heating ? LOW : HIGH);
  delay(100);
}
