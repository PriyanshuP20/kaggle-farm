"""Kaggriculture v7 — scale the ranch to 20+ animals.

v6 diagnosis (seed 0, $48k): herd stalled at 8-9 of 14 planned, only 2
quadrants unlocked, crew shrank to 7 late. Revenue was melon $20k + milk $14k
+ fertilizer $13k + wheat $10k — eggs only $6.6k (143 units) because the herd
never scaled. Egg (log) and milk (scarcity) curves reward volume; fertilizer
is linear free money per animal-day.

v7 changes vs v6:
  - HERD_TARGET 14 -> 22, geese front-loaded (10 first).
  - Cheaper gates: animals/structures/land trigger earlier (momentum > hoarding).
  - Ranchers leaner: ceil(work/6)+1 instead of /3 (feeding is 2 ops/animal/day).
  - Crew floor 8 late (harvest surge needs hands); CREW_MAX 12 -> 14.
  - Bigger wheat feed reserve (herd+4); buy animals until day -8.
"""

CROPS = {
    "WHEAT":      {"seed": 10,  "first": 2,  "max": 4,  "ongoing": False},
    "CARROT":     {"seed": 20,  "first": 2,  "max": 3,  "ongoing": False},
    "TOMATO":     {"seed": 50,  "first": 8,  "max": 8,  "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first": 10, "max": 10, "ongoing": True},
    "MELON":      {"seed": 80,  "first": 10, "max": 12, "ongoing": False},
}
ANIMALS = {
    "GOOSE": {"cost": 300, "struct": "COOP",    "product": "EGG"},
    "COW":   {"cost": 400, "struct": "PASTURE", "product": "MILK"},
    "SHEEP": {"cost": 500, "struct": "PASTURE", "product": "WOOL"},
}
LAND_PRICES = [1000, 2000, 4000]  # NE, SW, SE

# Town shops (copied from the interpreter — agents can't import the env).
# Each unlocked instance consumes 1 of every demanded product every 4 turns
# (~6/day); single-product shops consume 2x. Products nobody demands never
# recover — their curve is a fixed pool, first come first served.
SHOPS = {
    "BAKERY":         ["EGG", "WHEAT"],
    "PIZZA_SHOP":     ["MILK", "TOMATO", "WHEAT"],
    "BRUNCH_SPOT":    ["EGG", "WHEAT", "STRAWBERRY"],
    "YARN_STORE":     ["WOOL"],
    "ICE_CREAM_SHOP": ["STRAWBERRY", "MILK", "WHEAT"],
    "PET_CAFE":       ["CARROT"],
    "SMOOTHIE_SHOP":  ["STRAWBERRY", "MILK"],
    "FARMERS_MARKET": ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY"],
}
# Daily production yield per unit (for drain accounting).
YIELD_RATE = {"STRAWBERRY": 0.5, "TOMATO": 1.0, "EGG": 1.5, "MILK": 0.5, "WOOL": 0.33}
PRODUCTS = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER"]

# Glut-sensitive goods get small daily caps — but ONLY for products the town
# actually drains overnight (strawberry/milk/wool have shop demand). Melon has
# no shop demand at all: its market inventory never recovers, so holding back
# just strands stock at season end — always sell it.
SELL_CAP = {"TOMATO": 10, "STRAWBERRY": 10, "MILK": 8, "WOOL": 8}

import os
CASH_FLOOR = 120
CREW_MAX = int(os.environ.get("KAG_CREW", 14))
CREW_FLOOR = 8
PLOTS_PER_UNIT = float(os.environ.get("KAG_PPU", 5))

# Herd plan is built dynamically per episode: geese always (eggs ride a log
# curve — they scale at volume), sheep/cows only when this episode's shops
# actually drain their products. Without a YARN_STORE, wool's sq-curve is a
# ~58-unit pool; sheep are barely breakeven. See _herd_plan().
# v7: geese front-loaded 10-deep — the ranch pays back in ~4 days, so early
# scale compounds all season.
HERD_TARGET = int(os.environ.get("KAG_HERD", 22))
HERD_BUY_LAST_DAY = -8     # stop buying animals this many days before the end
ANIMAL_STRUCT = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}

_S = {"day": -1, "sold": {}, "route": {}, "cargo": {}}

PRIO_AHARVEST, PRIO_HARVEST, PRIO_FEED, PRIO_WATER, PRIO_CARE, PRIO_DIG, PRIO_PLANT, PRIO_PLACE, PRIO_BUILD = range(9)


def _shed_tiles(board):
    c = board // 2
    return [(c - 1, c - 1), (c, c - 1), (c - 1, c), (c, c)]


def _step_toward(pos, target):
    x, y = pos
    tx, ty = target
    if x != tx:
        return ["EAST" if tx > x else "WEST"]
    if y != ty:
        return ["SOUTH" if ty > y else "NORTH"]
    return ["PASS"]


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _fib(n):
    a, b = 1, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def _drain_rates(town):
    """Units/day the town removes from each product's market inventory."""
    drain = {p: 0 for p in PRODUCTS}
    for s in town.get("unlocked_shops", []):
        prods = SHOPS.get(s, [])
        mult = 2 if len(prods) == 1 else 1
        for p in prods:
            drain[p] += 6 * mult
    for p in PRODUCTS:
        if p != "FERTILIZER":
            drain[p] += 1   # town center buys 1/day of everything else
    return drain


def _opp_crops(obs):
    """Opponent's live crop counts — their tiles are public, so we can see a
    flood coming before it hits our curves."""
    counts = {}
    for row in obs["farms"][1 - obs["player"]]["tiles"]:
        for t in row:
            if isinstance(t, dict) and t.get("kind") == "PLANT" and t.get("crop"):
                counts[t["crop"]] = counts.get(t["crop"], 0) + 1
    return counts


def _herd_plan(drain):
    """v6's proven base, extended with geese: eggs ride a log curve so they
    pay at volume; the SHEEP/COW slots diversify into separate curves."""
    base = ["GOOSE", "GOOSE", "GOOSE", "SHEEP", "COW", "GOOSE",
            "SHEEP", "GOOSE", "COW", "GOOSE", "SHEEP", "COW"]
    return (base + ["GOOSE"] * (HERD_TARGET - len(base)))[:HERD_TARGET]


def _crop_targets(day, last_day, n_units, money, drain, opp):
    labor = n_units * PLOTS_PER_UNIT
    t = {}
    if n_units >= 6 and money > 700:
        # Melon is a fixed ~150-unit pool — if the opponent is flooding it,
        # we'd split the curve; cut our share and diversify instead.
        cut = 6 if opp.get("MELON", 0) >= 8 else 0
        t["MELON"] = max(4, min(10, labor // 3) - cut)
    # v7: strawberry/tomato deferred to day 2+ — day-0 cash buys the ranch
    # first (geese pay back in 4 days); premium seeds on starting capital
    # starved v7a's herd until day 14 (-$20k lesson).
    if n_units >= 8 and money > 1500 and day >= 2:
        t["STRAWBERRY"] = min(8, labor // 4)
        t["TOMATO"] = min(5, labor // 6)
    if day >= last_day - 5:
        t = {"WHEAT": labor, "CARROT": min(8, labor // 4)}
    else:
        t["WHEAT"] = max(6, labor - sum(t.values()))
    for c in list(t):
        if day + CROPS[c]["max"] > last_day:
            del t[c]
    return {c: n for c, n in t.items() if n > 0}


def _agent_impl(obs, config):
    global _S
    me = obs["farms"][obs["player"]]
    private = obs["private"]
    tiles = me["tiles"]
    board = len(tiles)
    day, hour = obs["day"], obs["hour"]
    money = me["money"]
    seeds = private["seeds"]
    shed = private["shed"]
    inventories = private.get("inventories", [])
    turns_per_day = int(config.get("turnsPerDay", 24))
    total_days = int(config.get("episodeSteps", 720) + turns_per_day - 1) // turns_per_day
    last_day = total_days - 1
    shed_tiles = _shed_tiles(board)
    drain = _drain_rates(obs.get("town", {}))
    opp = _opp_crops(obs)
    herd_plan = _herd_plan(drain)
    shed_set = set(shed_tiles)

    if _S["day"] != day:
        _S = {"day": day, "sold": {}, "route": {}, "cargo": {}}

    growing = {}
    animals = []          # (x, y, animal, tile)
    empty_structs = []    # (x, y, struct)
    for y, row in enumerate(tiles):
        for x, t in enumerate(row):
            if isinstance(t, dict):
                if t.get("kind") == "PLANT":
                    growing[t["crop"]] = growing.get(t["crop"], 0) + 1
                elif "animal" in t:
                    animals.append((x, y, t["animal"], t))
                elif t.get("kind") in ("COOP", "PASTURE"):
                    empty_structs.append((x, y, t["kind"]))
    planted_total = sum(growing.values())
    unlocked_tiles = sum(1 for row in tiles for t in row if t != "LOCKED")
    n_units = 1 + len(me["hands"])
    plot_cap = min(unlocked_tiles - 4, n_units * PLOTS_PER_UNIT)

    herd = len(animals)
    in_transit = 0
    transit_by_type = {}
    for inv in inventories:
        for a in ANIMALS:
            n = inv.get(a, 0)
            if n:
                in_transit += n
                transit_by_type[a] = transit_by_type.get(a, 0) + n
    for a in ANIMALS:
        n = shed.get(a, 0)
        if n:
            in_transit += n
            transit_by_type[a] = transit_by_type.get(a, 0) + n
    herd_total = herd + in_transit
    wheat_reserve = herd + 4  # shed wheat held back for tomorrow's feeding

    # ---- market ------------------------------------------------------------
    market = []
    spend = 0

    # 1) Crew sized to workload, leading planting slightly so the farm keeps
    #    growing (planted -> crew -> cap -> more planted). Ranch adds ~1
    #    rancher per 6 animals (feeding is 2 ops/animal/day). Floor 8: the
    #    late harvest surge needs hands even as planting winds down.
    crew_target = min(CREW_MAX, max(CREW_FLOOR, -(-planted_total // 3) + 1 + herd_total // 6))
    if day < last_day and hour <= turns_per_day // 2:
        while True:
            k = me["hires_today"] + sum(1 for m in market if m[0] == "HIRE")
            if k >= crew_target:
                break
            cost = _fib(k)
            if money - spend - cost < 2 or len(market) >= 10:
                break
            market.append(["HIRE"])
            spend += cost

    # 2) Seeds for today's crop mix, bounded by watering headroom.
    #    v7: while the founding herd is still unbought, hold $700 back so seed
    #    splurges can't eat the first geese.
    room_to_grow = max(0, plot_cap - planted_total)
    seed_floor = 700 if (day < 6 and herd_total < 4) else CASH_FLOOR
    for crop, target in _crop_targets(day, last_day, n_units, money, drain, opp).items():
        deficit = min(target - growing.get(crop, 0) - seeds.get(crop, 0), room_to_grow)
        if deficit <= 0 or len(market) >= 10:
            continue
        cost = deficit * CROPS[crop]["seed"]
        if money - spend - cost < seed_floor:
            deficit = max(0, int((money - spend - seed_floor) // CROPS[crop]["seed"]))
            cost = deficit * CROPS[crop]["seed"]
        if deficit > 0:
            market.append(["BUY_SEED", crop, deficit])
            spend += cost

    # 3) Wheat for the animals: keep the shed above reserve every turn — a
    #    missed feeding day is an escape risk. Buying wheat is cheap
    #    insurance even as the price drifts up with volume.
    wheat_in_shed = shed.get("WHEAT", 0)
    if herd_total > 0 and wheat_in_shed < wheat_reserve and len(market) < 10:
        need = min(wheat_reserve - wheat_in_shed, 6)
        if money - spend - need * 25 > CASH_FLOOR:
            market.append(["BUY_PRODUCT", "WHEAT", need])
            spend += need * 25

    # 4) Land only when tiles are genuinely scarce AND cash is rich — v7a
    #    lesson: buying SW on day 1 with starting cash starved the ranch for
    #    two weeks (-$20k). Quadrant must be covered 2x over.
    used_tiles = planted_total + herd + len(empty_structs)
    n_extra = len(me["unlocked_quadrants"]) - 1
    if n_extra < 3:
        want_land = used_tiles >= unlocked_tiles - 6
        if (want_land and money - spend > LAND_PRICES[n_extra] * 2 + 1500
                and day + 8 <= last_day and len(market) < 10):
            market.append(["BUY_LAND"])
            spend += LAND_PRICES[n_extra]

    # Plan order minus animals already placed or in transit — used by both
    # the buy step and the structure lookahead below.
    remaining = list(herd_plan)
    for _, _, a, _t in animals:
        if a in remaining:
            remaining.remove(a)
    for a, n in transit_by_type.items():
        for _ in range(n):
            if a in remaining:
                remaining.remove(a)

    # 5) Animals: buy planned animals that have a free home, up to 2/turn —
    #    skipping ones without structures (a homeless cow must not block the
    #    geese queued behind it; want_struct will get it a pasture anyway).
    #    v7: lower gates — the ranch pays back in ~4 days, momentum beats hoarding.
    if (money - spend > 700 and day < last_day + HERD_BUY_LAST_DAY
            and n_units >= 6):
        bought = 0
        for a in remaining:
            if bought >= 2 or len(market) >= 10:
                break
            have_home = sum(1 for _, _, k in empty_structs
                            if k == ANIMAL_STRUCT[a]) > transit_by_type.get(a, 0)
            if have_home and money - spend > ANIMALS[a]["cost"] + 500:
                market.append(["BUY_ANIMAL", a, 1])
                spend += ANIMALS[a]["cost"]
                transit_by_type[a] = transit_by_type.get(a, 0) + 1
                bought += 1

    # 6) Sells last — leftovers retry next turn.
    dump_all = day >= last_day - 1
    for item, n in shed.items():
        if n <= 0 or item in ("GOOSE", "COW", "SHEEP") or len(market) >= 10:
            continue
        if item == "WHEAT":
            n = max(0, n - wheat_reserve)   # keep the feed reserve
            if n <= 0:
                continue

        cap = SELL_CAP.get(item)
        if cap is not None and not dump_all:
            # the town can drink `drain` units/day before the curve slips —
            # sell up to that pace, never slower than the fixed floor
            cap = max(cap, drain.get(item, 0))
        allowed = n if (cap is None or dump_all) else max(0, cap - _S["sold"].get(item, 0))
        sell_n = min(n, allowed)
        if sell_n > 0:
            market.append(["SELL", item, sell_n])
            _S["sold"][item] = _S["sold"].get(item, 0) + sell_n

    # ---- tasks -------------------------------------------------------------
    # Crop tasks share a per-tile map (one op each). Animal chores are split:
    # FEED/PLACE/BUILD live in ranch-only structures (they need cargo or a
    # structure), while harvest/care/collect stay generic so anyone can do them.
    tasks, plant_candidates = [], []
    feed_set, place_map, build_map = set(), {}, {}
    can_plant = hour <= turns_per_day - 2

    allowance = {}
    for crop, target in _crop_targets(day, last_day, n_units, money, drain, opp).items():
        allowance[crop] = max(0, target - growing.get(crop, 0))

    for y, row in enumerate(tiles):
        for x, t in enumerate(row):
            if t == "LOCKED":
                continue
            if isinstance(t, dict):
                kind = t.get("kind")
                if kind == "WEED":
                    tasks.append((PRIO_DIG, x, y, ["DIG"], "dig"))
                elif kind == "PLANT":
                    crop = t["crop"]
                    cd = CROPS[crop]
                    age = day - t["planted_day"]
                    if cd["ongoing"]:
                        ready = t.get("yield_units", 0) > 0
                    else:
                        harvest_day = min(t["planted_day"] + cd["max"], last_day)
                        ready = (t.get("yield_units", 0) > 0
                                 and age >= cd["first"] and day >= harvest_day)
                    if ready:
                        tasks.append((PRIO_HARVEST, x, y, ["HARVEST"], "harvest"))
                    elif not t["watered_today"]:
                        tasks.append((PRIO_WATER, x, y, ["WATER"], "water"))
                elif "animal" in t:
                    if not t["fed_today"]:
                        feed_set.add((x, y))
                    if t.get("yield_units", 0) > 0:
                        tasks.append((PRIO_AHARVEST, x, y, ["HARVEST"], "aharvest"))
                    elif t["fed_today"] and not t["cared_today"]:
                        tasks.append((PRIO_CARE, x, y, ["CARE"], "care"))
                    elif t.get("fertilizer_available"):
                        tasks.append((PRIO_CARE, x, y, ["COLLECT_FERTILIZER"], "collect"))
                elif kind in ("COOP", "PASTURE"):
                    for a, d in ANIMALS.items():
                        if d["struct"] != kind:
                            continue
                        if shed.get(a, 0) > 0 or any(inv.get(a, 0) > 0 for inv in inventories):
                            place_map[(x, y)] = a
                            break
            elif t is None and can_plant and (x, y) not in shed_set:
                plant_candidates.append((x, y))

    # Build structures ahead of the herd plan (a couple at a time — each idle
    # structure steals a crop tile until it's occupied). v7: deeper lookahead
    # (+6) so buyers never wait on construction.
    structs_total = len(empty_structs) + herd
    if money > 600 and n_units >= 6 and day < last_day + HERD_BUY_LAST_DAY:
        have_struct = {}
        for _, _, k in empty_structs:
            have_struct[k] = have_struct.get(k, 0) + 1
        for _, _, a, _t in animals:
            k = ANIMAL_STRUCT[a]
            have_struct[k] = have_struct.get(k, 0) + 1
        # Structures needed for owned animals + the next few of the REMAINING
        # plan (so a pasture gets built before the cow arrives, not after).
        want_struct = {}
        for _, _, a, _t in animals:
            k = ANIMAL_STRUCT[a]
            want_struct[k] = want_struct.get(k, 0) + 1
        for a in remaining[:6]:
            k = ANIMAL_STRUCT[a]
            want_struct[k] = want_struct.get(k, 0) + 1
        need_ops = []
        for k, want in want_struct.items():
            for _ in range(max(0, want - have_struct.get(k, 0))):
                need_ops.append(k)
        if need_ops:
            plant_candidates.sort(key=lambda p: _dist(p, shed_tiles[0]))
            for (x, y), k in zip(plant_candidates[-len(need_ops):], need_ops):
                build_map[(x, y)] = k
            if len(need_ops) < len(plant_candidates):
                plant_candidates = plant_candidates[:-len(need_ops)]
            else:
                plant_candidates = []

    plant_candidates.sort(key=lambda p: _dist(p, shed_tiles[0]))
    seed_stock = dict(seeds)
    for x, y in plant_candidates:
        if planted_total >= plot_cap:
            break
        crop = None
        for c in ("MELON", "STRAWBERRY", "TOMATO", "WHEAT", "CARROT"):
            if allowance.get(c, 0) > 0 and seed_stock.get(c, 0) > 0:
                crop = c
                break
        if crop is None:
            break
        tasks.append((PRIO_PLANT, x, y, ["PLANT", crop], "plant"))
        allowance[crop] -= 1
        seed_stock[crop] -= 1
        planted_total += 1

    # ---- assign units -------------------------------------------------------
    task_map = {}
    for prio, x, y, op, tag in tasks:
        cur = task_map.get((x, y))
        if cur is None or prio < cur[0]:
            task_map[(x, y)] = (prio, op, tag)

    units = [me["farmer"]] + list(me["hands"])
    ranch_work = bool(feed_set or place_map or build_map or in_transit
                      or any(t.get("fertilizer_available") or (t["fed_today"] and not t["cared_today"])
                             for _, _, _, t in animals))
    n_ranchers = 0
    if ranch_work:
        # v7: feeding is 2 ops/animal/day over 24 turns — /6 suffices, /3
        # starved crops of labor.
        n_ranchers = min(len(units) - 1, max(2, -(-max(herd_total, structs_total, 1) // 6)))
    rancher_idx = set(range(len(units) - n_ranchers, len(units)))

    claimed, ops = set(), []
    for ui, pos in enumerate(units):
        pos = tuple(pos)
        ukey = "f" if ui == 0 else f"h{ui - 1}"
        inv = inventories[ui] if ui < len(inventories) else {}
        carrying_animal = next((a for a in ANIMALS if inv.get(a, 0) > 0), None)
        carrying_wheat = inv.get("WHEAT", 0) > 0
        is_rancher = ui in rancher_idx or carrying_animal or carrying_wheat

        # --- ranch duty ---
        if is_rancher:
            setup_first = False
            op = _ranch_op(ukey, pos, inv, carrying_animal, carrying_wheat,
                           task_map, claimed, shed_tiles, shed,
                           feed_set, place_map, build_map, setup_first)
            if op is not None:
                ops.append(op)
                continue

        # --- crop duty ---
        target = _S["route"].get(ukey)
        if target not in task_map or target in claimed:
            target = None
            best = None
            for (x, y), (prio, _op, tag) in task_map.items():
                if (x, y) in claimed:
                    continue
                key = (prio, _dist(pos, (x, y)))
                if best is None or key < best[0]:
                    best = (key, (x, y))
            if best is not None:
                target = best[1]
                _S["route"][ukey] = target
        if target is None:
            # idle crop hands pitch in on construction — ranchers are usually
            # busy feeding, and an unbuilt structure stalls the whole herd
            free_builds = [xy for xy in build_map if xy not in claimed]
            if free_builds:
                tgt = min(free_builds, key=lambda xy: _dist(pos, xy))
                if pos == tgt:
                    ops.append(["BUILD_" + build_map[tgt]])
                    claimed.add(tgt)
                else:
                    ops.append(_step_toward(pos, tgt))
                continue
            ops.append(["PASS"])
            continue
        _prio, op, _tag = task_map[target]
        ops.append(op if pos == target else _step_toward(pos, target))
        claimed.add(target)

    return {
        "farmer": ops[0] if ops else ["PASS"],
        "hands": ops[1:],
        "market": market[:10],
    }


def _nearest_shed(pos, shed_tiles):
    return min(shed_tiles, key=lambda s: _dist(pos, s))


def _ranch_op(ukey, pos, inv, carrying_animal, carrying_wheat, task_map, claimed,
              shed_tiles, shed, feed_set, place_map, build_map, setup_first):
    """Action for a ranch-duty unit. Returns None if it should do crops."""
    feeds = feed_set - claimed
    places = {xy: a for xy, a in place_map.items() if xy not in claimed}
    chores = {xy: v for xy, v in task_map.items()
              if v[2] in ("aharvest", "care", "collect") and xy not in claimed}
    builds = {xy: k for xy, k in build_map.items() if xy not in claimed}
    shed_set = set(shed_tiles)
    mine = _S["cargo"].get(ukey)          # feed tiles this unit claimed at pickup

    # Carrying an animal -> deliver to a matching empty structure.
    if carrying_animal:
        want = [xy for xy, a in places.items() if a == carrying_animal]
        if want:
            tgt = min(want, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                return ["PLACE", carrying_animal]
            return _step_toward(pos, tgt)
        # no matching home yet — hover near the shed
        return ["PASS"] if pos in shed_set else _step_toward(pos, _nearest_shed(pos, shed_tiles))

    # Carrying wheat -> feed the nearest hungry animal (own claims first).
    if carrying_wheat:
        targets = feeds
        if mine:
            own = [xy for xy in mine if xy in feed_set]
            if own:
                targets = own
        if targets:
            tgt = min(targets, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                if mine is not None:
                    mine.discard(tgt)
                return ["FEED"]
            return _step_toward(pos, tgt)
        if chores:
            tgt = min(chores, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                return chores[tgt][1]
            return _step_toward(pos, tgt)
        return ["PASS"]

    # Empty-handed: setup-first units do placements/builds before feeding.
    if setup_first:
        if places:
            fetchable = [(xy, a) for xy, a in places.items() if shed.get(a, 0) > 0]
            if fetchable:
                xy, a = min(fetchable, key=lambda p: _dist(pos, p[0]))
                if pos in shed_set:
                    claimed.add(xy)
                    return ["PICKUP", a, 1]
                return _step_toward(pos, _nearest_shed(pos, shed_tiles))
        if builds:
            tgt = min(builds, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                return ["BUILD_" + builds[tgt]]
            return _step_toward(pos, tgt)

    if feeds and shed.get("WHEAT", 0) > 0:
        if pos in shed_set:
            take = min(len(feeds), shed["WHEAT"])
            # remember which tiles we intend to feed; later ranchers see fewer
            chosen = set(sorted(feeds, key=lambda xy: _dist(pos, xy))[:take])
            _S["cargo"][ukey] = chosen
            claimed.update(chosen)
            return ["PICKUP", "WHEAT", take]
        return _step_toward(pos, _nearest_shed(pos, shed_tiles))

    if places:
        # nearest structure whose animal is in the shed
        fetchable = [(xy, a) for xy, a in places.items() if shed.get(a, 0) > 0]
        if fetchable:
            xy, a = min(fetchable, key=lambda p: _dist(pos, p[0]))
            if pos in shed_set:
                claimed.add(xy)
                return ["PICKUP", a, 1]
            return _step_toward(pos, _nearest_shed(pos, shed_tiles))

    if builds:
        tgt = min(builds, key=lambda xy: _dist(pos, xy))
        if pos == tgt:
            claimed.add(tgt)
            return ["BUILD_" + builds[tgt]]
        return _step_toward(pos, tgt)

    if chores:
        tgt = min(chores, key=lambda xy: _dist(pos, xy))
        if pos == tgt:
            claimed.add(tgt)
            return chores[tgt][1]
        return _step_toward(pos, tgt)

    return None  # no ranch work -> do crops


# IMPORTANT: kaggle_environments binds the LAST callable defined in this file
# as the agent (see agent.get_last_callable). Keep this wrapper last.
def agent(obs, config):
    return _agent_impl(obs, config)
