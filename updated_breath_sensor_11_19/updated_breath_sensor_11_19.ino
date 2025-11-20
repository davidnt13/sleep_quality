#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <math.h>

// ====== OLED Configuration ======
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ====== Sensor & Sampling Parameters ======
const int SENSOR_PIN = A0;
const unsigned long SAMPLE_DELAY_MS = 10; // Adjust based on expected signal speed (10 Hz)
int buzzerPin = 9;

// ====== Adaptive Filtering & Threshold ======
const int WINDOW_SIZE = 10;          // Larger window for more stable stats
float buffer[WINDOW_SIZE] = {0};
int bufferIndex = 0;

const float ALPHA = 0.3;             // Exponential smoothing factor
float smoothed_val = 0.0;
const float OFFSET = 0.02;           // Additional offset applied above mean
const float THRESHOLD_MIN = 0.05;    // Minimum threshold - 0.05 for both
const float THRESHOLD_MAX = 5.0;     // Safety clamp limits

// ====== Peak Detection State ======
float prev_val = 0.0;
bool allowPeak = false;
unsigned long lastPeakTime = 0;
const unsigned long REFRACTORY_MS = 1500;  // Minimum time between peaks (1.5s typical)
int peakFlag = 0;

const unsigned long peakIdentWindow = 20000; // 20 seconds
#define MAX_PEAKS 50                           // max peaks to store
unsigned long peakTimes[MAX_PEAKS] = {0};     // circular buffer
int peakTimesIndex = 0;

// ====== Hypopnea Detection ======
bool inHypopnea = false;
unsigned long hypopneaStart = 0;
const float HYPO_THRESHOLD = 0.7; // 30% drop
const unsigned long MIN_DURATION_MS = 10000;

// ====== Apnea Detection ======
bool inApnea = false;
unsigned long apneaStart = 0;
unsigned int apneaCount = 0;

// ====== Hypopnea Count ======
unsigned int hypopneaCount = 0;

// ====== Display & Utility ======
unsigned long lastSample = 0;
int peakCount = 0;

// ====== Sleep Timing ======
unsigned long sleepStart = 0;

// ====== Helper Function: Compute mean and stddev ======
void computeStats(const float *data, int size, float &mean, float &stddev) {
  float sum = 0.0, sumSq = 0.0;
  for (int i = 0; i < size; i++) {
    sum += data[i];
    sumSq += data[i] * data[i];
  }
  mean = sum / size;
  float variance = (sumSq / size) - (mean * mean);
  if (variance < 1e-6) variance = 1e-6;
  stddev = sqrt(variance);
}

// ===== Helper: Update one line of OLED without clearing entire screen =====
void updateOLEDLine(int y, const char* label, float value, int decimals = 1) {
  const int lineHeight = 10;                // height of a text line
  display.fillRect(0, y, SCREEN_WIDTH, lineHeight, BLACK); // erase line
  display.setCursor(0, y);
  display.print(label);
  display.println(value, decimals);
}

// Overload for integer values
void updateOLEDLine(int y, const char* label, int value) {
  const int lineHeight = 10;
  display.fillRect(0, y, SCREEN_WIDTH, lineHeight, BLACK);
  display.setCursor(0, y);
  display.print(label);
  display.println(value);
}

// ====== Setup ======
void setup() {
  Serial.begin(9600);

  pinMode(buzzerPin, OUTPUT);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println(F("SSD1306 allocation failed"));
    for (;;);
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);

  sleepStart = millis(); // Track start of session

  Serial.println("Adaptive Peak Detection + OLED started...");
}

// ====== Main Loop ======
void loop() {
  unsigned long now = millis();
  if (now - lastSample < SAMPLE_DELAY_MS) return;
  lastSample = now;

  // --- Sample signal ---
  float raw = analogRead(SENSOR_PIN) * 0.00488;
  float value = -raw;

  // --- Smooth ---
  smoothed_val = ALPHA * value + (1.0 - ALPHA) * smoothed_val;

  // --- Update buffer ---
  buffer[bufferIndex] = smoothed_val;
  bufferIndex = (bufferIndex + 1) % WINDOW_SIZE;

  // --- Compute rolling stats ---
  float mean, stddev;
  computeStats(buffer, WINDOW_SIZE, mean, stddev);

  // --- Threshold ---
  float dynamicThreshold = mean + OFFSET + stddev * 0.45;
  dynamicThreshold = constrain(dynamicThreshold, THRESHOLD_MIN, THRESHOLD_MAX);

  // --- Zero crossing detection ---
  float demeaned = smoothed_val - mean;
  if (prev_val < 0 && demeaned >= 0) allowPeak = true;

  // --- Peak detection ---
  peakFlag = 0;
  if (allowPeak && demeaned > dynamicThreshold) {
    if (now - lastPeakTime > REFRACTORY_MS) {
      lastPeakTime = now;
      peakFlag = 1;
      allowPeak = false;

      // --- Store timestamp in circular array ---
      peakTimes[peakTimesIndex] = now;
      peakTimesIndex = (peakTimesIndex + 1) % MAX_PEAKS;

      peakCount++;  // for longer-term rate calculation
    }
  }

  // --- Count peaks in last 20 seconds ---
  int peaks_in_20 = 0;
  for (int i = 0; i < MAX_PEAKS; i++) {
    if (peakTimes[i] != 0 && now - peakTimes[i] <= peakIdentWindow) {
      peaks_in_20++;
    }
  }

  // --- Breathing rate (BPM) ---
  float breath_rate = (peaks_in_20 * 60) / 20.0;

  // --- Apnea Detection ---
  const unsigned long MIN_APNEA_MS = 10000; // 10s
  if (peakFlag == 0) {
    if (!inApnea) {
      inApnea = true;
      apneaStart = now;
    } else if (now - apneaStart >= MIN_APNEA_MS) {
      apneaCount++;
      inApnea = false;
    }
  } else {
    inApnea = false;
  }

  // --- Hypopnea Detection ---
  if (smoothed_val < mean * HYPO_THRESHOLD) {
    if (!inHypopnea) {
      inHypopnea = true;
      hypopneaStart = now;
    } else if (now - hypopneaStart >= MIN_DURATION_MS) {
      hypopneaCount++;
      inHypopnea = false;
    }
  } else {
    inHypopnea = false;
  }

  // --- AHI calculation ---
  float hours_slept = (now - sleepStart) / 3600000.0; 
  float AHI = hours_slept > 0 ? (apneaCount + hypopneaCount) / hours_slept : 0;

  // --- Serial Output ---
  Serial.print(demeaned, 3); Serial.print("\t");
  Serial.print(peaks_in_20); Serial.print(" ");
  Serial.print(breath_rate); Serial.print(" ");
  Serial.print(apneaCount); Serial.print(" ");
  Serial.print(hypopneaCount); Serial.print(" ");
  Serial.println(AHI, 1);

  // --- OLED display ---
  int y = 0;
  updateOLEDLine(y, "BPM: ", breath_rate); y += 10;
  updateOLEDLine(y, "Apneas: ", apneaCount); y += 10;
  updateOLEDLine(y, "Hypopneas: ", hypopneaCount); y += 10;
  updateOLEDLine(y, "AHI: ", AHI); 
  display.display();

  prev_val = demeaned;
}


