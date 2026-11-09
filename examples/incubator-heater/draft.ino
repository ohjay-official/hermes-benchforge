// Incubator heater, first draft.
const int THERM_PIN = A1;
const int HEATER_PIN = 8;      // relay module: LOW = heater on

bool heating = false;

float readCelsius() {
  int adc = analogRead(THERM_PIN);
  float r = 10000.0 * adc / (1023.0 - adc);
  float kelvin = 1.0 / (1.0 / 298.15 + log(r / 10000.0) / 3950.0);
  return kelvin - 273.15;
}

void setup() {
  pinMode(HEATER_PIN, OUTPUT);
  digitalWrite(HEATER_PIN, HIGH);
}

void loop() {
  float c = readCelsius();
  if (c < 37.0) heating = true;
  else if (c > 38.0) heating = false;
  digitalWrite(HEATER_PIN, heating ? LOW : HIGH);
  delay(100);
}
