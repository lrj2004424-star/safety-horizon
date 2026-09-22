/*
  Beginner buzzer identification test for Arduino UNO.

  Wiring:
    D6  -> buzzer long leg / +
    GND -> buzzer short leg / -

  The built-in LED mirrors each test so the user can identify the phase.
*/

const uint8_t BUZZER_PIN = 6;
const uint8_t LED_PIN = LED_BUILTIN;

void quiet(unsigned long durationMs) {
  noTone(BUZZER_PIN);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(LED_PIN, LOW);
  delay(durationMs);
}

void passiveToneTest() {
  // Phase A: two short 2000 Hz tones. A passive buzzer should sound clearly.
  for (uint8_t count = 0; count < 2; count++) {
    digitalWrite(LED_PIN, HIGH);
    tone(BUZZER_PIN, 2000);
    delay(200);
    quiet(250);
  }
}

void activeBuzzerTest() {
  // Phase B: one long DC pulse. An active buzzer should sound clearly.
  noTone(BUZZER_PIN);
  digitalWrite(LED_PIN, HIGH);
  digitalWrite(BUZZER_PIN, HIGH);
  delay(700);
  quiet(0);
}

void setup() {
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  quiet(2000);
}

void loop() {
  passiveToneTest();
  quiet(1500);
  activeBuzzerTest();
  quiet(3000);
}
