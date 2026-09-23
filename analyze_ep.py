import json, sys, collections

def analyze(path):
    d = json.load(open(path))
    steps = d["steps"]
    names = d["info"].get("TeamNames") or [f"agent{i}" for i in range(len(steps[0]))]
    rewards = d.get("rewards")
    n_players = len(steps[0])

    print("=" * 78)
    print(f"{path}  seed={d['info'].get('seed')}")
    for i, n in enumerate(names):
        print(f"  player {i}: {n}  final={rewards[i] if rewards else '?'}")

    for p in range(n_players):
        print("\n" + "-" * 78)
        print(f"PLAYER {p}: {names[p]}")
        money_by_day = {}
        hires_by_day = collections.defaultdict(int)
        sell_rev = collections.defaultdict(float)
        sell_units = collections.defaultdict(int)
        spend = collections.defaultdict(float)
        buy_units = collections.defaultdict(int)
        land_day = {}
        verbs_day = collections.defaultdict(lambda: collections.defaultdict(int))
        animal_buys = collections.defaultdict(list)   # day -> list
        seed_buys = collections.defaultdict(lambda: collections.defaultdict(int))
        prod_buys = collections.defaultdict(lambda: collections.defaultdict(int))
        first_seen = {}
        # daily snapshots
        snaps = []
        prev_quads = None
        for t, step in enumerate(steps):
            s = step[p]
            obs = s["observation"]
            day, hour = obs["day"], obs["hour"]
            farm = obs["farms"][p]
            money_by_day[day] = farm["money"]
            # market orders
            for order in s["action"].get("market") or []:
                if not order:
                    continue
                op = order[0]
                if op == "SELL":
                    item, n = order[1], order[2]
                    price = obs["market"]["prices"].get(item, 0)
                    sell_rev[item] += n * price
                    sell_units[item] += n
                elif op == "BUY_ANIMAL":
                    animal_buys[day].append((order[1], order[2]))
                    buy_units[order[1]] += order[2]
                elif op == "BUY_SEED":
                    seed_buys[day][order[1]] += order[2]
                elif op == "BUY_PRODUCT":
                    prod_buys[day][order[1]] += order[2]
                elif op == "HIRE":
                    hires_by_day[day] += 1
                elif op == "BUY_LAND":
                    land_day.setdefault(day, 0)
                    land_day[day] += 1
            # verbs
            fa = s["action"].get("farmer") or []
            if fa and fa[0] != "PASS":
                verbs_day[day][fa[0]] += 1
            for h in s["action"].get("hands") or []:
                if h and h[0] != "PASS":
                    verbs_day[day][h[0]] += 1
            # snapshot at hour 0 (start of day state)
            if hour == 0:
                animals = collections.defaultdict(int)
                structs = collections.defaultdict(int)
                plants = collections.defaultdict(int)
                weeds = 0
                empty_structs = 0
                for row in farm["tiles"]:
                    for tl in row:
                        if not isinstance(tl, dict):
                            continue
                        k = tl.get("kind")
                        if k == "PLANT":
                            plants[tl.get("crop", "?")] += 1
                        elif k in ("COOP", "PASTURE"):
                            structs[k] += 1
                            if tl.get("animal"):
                                animals[tl["animal"]] += 1
                                key = f"first_{tl['animal']}"
                                first_seen.setdefault(key, day)
                            else:
                                empty_structs += 1
                        elif k == "WEED":
                            weeds += 1
                shed = obs["private"]["shed"]
                shed_animals = {k: v for k, v in shed.items() if k in ("GOOSE", "COW", "SHEEP") and v}
                snaps.append({
                    "day": day, "money": farm["money"],
                    "quads": "".join(q[0] + q[1] for q in farm["unlocked_quadrants"]),
                    "hands": len(farm["hands"]),
                    "animals": dict(animals), "structs": dict(structs),
                    "empty_structs": empty_structs,
                    "plants": dict(plants), "weeds": weeds,
                    "shed_animals": shed_animals,
                    "shed_wheat": shed.get("WHEAT", 0),
                    "shed_fert": shed.get("FERTILIZER", 0),
                    "invtotal": sum(sum(inv.values()) for inv in obs["private"]["inventories"]),
                })
            prev_quads = farm["unlocked_quadrants"]

        print("\nDay | money | quads | hands | animals(on-tile) | structs(empty) | plants | shedAnimals shedWheat | weeds")
        for sn in snaps:
            a = ",".join(f"{k}:{v}" for k, v in sn["animals"].items()) or "-"
            st = ",".join(f"{k}:{v}" for k, v in sn["structs"].items()) or "-"
            pl = ",".join(f"{k}:{v}" for k, v in sn["plants"].items()) or "-"
            sa = ",".join(f"{k}:{v}" for k, v in sn["shed_animals"].items()) or "-"
            print(f"{sn['day']:>3} | {sn['money']:>8.0f} | {sn['quads']:<8} | {sn['hands']:>2} | {a:<28} | {st}({sn['empty_structs']}) | {pl:<40} | {sa} w:{sn['shed_wheat']} f:{sn['shed_fert']} | {sn['weeds']}")

        print("\nHires/day:", dict(sorted(hires_by_day.items())))
        print("Land buys (day:count):", land_day)
        print("Animal buys by day:", {d_: v for d_, v in sorted(animal_buys.items())})
        print("Total animals bought:", dict(buy_units))
        print("Seed buys by day:", {d_: dict(v) for d_, v in sorted(seed_buys.items())})
        print("Product buys by day:", {d_: dict(v) for d_, v in sorted(prod_buys.items())})
        print("\nSell revenue by product (est):", {k: round(v) for k, v in sorted(sell_rev.items(), key=lambda x: -x[1])})
        print("Sell units:", dict(sell_units))
        print("Total est sell rev:", round(sum(sell_rev.values())))
        # top verbs overall
        tot = collections.defaultdict(int)
        for d_, v in verbs_day.items():
            for verb, c in v.items():
                tot[verb] += c
        print("Verb totals:", dict(sorted(tot.items(), key=lambda x: -x[1])))

if __name__ == "__main__":
    for p in sys.argv[1:]:
        analyze(p)
