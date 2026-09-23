#!/usr/bin/env python3
"""Parameter tuner for farm_v9.

Each candidate is a dict of KAG_PARAMS overrides; the agent reads them from the
KAG_PARAMS env var at import. Evaluation runs bench.py as a subprocess against a
MIXED opponent pool (main.py + starter) so we don't overfit to a dead market —
Kaggle opponents are real agents competing for the same price curves.

Usage:
    .venv/bin/python tune.py --iters 40
"""
import json, os, random, re, subprocess, sys, statistics

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(ROOT, ".venv/bin/python")
AGENT = os.path.join(ROOT, "agents/farm_v9.py")

# name -> (low, high, is_int)
SPACE = {
    "cash_floor":    (0, 300, True),
    "crew_max":      (8, 16, True),
    "ppu":           (3.0, 7.0, False),
    "crew_min":      (4, 8, True),
    "melon_gate":    (300, 1500, True),
    "premium_gate":  (800, 2500, True),
    "premium_units": (6, 10, True),
    "wheat_res":     (0, 8, True),
    "feed_buy_max":  (4, 15, True),
    "feed_est":      (15, 40, True),
    "land_gap":      (4, 14, True),
    "land_buf":      (500, 3000, True),
    "buy_gate":      (600, 2500, True),
    "buy_buf":       (300, 1600, True),
    "buy_units":     (5, 10, True),
    "struct_gate":   (400, 1500, True),
    "herd_cut":      (-16, -6, True),
    "rancher_div":   (2, 5, True),
    "crew_herd_div": (2, 5, True),
    "sb_cap":        (4, 14, True),
    "sb_div":        (3, 6, True),
    "tom_cap":       (3, 8, True),
    "tom_div":       (4, 8, True),
    "melon_cap":     (6, 14, True),
    "melon_div":     (2, 5, True),
    "cap_tom":       (6, 16, True),
    "cap_sb":        (6, 16, True),
    "cap_milk":      (4, 14, True),
    "cap_wool":      (4, 14, True),
    "herd_target":   (8, 18, True),
}

# Incumbent: best params from round 1 (tuned vs weak pool). Re-tuning against
# farm_v5+v6+main.py — real agents that fight for the same market.
DEFAULTS = {"crew_max": 10, "feed_buy_max": 4, "feed_est": 31, "land_gap": 8,
            "tom_div": 5, "cap_milk": 8, "cap_wool": 6, "ppu": 3.0,
            "wheat_res": 5, "buy_units": 7, "herd_cut": -9, "crew_herd_div": 2,
            "cash_floor": 33, "premium_gate": 2229, "land_buf": 2692,
            "rancher_div": 4, "tom_cap": 4, "cap_tom": 14, "herd_target": 12}

ME_RE = re.compile(r"me=\$\s*(\d+)")


def sample(space):
    return {k: (random.randint(lo, hi) if isint else round(random.uniform(lo, hi), 2))
            for k, (lo, hi, isint) in space.items()}


def perturb(params, space, scale=0.25):
    """Perturb ~30% of params within ±scale*range."""
    out = dict(params)
    for k, (lo, hi, isint) in space.items():
        if random.random() < 0.3:
            span = hi - lo
            v = out.get(k, (lo + hi) / 2)
            v = v + random.uniform(-scale * span, scale * span)
            out[k] = int(round(v)) if isint else round(v, 2)
            out[k] = max(lo, min(hi, out[k]))
    return out


def evaluate(params, n_games=4):
    """Mean of all 'me=$' rewards: n_games vs main.py + n_games vs starter."""
    env = dict(os.environ)
    env["KAG_PARAMS"] = json.dumps(params)
    try:
        r = subprocess.run(
            [PY, "bench.py", "-a", AGENT, "-o",
             os.path.join(ROOT, "agents/farm_v5.py"),
             os.path.join(ROOT, "agents/farm_v6.py"), "main.py",
             "-n", str(n_games), "-j", "12"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        vals = [int(x) for x in ME_RE.findall(r.stdout)]
        return statistics.mean(vals) if vals else 0
    except subprocess.TimeoutExpired:
        return 0


def main():
    iters = int(sys.argv[sys.argv.index("--iters") + 1]) if "--iters" in sys.argv else 40
    random.seed(7)

    best = dict(DEFAULTS)
    best_score = evaluate(best)
    print(f"baseline score={best_score:.0f} params={best}", flush=True)

    log = open(os.path.join(ROOT, "tune_log.jsonl"), "a")
    log.write(json.dumps({"iter": -1, "params": best, "score": best_score}) + "\n")

    scale = 0.4
    for i in range(iters):
        # Alternate: fresh random sample vs perturbation of the incumbent
        cand = sample(SPACE) if i % 3 == 0 else perturb(best, SPACE, scale)
        score = evaluate(cand)
        improved = score > best_score
        if improved:
            best, best_score = cand, score
        log.write(json.dumps({"iter": i, "params": cand, "score": score,
                              "best": best_score}) + "\n")
        log.flush()
        print(f"[{i:3d}] score={score:6.0f} {'<<< NEW BEST' if improved else ''} "
              f"(best={best_score:.0f})", flush=True)
        if i == iters // 2:
            scale = 0.15   # tighten perturbations in the back half

    print(f"\nFINAL best={best_score:.0f}")
    print(json.dumps(best, indent=2))


if __name__ == "__main__":
    main()
