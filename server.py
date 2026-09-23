#!/usr/bin/env python3
"""Local web UI for Kaggriculture.

Pick two agents, run an episode, and watch it play out live in the browser.
The backend steps the environment manually at a configurable speed and the
frontend polls /api/state for the latest snapshot.

Run:  .venv/bin/python server.py   then open http://localhost:5050
"""

import json
import os
import re
import threading
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory
from kaggle_environments import make
from kaggle_environments.agent import Agent

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
UPLOAD_DIR = os.path.join(ROOT, "agents")

BUILTINS = ["pass", "random", "starter"]
NON_AGENT_FILES = {"server.py", "bench.py", "run_game.py", "tune.py", "tune_v10.py",
                   "analyze_draws.py", "analyze_ep.py", "analyze_market.py",
                   "analyze_replay.py", "bench_draws.py", "cmp_profiles.py",
                   "draw_map.py", "sim_market.py"}


def _archived_agent(name):
    """agents/farm_vN.py with N < 10 are archived — kept on disk, hidden here."""
    m = re.fullmatch(r"farm_v(\d+)\.py", name)
    return m is not None and int(m.group(1)) < 10

app = Flask(__name__, static_folder=WEB, static_url_path="")

_lock = threading.Lock()
_game = {
    "thread": None,
    "stop": False,
    "running": False,
    "snapshot": None,   # latest serializable state for the frontend
    "error": None,
    "replay": None,     # env.toJSON() after the episode ends
}


def _plain(o):
    """Recursively convert structify/nested containers to plain python for JSON."""
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    return o


def _name(spec):
    base = os.path.basename(str(spec))
    return base[:-3] if base.endswith(".py") else base


def _list_agent_files():
    out = []
    skip_dirs = {".venv", "__pycache__", "replays", "kaggriculture-data", ".git"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for n in files:
            if (n.endswith(".py") and n not in NON_AGENT_FILES
                    and not _archived_agent(n)):
                out.append(os.path.relpath(os.path.join(base, n), ROOT))
    return sorted(out)


def _resolve(spec):
    """'starter' stays a builtin name; 'agents/foo.py' becomes an abs path."""
    if spec in BUILTINS:
        return spec
    path = spec if os.path.isabs(spec) else os.path.join(ROOT, spec)
    if not os.path.exists(path):
        raise FileNotFoundError(f"agent not found: {spec}")
    return path


def _snapshot(env, running, names):
    state = env.state
    obs0 = state[0].observation
    farms = _plain(obs0.farms)
    players = []
    for i, s in enumerate(state):
        private = s.observation.get("private", {}) if hasattr(s.observation, "get") else {}
        players.append({
            "index": i,
            "name": names[i] if names else f"agent{i}",
            "status": str(s.status),
            "reward": s.reward,
            "money": farms[i]["money"],
            "farmer": farms[i]["farmer"],
            "hands": farms[i]["hands"],
            "unlocked": farms[i]["unlocked_quadrants"],
            "hires_today": farms[i]["hires_today"],
            "tiles": farms[i]["tiles"],
            "shed": _plain(private.get("shed", {})),
            "seeds": _plain(private.get("seeds", {})),
            "inventories": _plain(private.get("inventories", [])),
        })
    return {
        "running": running,
        "done": bool(env.done),
        "step": int(getattr(obs0, "step", 0)),
        "day": int(getattr(obs0, "day", 0)),
        "hour": int(getattr(obs0, "hour", 0)),
        "episodeSteps": int(env.configuration.episodeSteps),
        "turnsPerDay": int(getattr(env.configuration, "turnsPerDay", 24)),
        "market": _plain(getattr(obs0, "market", {})),
        "town": _plain(getattr(obs0, "town", {})),
        "players": players,
        "error": _game["error"],
    }


def _publish(env, running, names):
    with _lock:
        _game["snapshot"] = _snapshot(env, running, names)
        _game["running"] = running


def _run_game(specs, seed, steps, sps):
    try:
        env = make("kaggriculture",
                   configuration={"episodeSteps": steps, "seed": seed},
                   debug=False)
        if len(env.state) != 2:
            env.reset(2)
        names = [_name(s) for s in specs]
        agents = [Agent(_resolve(s), env) for s in specs]
        _publish(env, True, names)

        delay = 0.0 if sps <= 0 else 1.0 / sps
        while not env.done and not _game["stop"]:
            actions = []
            for i, a in enumerate(agents):
                # Mirror env.run/act_agent: only ACTIVE agents act; act()
                # returns the exception itself on agent errors.
                if env.state[i].status == "ACTIVE":
                    action, _log = a.act(env.state[i].observation)
                    actions.append(action)
                else:
                    actions.append(None)
            env.step(actions)
            _publish(env, True, names)
            if delay:
                time.sleep(delay)

        _game["replay"] = env.toJSON()
        _publish(env, False, names)
    except Exception:
        with _lock:
            _game["error"] = traceback.format_exc()
            _game["running"] = False


# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.route("/api/agents")
def api_agents():
    return jsonify({"builtins": BUILTINS, "files": _list_agent_files()})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    if not f or not f.filename.endswith(".py"):
        return jsonify({"error": "expected a .py file"}), 400
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe = os.path.basename(f.filename)
    f.save(os.path.join(UPLOAD_DIR, safe))
    return jsonify({"saved": f"agents/{safe}", "files": _list_agent_files()})


@app.route("/api/start", methods=["POST"])
def api_start():
    body = request.get_json(force=True)
    agent = body.get("agent", "main.py")
    opponent = body.get("opponent", "starter")
    seed = int(body.get("seed", 0))
    steps = int(body.get("steps", 720))
    sps = float(body.get("speed", 20))  # steps/sec; 0 = max speed

    with _lock:
        _game["stop"] = True
        _game["error"] = None
        _game["replay"] = None
    t = _game.get("thread")
    if t and t.is_alive():
        t.join(timeout=3)

    _game["stop"] = False
    thread = threading.Thread(
        target=_run_game, args=([agent, opponent], seed, steps, sps), daemon=True
    )
    _game["thread"] = thread
    thread.start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    _game["stop"] = True
    return jsonify({"ok": True})


@app.route("/api/state")
def api_state():
    with _lock:
        snap = _game["snapshot"]
        running = _game["running"]
        err = _game["error"]
    if snap is None:
        return jsonify({"running": running, "done": False, "snapshot": None, "error": err})
    snap = dict(snap)
    snap["running"] = running
    snap["error"] = err
    return jsonify(snap)


@app.route("/api/replay")
def api_replay():
    if not _game["replay"]:
        return jsonify({"error": "no finished episode yet"}), 404
    return app.response_class(
        json.dumps(_game["replay"]),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=replay.json"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, threaded=True)
