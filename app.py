# app.py
import json, os, threading, time, random
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path

from device.handy import HandyClient
from haptics.runner import StoryRunner
from haptics.tokens import TokenCompiler
from haptics.motifs import MotifLibrary

APP_ROOT = Path(__file__).parent.resolve()
app = Flask(__name__, static_folder=str(APP_ROOT / "web"), static_url_path="")

# ---------- Config ----------
def load_config():
    cfg_path = APP_ROOT / "config.json"
    if not cfg_path.exists():
        # api_key is no longer stored in config
        return {
            "server": {"host": "0.0.0.0", "port": 7860},
            "device": {"mode": "simulate", "log_device": True, "speed_calibration_factor": 2.8},
            "caps": {
                "depth_min_mm": 15, "depth_max_mm": 110,
                "speed_min_hz": 0.4, "speed_max_hz": 3.2,
            },
        }
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()

# ---------- Content Loading ----------
def load_narrative_content():
    """Loads the main dialogue file at startup."""
    content_path = APP_ROOT / "content" / "lines_medusa.json"
    try:
        with open(content_path, "r", encoding="utf-8") as f:
            print(f"[Content] Successfully loaded narrative from {content_path}")
            return json.load(f)
    except FileNotFoundError:
        print(f"FATAL: Could not load narrative file from {content_path}")
        return {}

NARRATIVE_CONTENT = load_narrative_content()

# ---------- Haptics + Story Engine ----------
motif_library = MotifLibrary([
    APP_ROOT / "data" / "motif_bank.json",
    APP_ROOT / "data" / "snake_patterns.json"
])
compiler = TokenCompiler(motif_library)

runner_lock = threading.Lock()
runner: StoryRunner | None = None

def build_runner(params, api_key: str | None):
    global runner
    with runner_lock:
        if runner:
            runner.last_line = ""
            if runner.is_alive():
                runner.stop()
                runner.join(timeout=1.0)

        # Create the device object first, using the key provided by the client.
        device = HandyClient(
            mode=CONFIG["device"]["mode"],
            api_key=api_key,
            log_device=CONFIG["device"].get("log_device", True),
            max_speed_hz=CONFIG["caps"]["speed_max_hz"],
            speed_calibration_factor=CONFIG["device"].get("speed_calibration_factor", 2.8)
        )

        # Then create the runner, passing the device object to it.
        runner = StoryRunner(
            device=device,
            compiler=compiler,
            narrative_templates=NARRATIVE_CONTENT,
            depth_min_mm=params["depth_min"],
            depth_max_mm=params["depth_max"],
            speed_min_hz=params["speed_min"],
            speed_max_hz=params["speed_max"],
            length_min=params["length_min"],
            name=params.get("name") or "",
            seed=params.get("seed")
        )
        runner.start()
        return runner

# ---------- Routes ----------
@app.route("/")
def root():
    return app.send_static_file("index.html")

@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(str(APP_ROOT / "web" / "assets"), filename)

@app.post("/start")
def start_story():
    data = request.get_json(force=True, silent=True) or {}
    api_key = data.get("api_key")
    if not api_key:
        return jsonify({"ok": False, "error": "API key is required"}), 400

    try:
        depth_min = float(data.get("depth_min"))
        depth_max = float(data.get("depth_max"))
        speed_min = float(data.get("speed_min"))
        speed_max = float(data.get("speed_max"))
        length_min = int(data.get("length_min", 10))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Missing or invalid parameters"}), 400
    
    caps = CONFIG["caps"]
    depth_min = max(caps["depth_min_mm"], min(depth_min, caps["depth_max_mm"]))
    depth_max = max(depth_min, min(depth_max, caps["depth_max_mm"]))
    speed_min = max(caps["speed_min_hz"], min(speed_min, caps["speed_max_hz"]))
    speed_max = max(speed_min, min(speed_max, caps["speed_max_hz"]))

    params = {
        "depth_min": depth_min,
        "depth_max": depth_max,
        "speed_min": speed_min,
        "speed_max": speed_max,
        "length_min": length_min,
        "name": (data.get("name") or "").strip()[:24],
        "seed": data.get("seed")
    }
    
    r = build_runner(params, api_key)
    return jsonify({"ok": True, "state": r.state_snapshot()})

@app.post("/pause")
def pause_story():
    with runner_lock:
        if runner: runner.pause()
    return jsonify({"ok": True})

@app.post("/resume")
def resume_story():
    with runner_lock:
        if runner: runner.resume()
    return jsonify({"ok": True})

@app.post("/stop")
def stop_story():
    with runner_lock:
        if runner:
            runner.stop()
            runner.join(timeout=1.0)
    return jsonify({"ok": True})

@app.get("/status")
def status():
    with runner_lock:
        if not runner:
            return jsonify({"state": "idle"})
        return jsonify(runner.state_snapshot())

if __name__ == "__main__":
    app.run(host=CONFIG["server"]["host"], port=CONFIG["server"]["port"], debug=False, threaded=True)