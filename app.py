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
        return {
            "server": {"host": "0.0.0.0", "port": 7860},
            "device": {"mode": "simulate", "api_key": "", "log_device": True},
            "caps": {
                "depth_min_mm": 15, "depth_max_mm": 110,
                "speed_min_hz": 0.4, "speed_max_hz": 3.2,
            },
        }
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()

def save_config(config_data):
    cfg_path = APP_ROOT / "config.json"
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
        print(f"[Config] Updated config.json successfully.")
    except IOError as e:
        print(f"[Config] ERROR: Could not save config.json: {e}")

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

        # Create the device object first.
        device = HandyClient(
            mode=CONFIG["device"]["mode"],
            api_key=api_key or CONFIG["device"].get("api_key",""),
            log_device=CONFIG["device"].get("log_device", True)
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

@app.post("/save_api_key")
def save_api_key():
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("api_key", "").strip()

    if not key:
        return jsonify({"ok": False, "error": "API key not provided"}), 400
    
    # Update CONFIG and save it
    CONFIG["device"]["api_key"] = key
    save_config(CONFIG)

    return jsonify({"ok": True, "message": "API key saved successfully"})

@app.get("/check_api_key")
def check_api_key():
    # Return whether an API key is present in the server's config
    return jsonify({"has_api_key": bool(CONFIG["device"].get("api_key"))})


@app.post("/start")
def start_story():
    data = request.get_json(force=True, silent=True) or {}
    try:
        depth_min = float(data.get("depth_min"))
        depth_max = float(data.get("depth_max"))
        speed_min = float(data.get("speed_min"))
        speed_max = float(data.get("speed_max"))
    except Exception:
        return jsonify({"ok": False, "error": "Missing or invalid parameters"}), 400
    
    length_min = 10

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
    
    # Use the API key directly from the server's CONFIG for building the runner
    r = build_runner(params, CONFIG["device"].get("api_key"))
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