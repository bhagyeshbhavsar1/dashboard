# app.py
from flask import Flask, Response, render_template
import serial, threading, json, time, os, requests
from datetime import datetime, timezone

# ---------- CONFIG ----------
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")  # Change if needed
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))

# Supabase settings
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ndmoabrjopfbqemiancq.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5kbW9hYnJqb3BmYnFlbWlhbmNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQyNzEwODYsImV4cCI6MjA3OTg0NzA4Nn0.opR1Q4LCwZitzgovaOtTTE-qd-2wKxqwAcgr4AAOa-Y")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "sensor_data")
SEND_TO_SUPABASE = True   # Set False to disable sending

# ---------------------------
app = Flask(__name__)

# Latest reading
latest = {
    "timestamp_ms": None,
    "temperature": None,
    "turbidity": None,
    "ph": None,
    "tds": None
}
lock = threading.Lock()


# -----------------------------------------------------
# PUSH DATA TO SUPABASE
# -----------------------------------------------------
def push_to_supabase(ts_ms, temperature, turbidity, ph, tds):
    if not SEND_TO_SUPABASE:
        return

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    iso_ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()

    payload = {
        "timestamp": iso_ts,
        "device_id": "arduino_uno",
        "temperature": round(temperature, 2),
        "turbidity": round(turbidity, 2),
        "ph_value": round(ph, 2),
        "tds_value": round(tds, 2)
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=6)
        if not (200 <= resp.status_code < 300):
            print("Supabase insert failed:", resp.status_code, resp.text)
        else:
            print(f"Supabase insert OK: Temp={temperature}, pH={ph}, TDS={tds}")
    except Exception as e:
        print("Supabase request error:", e)


# -----------------------------------------------------
# SERIAL READER
# -----------------------------------------------------
def open_serial_with_retry():
    ser = None
    while ser is None:
        try:
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
            print("Opened serial:", SERIAL_PORT)
        except Exception as e:
            print(f"Failed to open {SERIAL_PORT}: {e}")
            time.sleep(2)
    return ser


def serial_reader():
    ser = open_serial_with_retry()
    ser.reset_input_buffer()

    while True:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            # Parse Arduino JSON
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                print("Non-JSON line:", line)
                continue

            ts_ms = int(time.time() * 1000)

            # Correct keys from Arduino JSON
            temperature = float(data.get("temperature_C", 0.0))
            turbidity   = float(data.get("turbidity_NTU", 0.0))
            ph          = float(data.get("pH", 0.0))
            tds         = float(data.get("tds_ppm", 0.0))

            with lock:
                latest.update({
                    "timestamp_ms": ts_ms,
                    "temperature": round(temperature, 2),
                    "turbidity": round(turbidity, 2),
                    "ph": round(ph, 2),
                    "tds": round(tds, 2)
                })

            # Push async to Supabase
            threading.Thread(
                target=push_to_supabase,
                args=(ts_ms, temperature, turbidity, ph, tds),
                daemon=True
            ).start()

            print("Reading:", latest)

        except Exception as e:
            print("Serial read loop error:", e)
            time.sleep(1)


# -----------------------------------------------------
# STREAM ENDPOINT
# -----------------------------------------------------
@app.route("/stream")
def stream():
    def event_stream():
        while True:
            with lock:
                data = dict(latest)
            if data["timestamp_ms"] is None:
                yield "retry: 2000\n\n"
            else:
                data["timestamp"] = datetime.now(timezone.utc).isoformat()
                yield f"data: {json.dumps(data)}\n\n"
            time.sleep(1)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/")
def index():
    return render_template("index.html")


# -----------------------------------------------------
if __name__ == "__main__":
    threading.Thread(target=serial_reader, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, threaded=True)
