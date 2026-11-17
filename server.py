import os
import sys
import json
import time
import shutil
import zipfile
import threading
import subprocess
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

ADMIN_KEY = os.environ.get("ADMIN_KEY", "cyberdeep2026")
BASE_DIR = os.path.abspath("hosted_bots")
STATE_FILE = os.path.abspath("bots_state.json")

os.makedirs(BASE_DIR, exist_ok=True)
RUNNING_PROCS = {}

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def check_auth():
    return request.headers.get("X-API-Key") == ADMIN_KEY

def find_entry_path(bot_dir, entry_file):
    direct = os.path.join(bot_dir, entry_file)
    if os.path.exists(direct):
        return bot_dir, entry_file
    for root, dirs, files in os.walk(bot_dir):
        if entry_file in files:
            return root, entry_file
    return None, None

def start_bot_process(bot_name):
    state = load_state()
    if bot_name not in state: return False, "Bot not found"
    if bot_name in RUNNING_PROCS and RUNNING_PROCS[bot_name].poll() is None:
        return True, "Already running"

    bot_info = state[bot_name]
    bot_dir = os.path.join(BASE_DIR, bot_name)
    work_dir, entry_file = find_entry_path(bot_dir, bot_info["entry"])
    if not work_dir: return False, "Entry file not found"

    log_path = os.path.join(bot_dir, "bot.log")
    log_file = open(log_path, "a", buffering=1, encoding="utf-8", errors="ignore")
    
    proc = subprocess.Popen([sys.executable, "-u", entry_file], cwd=work_dir, stdout=log_file, stderr=subprocess.STDOUT)
    RUNNING_PROCS[bot_name] = proc
    state[bot_name]["should_run"] = True
    save_state(state)
    return True, f"Started (PID: {proc.pid})"

def stop_bot_process(bot_name):
    state = load_state()
    if bot_name in state:
        state[bot_name]["should_run"] = False
        save_state(state)
    proc = RUNNING_PROCS.get(bot_name)
    if proc and proc.poll() is None:
        proc.terminate()
    RUNNING_PROCS.pop(bot_name, None)
    return True, "Stopped"

def watchdog_loop():
    while True:
        try:
            state = load_state()
            for bot_name, info in state.items():
                if info.get("should_run"):
                    proc = RUNNING_PROCS.get(bot_name)
                    if proc is None or proc.poll() is not None:
                        start_bot_process(bot_name)
        except Exception:
            pass
        time.sleep(5)

@app.route("/api/status", methods=["GET"])
def status():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"status": "online"})

@app.route("/api/deploy", methods=["POST"])
def deploy():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    if "file" not in request.files: return jsonify({"error": "No file"}), 400
    
    file = request.files["file"]
    bot_name = request.form.get("bot_name", "").strip()
    entry_file = request.form.get("entry_file", "bot.py").strip()
    
    stop_bot_process(bot_name)
    bot_dir = os.path.join(BASE_DIR, bot_name)
    if os.path.exists(bot_dir): shutil.rmtree(bot_dir)
    os.makedirs(bot_dir, exist_ok=True)

    save_path = os.path.join(bot_dir, file.filename)
    file.save(save_path)

    if file.filename.endswith(".zip"):
        with zipfile.ZipFile(save_path, "r") as zf: zf.extractall(bot_dir)
        os.remove(save_path)

    work_dir, _ = find_entry_path(bot_dir, entry_file)
    if not work_dir: return jsonify({"error": "Entry file missing in package"}), 400

    req_path = os.path.join(work_dir, "requirements.txt")
    if os.path.exists(req_path):
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path])

    state = load_state()
    state[bot_name] = {"entry": entry_file, "should_run": True}
    save_state(state)
    
    ok, msg = start_bot_process(bot_name)
    return jsonify({"message": msg}) if ok else (jsonify({"error": msg}), 500)

@app.route("/api/bots", methods=["GET"])
def list_bots():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    state = load_state()
    bots = [{"name": n, "entry": i["entry"], "status": "running" if (n in RUNNING_PROCS and RUNNING_PROCS[n].poll() is None) else "stopped"} for n, i in state.items()]
    return jsonify({"bots": bots})

@app.route("/api/bots/<bot_name>/start", methods=["POST"])
def api_start(bot_name):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    ok, msg = start_bot_process(bot_name)
    return jsonify({"message": msg})

@app.route("/api/bots/<bot_name>/stop", methods=["POST"])
def api_stop(bot_name):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    ok, msg = stop_bot_process(bot_name)
    return jsonify({"message": msg})

@app.route("/api/bots/<bot_name>/logs", methods=["GET"])
def api_logs(bot_name):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    log_path = os.path.join(BASE_DIR, bot_name, "bot.log")
    if not os.path.exists(log_path): return jsonify({"logs": "No logs yet."})
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        return jsonify({"logs": "".join(f.readlines()[-100:])})

@app.route("/api/bots/<bot_name>", methods=["DELETE"])
def api_delete(bot_name):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    stop_bot_process(bot_name)
    state = load_state()
    state.pop(bot_name, None)
    save_state(state)
    shutil.rmtree(os.path.join(BASE_DIR, bot_name), ignore_errors=True)
    return jsonify({"message": "Deleted"})

if __name__ == "__main__":
    threading.Thread(target=watchdog_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
