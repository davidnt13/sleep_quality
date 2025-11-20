from flask import Flask, render_template
from flask_socketio import SocketIO
import serial
import threading
import time

# --- setup your Arduino serial port ---
# Replace with your own port, e.g., "COM3" on Windows or "/dev/ttyUSB0" on Linux
arduino_port = "/dev/tty.usbmodem2101"
baud_rate = 9600

# Create Flask app and SocketIO
app = Flask(__name__)
socketio = SocketIO(app)

def read_from_serial():
    ser = serial.Serial(arduino_port, baud_rate, timeout=1)
    while True:
        try:
            line = ser.readline().decode("utf-8").strip()
            if not line:
                continue
            
            parts = line.split()
            if len(parts) >= 4:
                data = {
                    "lower": float(parts[0]),
                    "upper": float(parts[1]),
                    "value": float(parts[2]),
                    "peaks_in_20": 10,#float(parts[3]),
                    "breath_rate": float(parts[4]),
                    "peak": int(parts[5])
                }
                print(data["peaks_in_20"])
                socketio.emit("arduino_data", data)
        except Exception as e:
            print("Error reading serial:", e)
            time.sleep(1)

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    # Start serial thread
    thread = threading.Thread(target=read_from_serial, daemon=True)
    thread.start()

    # Start web server
    socketio.run(app, host="0.0.0.0", port=5001)
