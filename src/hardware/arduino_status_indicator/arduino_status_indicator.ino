/*
  Output-only fatigue advisory indicator for cutting-safety-mvp.

  The computer sends:
    FATIGUE <state_code> <risk_score> <buzzer_level>

  Buzzer levels:
    0 silent, 1 system/visibility notice, 2 fatigue trend, 3 high risk.

  This sketch must never be connected to the cutting press, a safety relay,
  an emergency-stop circuit, or a mains/kit relay module.
*/

const uint8_t PIN_BUZZER = 6;
const uint8_t PIN_RED = 9;
const uint8_t PIN_YELLOW = 10;
const uint8_t PIN_GREEN = 11;

const bool LED_ACTIVE_HIGH = true;       // Verify the actual light module.
const bool BUZZER_IS_PASSIVE = false;    // Verified: this two-pin buzzer responds to DC HIGH.
const bool BUZZER_ACTIVE_HIGH = true;    // Used only by an active buzzer.

// 0 ACTIVE, 1 OBSERVING, 2 UNCERTAIN, 3 FATIGUE_TREND, 4 HIGH_RISK, 5 FAULT.
uint8_t currentStateCode = 2;
uint8_t currentRiskScore = 0;
uint8_t currentBuzzerLevel = 0;
String inputLine = "";
unsigned long lastMessageMs = 0;
unsigned long reviewedPatternStartedMs = 0;
const unsigned long SERIAL_WATCHDOG_MS = 1500;

void writeLed(uint8_t pin, bool on) {
  digitalWrite(pin, (on == LED_ACTIVE_HIGH) ? HIGH : LOW);
}

void allLightsOff() {
  writeLed(PIN_RED, false);
  writeLed(PIN_YELLOW, false);
  writeLed(PIN_GREEN, false);
}

void writeBuzzer(bool on, uint16_t frequencyHz) {
  if (BUZZER_IS_PASSIVE) {
    if (on) {
      tone(PIN_BUZZER, frequencyHz);
    } else {
      noTone(PIN_BUZZER);
    }
  } else {
    digitalWrite(
      PIN_BUZZER,
      (on == BUZZER_ACTIVE_HIGH) ? HIGH : LOW
    );
  }
}

void setFault() {
  currentStateCode = 5;
  currentRiskScore = 0;
  currentBuzzerLevel = 1;
}

void setSilent() {
  currentStateCode = 2;       // UNCERTAIN, but intentionally silent.
  currentRiskScore = 0;
  currentBuzzerLevel = 0;
  writeBuzzer(false, 0);
}

void setup() {
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_RED, OUTPUT);
  pinMode(PIN_YELLOW, OUTPUT);
  pinMode(PIN_GREEN, OUTPUT);
  allLightsOff();
  writeBuzzer(false, 0);
  Serial.begin(115200);
  inputLine.reserve(48);
}

bool parseUnsignedField(const String &text, int start, int end, int &value) {
  if (start < 0 || end <= start) return false;
  String field = text.substring(start, end);
  for (unsigned int i = 0; i < field.length(); i++) {
    if (!isDigit(field.charAt(i))) return false;
  }
  value = field.toInt();
  return true;
}

bool parseFatigueCommand(const String &line) {
  // Expected: FATIGUE <state_code> <risk_score> <buzzer_level>
  int split1 = line.indexOf(' ', 8);
  int split2 = split1 < 0 ? -1 : line.indexOf(' ', split1 + 1);
  int stateCode = -1;
  int riskScore = -1;
  int buzzerLevel = -1;
  if (!parseUnsignedField(line, 8, split1, stateCode) ||
      !parseUnsignedField(line, split1 + 1, split2, riskScore) ||
      !parseUnsignedField(line, split2 + 1, line.length(), buzzerLevel)) {
    return false;
  }
  if (stateCode > 4 || riskScore > 100 || buzzerLevel > 3) return false;
  uint8_t previousBuzzerLevel = currentBuzzerLevel;
  currentStateCode = (uint8_t)stateCode;
  currentRiskScore = (uint8_t)riskScore;
  currentBuzzerLevel = (uint8_t)buzzerLevel;
  if (currentBuzzerLevel == 3 && previousBuzzerLevel != 3) {
    reviewedPatternStartedMs = millis();
  }
  return true;
}

bool parseLegacyState(const String &state) {
  if (state == "NORMAL") {
    currentStateCode = 0;
    currentBuzzerLevel = 0;
  } else if (state == "ATTENTION") {
    currentStateCode = 1;
    currentBuzzerLevel = 0;
  } else if (state == "WARNING") {
    currentStateCode = 3;
    currentBuzzerLevel = 2;
  } else if (state == "DANGER") {
    currentStateCode = 4;
    currentBuzzerLevel = 3;
  } else if (state == "UNCERTAIN") {
    currentStateCode = 2;
    currentBuzzerLevel = 0;
  } else if (state == "FAULT") {
    setFault();
  } else {
    return false;
  }
  currentRiskScore = 0;
  return true;
}

void readCommands() {
  while (Serial.available() > 0) {
    char value = (char)Serial.read();
    if (value == '\n') {
      inputLine.trim();
      bool accepted = false;
      if (inputLine.startsWith("FATIGUE ")) {
        accepted = parseFatigueCommand(inputLine);
      } else if (inputLine.startsWith("STATE ")) {
        accepted = parseLegacyState(inputLine.substring(6));
      }
      if (accepted) {
        lastMessageMs = millis();
      } else {
        setFault();
      }
      inputLine = "";
    } else if (inputLine.length() < 47) {
      inputLine += value;
    } else {
      inputLine = "";
      setFault();
    }
  }
}

void renderLights(unsigned long nowMs) {
  allLightsOff();
  if (currentStateCode == 0) {             // ACTIVE
    writeLed(PIN_GREEN, true);
  } else if (currentStateCode == 1) {      // OBSERVING
    writeLed(PIN_YELLOW, true);
  } else if (currentStateCode == 2) {      // UNCERTAIN
    bool phase = (nowMs / 350) % 2 == 0;
    writeLed(PIN_RED, phase);
    writeLed(PIN_YELLOW, !phase);
  } else if (currentStateCode == 3) {      // FATIGUE_TREND
    writeLed(PIN_RED, true);
    writeLed(PIN_YELLOW, true);
  } else if (currentStateCode == 4) {      // HIGH_RISK
    writeLed(PIN_RED, true);
  } else {                                 // FAULT
    bool phase = (nowMs / 180) % 2 == 0;
    writeLed(PIN_RED, phase);
    writeLed(PIN_YELLOW, phase);
  }
}

void renderBuzzer(unsigned long nowMs) {
  bool on = false;
  uint16_t frequencyHz = 1600;
  if (currentBuzzerLevel == 1) {
    // Two short notice chirps every four seconds: visibility/system issue.
    unsigned long phase = nowMs % 4000;
    on = phase < 100 || (phase >= 250 && phase < 350);
    frequencyHz = 1600;
  } else if (currentBuzzerLevel == 2) {
    // One medium beep every 1.5 seconds: sustained fatigue trend.
    on = nowMs % 1500 < 250;
    frequencyHz = 1900;
  } else if (currentBuzzerLevel == 3) {
    // Safety-officer level 3--5: exactly two short beeps and one long beep.
    // The bridge starts level 3 only once per reviewed decision, then keeps
    // the red risk light on with buzzer level 0 so the sound cannot repeat.
    unsigned long phase = nowMs - reviewedPatternStartedMs;
    on = phase < 120 || (phase >= 220 && phase < 340) ||
         (phase >= 500 && phase < 1100);
    frequencyHz = 2300;
  }
  writeBuzzer(on, frequencyHz);
}

void loop() {
  readCommands();
  unsigned long nowMs = millis();
  if (nowMs - lastMessageMs > SERIAL_WATCHDOG_MS) {
    // Launcher closed / status stale: stop sound rather than repeating a
    // fault pattern after the user has intentionally exited the program.
    setSilent();
  }
  renderLights(nowMs);
  renderBuzzer(nowMs);
}
