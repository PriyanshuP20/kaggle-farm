#!/usr/bin/env python3
"""Analyze a kaggriculture episode replay: what did each player actually do?

Usage: .venv/bin/python analyze_replay.py replays_top/111412741.json
"""
import json, sys, collections

path = sys.argv[1]
rep = json.load(open(path))
steps = rep["steps"]
n_players = len(steps[0])

print(f"episode: {path.split('/')[-1]}  steps={len(steps)}  config seed={rep['configuration'].get('seed')}")

for p in range(n_players):
    ops = collections.Counter()
    mkts = collections.Counter()
    last = None
    for s in steps:
        st = s[p]
        a = st.get("action") or {}
        if isinstance(a, dict):
            for op in [a.get("farmer")] + list(a.get("hands") or []):
                if op:
                    ops[op[0]] += 1
            for m in a.get("market") or []:
                mkts[m[0]] += (m[2] if len(m) > 2 and isinstance(m[2], (int, float)) else 1)
        last = st
    obs = last["observation"]
    f = obs["farms"][p]
    tiles = collections.Counter()
    for row in f["tiles"]:
        for t in row:
            if isinstance(t, dict):
                tiles[t.get("crop") or t.get("animal") or t.get("kind", "?")] += 1
            elif t is None:
                tiles["empty"] += 1
            else:
                tiles["locked"] += 1
    print(f"\n=== Player {p} — final money=${f['money']:.0f} ===")
    print("  final tiles:", dict(tiles))
    print("  land:", len(f["unlocked_quadrants"]))
    print("  market ops:", dict(mkts))
    print("  unit ops:", dict(ops.most_common(12)))

# money curve at day boundaries (hour 0 of each day)
print("\n=== money by day ===")
print("day | " + " | ".join(f"P{p}" for p in range(n_players)))
for s in steps:
    o = s[0]["observation"]
    if o.get("hour") == 0 and o.get("day", 0) % 3 == 0:
        print(f"{o['day']:3d} | " + " | ".join(f"{s[p]['observation']['farms'][p]['money']:7.0f}" for p in range(n_players)))

# herd trajectory
print("\n=== herd size by day ===")
for s in steps:
    o = s[0]["observation"]
    if o.get("hour") == 12 and o.get("day", 0) % 3 == 0:
        row = []
        for p in range(n_players):
            h = sum(1 for r in s[p]["observation"]["farms"][p]["tiles"] for t in r
                    if isinstance(t, dict) and "animal" in t)
            row.append(h)
        print(f"{o['day']:3d} | " + " | ".join(map(str, row)))
