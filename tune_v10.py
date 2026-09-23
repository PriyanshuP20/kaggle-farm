#!/usr/bin/env python3
"""Parameter tuner for farm_v10 — the cow/sheep pasture-ranch agent.

Each candidate is a dict of KAG_PARAMS overrides; the agent reads them from the
KAG_PARAMS env var at import. Evaluation runs bench.py against a MIXED pool
(v9 + v5 + starter) so we don't overfit to either a dead market or a pure
mirror match.

Usage:
    .venv/bin/python tune_v10.py --iters 40
"""
import json, os, random, re, subprocess, sys, statistics

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(ROOT, ".venv/bin/python")
AGENT = os.path.join(ROOT, "agents/farm_v10.py")

# name -> (low, high, is_int)
SPACE = {
    "crew_max":        (8, 14, True),
    "crew_min":        (4, 9, True),
    "hire_last_hour":  (4, 14, True),
    "herd_target":     (10, 22, True),
    "buy_per_turn":    (1, 4, True),
    "buy_buf":         (50, 400, True),
    "buy_last_day":    (14, 23, True),
    "feed_buy_max":    (5, 15, True),
    "feed_res_x":      (4, 16, True),
    "feed_est":        (30, 70, True),
    "struct_ahead":    (2, 6, True),
    "feed_trip_max":   (6, 16, True),
    "ppu":             (3.0, 7.0, False),
    "wheat_cap":       (14, 30, True),
    "wheat_x":         (2, 10, True),
    "sb_cap":          (8, 26, True),
    "sb_first_day":    (2, 8, True),
    "sb_last_plant":   (10, 18, True),
    "melon_cap":       (6, 18, True),
    "melon_last_plant":(8, 20, True),
    "tom_cap":         (2, 8, True),
    "carrot_cap":      (4, 12, True),
    "cash_floor":      (0, 200, True),
    "early_floor":     (50, 300, True),
    "land_gap":        (0, 8, True),
    "land_buf":        (400, 1600, True),
    "land1_day":       (4, 10, True),
    "land2_day":       (7, 14, True),
    "cap_milk":        (10, 40, True),
    "cap_wool":        (5, 20, True),
    "cap_sb":          (15, 45, True),
    "floor_milk":      (0.4, 1.0, False),
    "floor_wool":      (0.4, 1.0, False),
    "milk_bad_price":  (50, 140, True),
    "fert_keep":       (0, 20, True),
    "fert_use_min":    (2, 15, True),
}

DEFAULTS = {}   # empty = in-file defaults

ME_RE = re.compile(r"me=\$\s*(\d+)")


def sample(space):
    return {k: (random.randint(lo, hi) if isint else round(random.uniform(lo, hi), 2))
            for k, (lo, hi, isint) in space.items()}


def perturb(params, space, scale=0.25):
    out = dict(params)
    for k, (lo, hi, isint) in space.items():
        if random.random() < 0.3:
            span = hi - lo
            v = out.get(k, (lo + hi) / 2)
            v = v + random.uniform(-scale * span, scale * span)
            out[k] = int(round(v)) if isint else round(v, 2)
            out[k] = max(lo, min(hi, out[k]))
    return out


def evaluate(params, n_games=3):
    env = dict(os.environ)
    env["KAG_PARAMS"] = json.dumps(params)
    try:
        r = subprocess.run(
            [PY, "bench.py", "-a", AGENT, "-o",
             os.path.join(ROOT, "agents/farm_v9.py"),
             os.path.join(ROOT, "agents/farm_v5.py"), "starter",
             "-n", str(n_games), "-j", "12"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        vals = [int(x) for x in ME_RE.findall(r.stdout)]
        return statistics.mean(vals) if vals else 0
    except subprocess.TimeoutExpired:
        return 0


def main():
    iters = int(sys.argv[sys.argv.index("--iters") + 1]) if "--iters" in sys.argv else 40
    random.seed(11)

    best = dict(DEFAULTS)
    best_score = evaluate(best)
    print(f"baseline score={best_score:.0f} params={best}", flush=True)

    log = open(os.path.join(ROOT, "tune_v10_log.jsonl"), "a")
    log.write(json.dumps({"iter": -1, "params": best, "score": best_score}) + "\n")

    scale = 0.4
    for i in range(iters):
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
            scale = 0.15

    print(f"\nFINAL best={best_score:.0f}")
    print(json.dumps(best, indent=2))


if __name__ == "__main__":
    main()
