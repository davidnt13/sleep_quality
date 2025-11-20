import math
import time
import statistics
import random
import threading
import json, os
import base64
from flask import Flask, render_template, request
from flask_socketio import SocketIO
import glob
from datetime import datetime

# --- Flask / SocketIO setup ---
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- folder for saved screenshots ---
SCREEN_DIR = os.path.join(app.static_folder, "screens")
os.makedirs(SCREEN_DIR, exist_ok=True)

# --- Information for the daily readout ---
current_day = None
day_data = {
    "samples": [],  # breath_rate values
    "peaks": [],    # peaks_in_20 values
    "apnea_events": 0,
    "hypopnea_events": 0,
    "longest_pause": 0.0,
    "breaths_in_20": 0,
    "AHI": 0
}
DATA_DIR = os.path.join(app.static_folder, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ----------------------------------------------------------------------
# Arduino‑like data simulator
# ----------------------------------------------------------------------
def fake_arduino_data():
    """Continuously emit fake breathing data."""
    t = 0.0
    while True:
        try:
            sin_period = 2
            value = math.sin(sin_period*t)
            lower, upper = -1.0, 1.0
            peaks_in_20 = random.randint(3, 15)       # include low values sometimes
            breath_rate = 60 / sin_period
            peak = 1 if value > 0.9 else 0
            num_apnea_events = 1
            num_hypopnea_events = 1
            ahi = 5

            data = {
                "lower": lower,
                "upper": upper,
                "value": value,
                "peaks_in_20": peaks_in_20,
                "breath_rate": breath_rate,
                "apneas": num_apnea_events,
                "hypopneas": num_hypopnea_events,
                "peak": peak,
                "AHI": ahi
            }
            
            # --- collect data for daily summary ---
            today = datetime.now().strftime("%Y-%m-%d")
            global current_day, day_data
            
            # reset if new day
            if current_day != today:
                current_day = today
                day_data = {"samples": [], "peaks": [], "apnea_events": 0, "hypopnea_events": 0, "longest_pause": 0.0}
            
            # store current sample
            day_data["samples"].append(data["breath_rate"])
            day_data["peaks"].append(data["peaks_in_20"])
            
            # detect apnea events (example threshold)
            day_data['apnea_events'] = data["apneas"]
            day_data['hypopnea_events'] = data['hypopneas']
            day_data['breaths_in_20'] = data['peaks_in_20']
            day_data['AHI'] = data['AHI']

            # send live sample to web page
            print("Sending:", data)
            socketio.emit("arduino_data", data)

            t += 0.02
            time.sleep(0.01)  # adjust speed of simulation
        except Exception as e:
            print("Error in simulator:", e)
            time.sleep(1)

# ----------------------------------------------------------------------
# Web routes
# ----------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index2.html')

@app.route('/alerts')
def alerts():
    """Show captured screenshots."""
    imgs = sorted(os.listdir(SCREEN_DIR), reverse=True)
    imgs = [f"screens/{f}" for f in imgs if f.endswith(".png")]
    return render_template('alerts.html', images=imgs)

@app.route('/metrics')
def metrics():
    files = sorted(glob.glob(os.path.join(app.static_folder, 'data', '*.json')))
    data = []
    for f in files:
        try:
            with open(f) as fh:
                record = json.load(fh)
                data.append(record)
        except Exception:
            pass
    # send the whole list for Chart.js and calendar
    print("Loaded metrics:", data)
    return render_template('metrics.html', records=data)

@app.route("/upload_snapshot", methods=["POST"])
def upload_snapshot():
    """Receive base64 chart screenshot from client."""
    try:
        img_data = request.json.get("image")
        if not img_data:
            return {"status": "no image"}, 400

        # remove header "data:image/png;base64,"
        header, encoded = img_data.split(",", 1)
        file_bytes = base64.b64decode(encoded)
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(SCREEN_DIR, f"{ts}.png")
        with open(path, "wb") as f:
            f.write(file_bytes)

        print("Saved snapshot:", path)
        return {"status": "ok", "file": f"screens/{ts}.png"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}, 500

@app.route('/learn')
def learn():
    return render_template('learn.html')

def summarize_day():
    """Compute averages and write metrics JSON for the current day."""
    if not day_data["samples"]:
        return
    avg_rate = statistics.mean(day_data["samples"])
    min_rate = min(day_data["samples"])
    max_rate = max(day_data["samples"])
    avg_peaks = statistics.mean(day_data["peaks"])
    metrics = {
        "date": current_day,
        "avg_breath_rate": round(avg_rate, 2),
        "min_breath_rate": round(min_rate, 2),
        "max_breath_rate": round(max_rate, 2),
        "avg_peaks_in_20": round(avg_peaks, 2),
        "apnea_events": day_data["apnea_events"],
        "hypopnea_events": day_data["hypopnea_events"],
        "AHI": day_data["AHI"],
        "longest_pause": day_data.get("longest_pause", 0.0)
    }
    path = os.path.join(DATA_DIR, f"{current_day}.json")
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2)
    print("Saved daily metrics:", path)

def hourly_saver():
    """Background thread to save metrics every hour."""
    while True:
        try:
            summarize_day()
        except Exception as e:
            print("Error writing daily metrics:", e)
        time.sleep(60 * 60)  # wait one hour

# ----------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------
if __name__ == "__main__":
    threading.Thread(target=fake_arduino_data, daemon=True).start()
    threading.Thread(target=hourly_saver, daemon=True).start()
    print("Simulated Arduino thread started.")
    socketio.run(app, host="0.0.0.0", port=5001)