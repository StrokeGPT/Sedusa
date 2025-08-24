import os, sys, time, threading, webbrowser, logging
from pathlib import Path

# ---- logging to file in user profile ----
log_dir = Path(os.getenv("APPDATA", str(Path.home()))) / "SedusaLogs"
log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=log_dir / "app.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ---- make relative paths work in a bundled exe ----
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    base_dir = Path(sys._MEIPASS)
else:
    base_dir = Path(__file__).parent.resolve()
os.chdir(base_dir)

# ---- import your Flask app ----
try:
    from app import app  # if your app is a module-level Flask() named 'app'
except Exception:
    # fallback if you expose a factory
    from app import create_app
    app = create_app()

PORT = 5423  # fixed local port for simplicity

def _open_browser_when_ready():
    url = f"http://127.0.0.1:{PORT}"
    # try /status first if it exists, else just open after a short delay
    try_status = True
    try:
        import requests  # already in your requirements
    except Exception:
        try_status = False

    if try_status:
        for _ in range(50):
            try:
                requests.get(f"{url}/status", timeout=0.4)
                break
            except Exception:
                time.sleep(0.2)
    else:
        time.sleep(1.0)
    try:
        webbrowser.open(url)
    except Exception as e:
        logging.error("Failed to open browser: %r", e)

if __name__ == "__main__":
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    # bind only to localhost so no firewall prompts
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)
