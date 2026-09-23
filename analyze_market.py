#!/usr/bin/env python3
"""Per-day market action breakdown: what do the winners buy and sell, when?"""
import json, sys, collections

rep = json.load(open(sys.argv[1]))
steps = rep["steps"]

for p in range(2):
    print(f"\n===== Player {p} =====")
    sells = collections.Counter()
    buys = collections.Counter()
    hires = 0
    lands = 0
    for s in steps:
        a = s[p].get("action") or {}
        for m in a.get("market") or []:
            if m[0] == "SELL":
                sells[m[1]] += m[2] if len(m) > 2 else 1
            elif m[0] == "BUY_PRODUCT":
                buys[m[1]] += m[2] if len(m) > 2 else 1
            elif m[0] == "BUY_SEED":
                buys[m[1] + "_seed"] += m[2] if len(m) > 2 else 1
            elif m[0] == "BUY_ANIMAL":
                buys[m[1] + "_animal"] += 1
            elif m[0] == "HIRE":
                hires += 1
            elif m[0] == "BUY_LAND":
                lands += 1
    print("SELLS:", dict(sells.most_common()))
    print("BUYS :", dict(buys.most_common()))
    print("hires:", hires, "lands:", lands)

    # day-by-day: money + wheat/fert buys+sells volume
    print("day | money | wheat bought | wheat sold | fert bought | fert sold | melon sold | egg sold")
    perday = collections.defaultdict(lambda: collections.Counter())
    for s in steps:
        o = s[p]["observation"]
        d = o["day"]
        a = s[p].get("action") or {}
        for m in a.get("market") or []:
            if m[0] in ("SELL", "BUY_PRODUCT") and len(m) > 2:
                perday[d][f"{m[0]}:{m[1]}"] += m[2]
        perday[d]["money"] = s[p]["observation"]["farms"][p]["money"]
    for d in sorted(perday):
        if d % 3 == 0:
            r = perday[d]
            print(f"{d:3d} | {r['money']:7.0f} | {r['BUY_PRODUCT:WHEAT']:12.0f} | {r['SELL:WHEAT']:10.0f} | {r['BUY_PRODUCT:FERTILIZER']:12.0f} | {r['SELL:FERTILIZER']:9.0f} | {r['SELL:MELON']:10.0f} | {r['SELL:EGG']:8.0f}")
