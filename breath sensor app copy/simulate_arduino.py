# Standard library imports
import base64
import glob
import json
import math
import os
import random
import statistics
import threading
import time
import serial
from datetime import datetime

# Third-party imports
from flask import Flask, render_template, request
from flask_socketio import SocketIO

# --- Flask / SocketIO setup ---
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Arduino Reading Setup ---
arduino_port = "/dev/tty.usbmodem2101"
baud_rate = 9600

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
    "AHI": 0,
    "total_sleep_secs": 0.0,
}
DATA_DIR = os.path.join(app.static_folder, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Sleep timer information
# Sleep session control flags
sleep_active = False
sleep_paused = False
sleep_ended = False

sleep_start_time = None 
sleep_accumulated = 0.0 

def get_sleep_accumulated():
    """Return total sleep in seconds including current active session."""
    if sleep_paused or sleep_ended or sleep_start_time is None:
        return sleep_accumulated
    else:
        return sleep_accumulated + (time.time() - sleep_start_time)

# ----------------------------------------------------------------------
# Arduino‑like data simulator
# ----------------------------------------------------------------------
def fake_arduino_data():
    """Continuously emit fake breathing data."""
    t = 0.0
    global sleep_active, sleep_paused, sleep_ended
    global current_day, day_data

    while True:
        try:
            # --- SLEEP STATE LOGIC -----------------------------------------
            if not sleep_active or sleep_paused or sleep_ended:
                if sleep_ended:
                    print("[DEBUG] Sleep ended, simulator paused")
                time.sleep(0.1)
                continue
            # ----------------------------------------------------------------

            sin_period = 2
            value = math.sin(sin_period*t)
            lower, upper = -1.0, 1.0
            peaks_in_20 = random.randint(3, 15)
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
                "AHI": ahi,
                "total_sleep_secs": get_sleep_accumulated()
            }

            # --- collect data for daily summary ---
            day_data["samples"].append(data["breath_rate"])
            day_data["peaks"].append(data["peaks_in_20"])
            day_data['apnea_events'] = data["apneas"]
            day_data['hypopnea_events'] = data['hypopneas']
            day_data['breaths_in_20'] = data['peaks_in_20']
            day_data['AHI'] = data['AHI']
            day_data['total_sleep_secs'] = data['total_sleep_secs']

            # send live sample to web page
            socketio.emit("arduino_data", data)

            t += 0.02
            time.sleep(0.01)
        except Exception as e:
            print("Error in simulator:", e)
            time.sleep(1)

# ----------------------------------------------------------------------
# Actual Arduino
# ----------------------------------------------------------------------            
def read_from_serial():
    """Continuously read Arduino data, track sleep state, and update daily summary."""
    ser = serial.Serial(arduino_port, baud_rate, timeout=1)
    global sleep_active, sleep_paused, sleep_ended
    global current_day, day_data, sleep_accumulated, sleep_start_time

    while True:
        try:
            # --- SLEEP STATE LOGIC -----------------------------------------
            if not sleep_active or sleep_paused or sleep_ended:
                if sleep_ended:
                    print("[DEBUG] Sleep ended, serial reader paused")
                time.sleep(0.1)
                continue
            # ----------------------------------------------------------------

            line = ser.readline().decode("utf-8").strip()
            if not line:
                continue

            # Split tab for demeaned value
            parts = line.split("\t")
            if len(parts) != 2:
                continue  # malformed line

            demeaned_str, rest = parts
            rest_parts = rest.strip().split()
            if len(rest_parts) < 5:
                continue  # not enough fields

            # Map Arduino values to simulator data dictionary
            value = float(demeaned_str)
            data = {
                "lower": -0.5,
                "upper": 0.5,
                "value": value,
                "peaks_in_20": int(rest_parts[0]),
                "breath_rate": float(rest_parts[1]),
                "apneas": int(rest_parts[2]),
                "hypopneas": int(rest_parts[3]),
                "peak": 1 if value > 0.9 else 0,
                "AHI": float(rest_parts[4]),
                "total_sleep_secs": get_sleep_accumulated()
            }

            # --- update daily summary ---
            day_data["samples"].append(data["breath_rate"])
            day_data["peaks"].append(data["peaks_in_20"])
            day_data['apnea_events'] = data["apneas"]
            day_data['hypopnea_events'] = data['hypopneas']
            day_data['breaths_in_20'] = data['peaks_in_20']
            day_data['AHI'] = data['AHI']
            day_data['total_sleep_secs'] = data['total_sleep_secs']

            # --- send live data to client ---
            socketio.emit("arduino_data", data)

        except Exception as e:
            print("Error reading serial:", e)
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

@app.route("/start_sleep")
def start_sleep():
    global sleep_active, sleep_paused, sleep_ended
    global sleep_start_time, sleep_accumulated
    global current_day
    
    current_day = datetime.now().strftime("%Y-%m-%d")

    sleep_active = True
    sleep_paused = False
    sleep_ended = False

    sleep_start_time = time.time()
    sleep_accumulated = 0.0  # reset for new session

    return {"status": "sleep started"}

@app.route("/pause_sleep")
def pause_sleep():
    global sleep_paused, sleep_start_time, sleep_accumulated

    if not sleep_paused and sleep_start_time is not None:
        sleep_accumulated += time.time() - sleep_start_time
        sleep_start_time = None  # reset to avoid double-counting

    sleep_paused = True
    return {"status": "sleep paused"}


@app.route("/resume_sleep")
def resume_sleep():
    global sleep_paused, sleep_start_time

    sleep_paused = False
    sleep_start_time = time.time()  # restart timing from here

    return {"status": "sleep resumed"}

@app.route("/end_sleep")
def end_sleep():
    global sleep_active, sleep_paused, sleep_ended
    global sleep_accumulated, sleep_start_time
    global day_data, current_day

    # finalize timer
    if sleep_start_time is not None and not sleep_paused:
        sleep_accumulated += time.time() - sleep_start_time
        sleep_start_time = None

    total_sleep_hours = sleep_accumulated / 3600
    day_data['total_sleep_secs'] = sleep_accumulated
    summarize_day()

    # reset daily data
    current_day = datetime.now().strftime("%Y-%m-%d")
    day_data = {
        "samples": [],
        "peaks": [],
        "apnea_events": 0,
        "hypopnea_events": 0,
        "longest_pause": 0.0,
        "breaths_in_20": 0,
        "AHI": 0,
        "total_sleep_secs": 0.0
    }

    sleep_active = False
    sleep_paused = False
    sleep_ended = True

    return {
        "status": "sleep ended",
        "total_sleep_seconds": sleep_accumulated,
        "total_sleep_hours": total_sleep_hours
    }

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
        "longest_pause": day_data.get("longest_pause", 0.0),
        "total_sleep_secs": day_data["total_sleep_secs"]
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