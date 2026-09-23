"""Kaggriculture v9 — mechanics-correct 100K agent.

Key fixes vs v8:
- WATER bonus window from interpreter: window_start=(max+1)//2, +1/day (+2 fertilized), cap max_yield.
  WHEAT base 1 -> 4 watered (6 fertilized). MELON base 1 -> 6 watered. Watering is the income.
- PLANT only from CURRENT seeds (market executes after unit actions; buy_seed phantom wastes trips).
- FERTILIZER must be PICKUPed to inventory before FERTILIZE (was silently no-op).
- HARVEST goes to inventory, auto-drops to shed end-of-day; SELL from shed next turn.
- 2 consecutive unwatered/unfed days = weed/escape. Water/feed daily, no exceptions.
- No SELL caps (market T=100-450; small sales barely move price). Sell everything above reserve.
- Hires are cheap (5/day=$12, 10/day=$143). Hire to match plots once watering works.
- Land ASAP (7k total), coops closest to shed, crops in compact block.
"""

CROPS = {
    "WHEAT":      {"seed": 10,  "first": 2,  "max": 4,  "ongoing": False, "max_yield": 6, "interval": 0},
    "CARROT":     {"seed": 20,  "first": 2,  "max": 3,  "ongoing": False, "max_yield": 4, "interval": 0},
    "TOMATO":     {"seed": 50,  "first": 8,  "max": 8,  "ongoing": True,  "max_yield": 4, "interval": 1},
    "STRAWBERRY": {"seed": 100, "first": 10, "max": 10, "ongoing": True,  "max_yield": 4, "interval": 2},
    "MELON":      {"seed": 80,  "first": 10, "max": 12, "ongoing": False, "max_yield": 6, "interval": 0},
}
for _c, _d in CROPS.items():
    _d["bonus_start"] = (_d["max"] + 1) // 2

ANIMALS = {
    "GOOSE": {"cost": 300, "struct": "COOP",    "first": 4, "interval": 1, "max_held": 4, "product": "EGG"},
    "COW":   {"cost": 400, "struct": "PASTURE", "first": 8, "interval": 2, "max_held": 6, "product": "MILK"},
    "SHEEP": {"cost": 500, "struct": "PASTURE", "first": 6, "interval": 3, "max_held": 6, "product": "WOOL"},
}

LAND_PRICES = [1000, 2000, 4000]
CASH_FLOOR = 150
HERD_BUY_LAST_DAY = 10

_S = {"day": -1, "sold": {}, "route": {}, "cargo": {}}


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


def _nearest_shed(pos, shed_tiles):
    return min(shed_tiles, key=lambda s: _dist(pos, s))


def _crop_targets(day, last_day, plot_cap, money, herd_total):
    t = {}
    if day >= last_day - 4:
        # Only crops that can still mature.
        if day + 4 <= last_day:
            t["WHEAT"] = plot_cap
        if day + 3 <= last_day:
            t["CARROT"] = min(8, plot_cap // 4)
        return t
    # Early: WHEAT for cash + feed.
    t["WHEAT"] = max(6, min(plot_cap, herd_total * 2 + 8))
    # MELON is the money crop: 1 -> 6 units @250 with watering.
    if day + 12 <= last_day and money > 300 and plot_cap >= 6:
        t["MELON"] = min(30, plot_cap - t["WHEAT"])
    # STRAWBERRY ongoing income once rich.
    if day + 10 <= last_day and money > 2500 and plot_cap >= 16:
        t["STRAWBERRY"] = min(8, plot_cap // 4)
        t["WHEAT"] = max(4, t["WHEAT"] - t["STRAWBERRY"])
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
    inventories = private["inventories"]
    turns_per_day = int(config.get("turnsPerDay", 24))
    total_days = int(config.get("episodeSteps", 720) + turns_per_day - 1) // turns_per_day
    last_day = total_days - 1
    shed_tiles = _shed_tiles(board)
    shed_set = set(shed_tiles)

    if _S["day"] != day:
        _S = {"day": day, "sold": {}, "route": {}, "cargo": {}}

    # ---- scan ----
    growing, animals, empty_structs, weeds = {}, [], [], []
    for y, row in enumerate(tiles):
        for x, t in enumerate(row):
            if t == "LOCKED":
                continue
            if isinstance(t, dict):
                kind = t.get("kind")
                if kind == "WEED":
                    weeds.append((x, y))
                elif kind == "PLANT":
                    growing[t["crop"]] = growing.get(t["crop"], 0) + 1
                elif "animal" in t:
                    animals.append((x, y, t["animal"], t))
                elif kind in ("COOP", "PASTURE"):
                    empty_structs.append((x, y, kind))

    planted_total = sum(growing.values())
    unlocked_tiles = sum(1 for row in tiles for t in row if t != "LOCKED")
    n_units = 1 + len(me["hands"])
    herd = len(animals)
    in_transit = 0
    for inv in inventories:
        for a in ANIMALS:
            in_transit += inv.get(a, 0)
    for a in ANIMALS:
        in_transit += shed.get(a, 0)
    herd_total = herd + in_transit
    wheat_reserve = herd_total + 6 if herd_total else 10
    # Crew scales with plots; hires are cheap once watering works.
    plot_cap = min(unlocked_tiles - 4, n_units * 6)
    if money > 4000 and unlocked_tiles > 40:
        crew_target = 12
    elif money > 2000:
        crew_target = 9
    else:
        crew_target = 6

    crop_targets = _crop_targets(day, last_day, plot_cap, money, herd_total)
    allowance = {c: max(0, crop_targets.get(c, 0) - growing.get(c, 0)) for c in crop_targets}
    room_to_grow = max(0, plot_cap - planted_total)

    # ---- market (executes AFTER unit actions: BUY_SEED is for future turns) ----
    market, spend = [], 0

    # 1) HIRE minimal early (need hands to water day 0).
    if day < last_day and hour <= turns_per_day // 2:
        n_h = sum(1 for m in market if m[0] == "HIRE")
        while len(market) < 10 and me["hires_today"] + n_h < crew_target:
            cost = _fib(me["hires_today"] + n_h)
            if money - spend - cost < 2:
                break
            market.append(["HIRE"])
            spend += cost
            n_h += 1

    # 2) BUY_LAND ASAP (reserve budget before seeds).
    n_extra = len(me["unlocked_quadrants"]) - 1
    if n_extra < 3 and day + 6 <= last_day and len(market) < 10:
        if money - spend > LAND_PRICES[n_extra] + 600:
            market.append(["BUY_LAND"])
            spend += LAND_PRICES[n_extra]

    # 3) BUY_SEED for deficits (future planting).
    for crop in ("MELON", "STRAWBERRY", "WHEAT", "CARROT", "TOMATO"):
        if crop not in crop_targets or day + CROPS[crop]["max"] > last_day:
            continue
        if len(market) >= 10:
            break
        deficit = min(crop_targets[crop] - growing.get(crop, 0) - seeds.get(crop, 0), room_to_grow)
        if deficit <= 0:
            continue
        cost = deficit * CROPS[crop]["seed"]
        if money - spend - cost < CASH_FLOOR:
            deficit = max(0, (money - spend - CASH_FLOOR) // CROPS[crop]["seed"])
            cost = deficit * CROPS[crop]["seed"]
        if deficit > 0:
            market.append(["BUY_SEED", crop, deficit])
            spend += cost
            room_to_grow -= deficit

    # 4) BUY_ANIMAL when structures exist. Geese first (fast payback).
    if len(market) < 10 and day < last_day - HERD_BUY_LAST_DAY:
        for a in ("GOOSE", "SHEEP", "COW"):
            if shed.get(a, 0) > 0 or any(inv.get(a, 0) > 0 for inv in inventories):
                continue
            if money - spend < ANIMALS[a]["cost"] + 150:
                continue
            if not any(k == ANIMALS[a]["struct"] for _, _, k in empty_structs):
                continue
            market.append(["BUY_ANIMAL", a, 1])
            spend += ANIMALS[a]["cost"]
            if len(market) >= 10:
                break

    # 5) BUY WHEAT if animals will starve and no harvest coming.
    if herd_total > 0 and shed.get("WHEAT", 0) < wheat_reserve and len(market) < 10:
        need = min(wheat_reserve - shed.get("WHEAT", 0), 6)
        # afford at market price (~25); _commit_unit checks exact funds per unit
        if money - spend > need * 30 + CASH_FLOOR:
            market.append(["BUY_PRODUCT", "WHEAT", need])
            spend += need * 30

    # 6) BUY FERTILIZER to cover bonus windows.
    if day < last_day - 2 and len(market) < 10:
        bonus_plants = sum(
            1 for row in tiles for t in row
            if isinstance(t, dict) and t.get("kind") == "PLANT"
            and (cd := CROPS[t["crop"]]) and cd["bonus_start"] <= day - t["planted_day"] <= cd["max"]
        )
        want = min(8, max(0, bonus_plants - shed.get("FERTILIZER", 0)))
        if want > 0 and money - spend > want * 110 + CASH_FLOOR:
            market.append(["BUY_PRODUCT", "FERTILIZER", want])
            spend += want * 110

    # 7) SELL everything above reserve. No caps.
    dump_all = day >= last_day - 1
    for item in ("EGG", "MILK", "WOOL", "MELON", "STRAWBERRY", "TOMATO", "CARROT", "WHEAT", "FERTILIZER"):
        n = shed.get(item, 0)
        if n <= 0 or item in ("GOOSE", "COW", "SHEEP") or len(market) >= 10:
            continue
        if item == "WHEAT" and not dump_all:
            n = max(0, n - wheat_reserve)
            if n <= 0:
                continue
        if item == "FERTILIZER" and not dump_all:
            n = max(0, n - 4)
            if n <= 0:
                continue
        market.append(["SELL", item, n])

    # ---- tasks (PLANT only from CURRENT seeds) ----
    tasks, plant_candidates, feed_set, place_map, collect_candidates = [], [], set(), {}, set()
    can_plant = hour <= turns_per_day - 2
    seed_stock = dict(seeds)

    for y, row in enumerate(tiles):
        for x, t in enumerate(row):
            if t == "LOCKED":
                continue
            if isinstance(t, dict):
                kind = t.get("kind")
                if kind == "WEED":
                    tasks.append((4, x, y, ["DIG"], "dig"))
                elif kind == "PLANT":
                    crop = t["crop"]
                    cd = CROPS[crop]
                    age = day - t["planted_day"]
                    if t.get("yield_units", 0) > 0 and age >= cd["first"]:
                        if cd["ongoing"] or day >= min(t["planted_day"] + cd["max"], last_day):
                            tasks.append((1, x, y, ["HARVEST"], "harvest"))
                            continue
                    if not t.get("watered_today"):
                        # Bonus-window waters first (prio 0), normal waters prio 2.
                        in_window = cd["bonus_start"] <= age <= cd["max"] if not cd["ongoing"] else True
                        tasks.append((0 if in_window else 2, x, y, ["WATER"], "water"))
                        if in_window and cd["bonus_start"] <= age <= cd["max"] and (
                            shed.get("FERTILIZER", 0) > 0 or any(inv.get("FERTILIZER", 0) > 0 for inv in inventories)
                        ):
                            tasks.append((3, x, y, ["FERTILIZE"], "fertilize"))
                    elif t.get("fertilizer_available"):
                        collect_candidates.add((x, y))
                elif "animal" in t:
                    if not t.get("fed_today"):
                        feed_set.add((x, y))
                    if t.get("yield_units", 0) > 0:
                        tasks.append((1, x, y, ["HARVEST"], "aharvest"))
                    elif t.get("fed_today") and not t.get("cared_today"):
                        tasks.append((3, x, y, ["CARE"], "care"))
                    elif t.get("fertilizer_available"):
                        collect_candidates.add((x, y))
                elif kind in ("COOP", "PASTURE"):
                    for a, d in ANIMALS.items():
                        if d["struct"] != kind:
                            continue
                        if shed.get(a, 0) > 0 or any(inv.get(a, 0) > 0 for inv in inventories):
                            place_map[(x, y)] = a
                            break
            elif t is None and can_plant and (x, y) not in shed_set:
                plant_candidates.append((x, y))

    plant_candidates.sort(key=lambda p: _dist(p, shed_tiles[0]))

    # Structures on closest tiles (they need daily visits too).
    if day < last_day - HERD_BUY_LAST_DAY and money > 300 and len(plant_candidates) > 6:
        want_coop = 4 if day < 8 else 2
        want_past = 2 if day >= 4 else 0
        existing = {}
        for _, _, k in empty_structs:
            existing[k] = existing.get(k, 0) + 1
        for _, _, a, _ in animals:
            k = ANIMALS[a]["struct"]
            existing[k] = existing.get(k, 0) + 1
        for k, want in (("COOP", want_coop), ("PASTURE", want_past)):
            for _ in range(max(0, want - existing.get(k, 0))):
                if plant_candidates:
                    x, y = plant_candidates.pop(0)
                    tasks.append((5, x, y, ["BUILD_" + k], "build"))

    # PLANT from current seeds only.
    for x, y in plant_candidates:
        if planted_total >= plot_cap:
            break
        crop = None
        for c in ("MELON", "STRAWBERRY", "WHEAT", "CARROT", "TOMATO"):
            if allowance.get(c, 0) > 0 and seed_stock.get(c, 0) > 0:
                crop = c
                break
        if crop is None:
            break
        tasks.append((6, x, y, ["PLANT", crop], "plant"))
        allowance[crop] -= 1
        seed_stock[crop] -= 1
        planted_total += 1

    # ---- assign: every unit runs _ranch_op (closest-task, farm priority) ----
    task_map = {}
    for prio, x, y, op, tag in tasks:
        cur = task_map.get((x, y))
        if cur is None or prio < cur[0]:
            task_map[(x, y)] = (prio, op, tag)
    for xy in collect_candidates:
        if xy not in task_map:
            task_map[xy] = (3, ["COLLECT_FERTILIZER"], "collect")

    units = [me["farmer"]] + list(me["hands"])
    claimed, ops = set(), []
    for ui, pos in enumerate(units):
        pos = tuple(pos)
        ukey = "f" if ui == 0 else f"h{ui - 1}"
        inv = inventories[ui] if ui < len(inventories) else {}
        op = _ranch_op(ukey, pos, inv, task_map, claimed, shed_tiles, shed, feed_set, place_map, collect_candidates)
        ops.append(op if op is not None else ["PASS"])

    return {"farmer": ops[0] if ops else ["PASS"], "hands": ops[1:], "market": market[:10]}


def _ranch_op(ukey, pos, inv, task_map, claimed, shed_tiles, shed, feed_set, place_map, collect_candidates):
    shed_set = set(shed_tiles)
    carrying_animal = next((a for a in ANIMALS if inv.get(a, 0) > 0), None)
    carrying_wheat = inv.get("WHEAT", 0) > 0
    carrying_fert = inv.get("FERTILIZER", 0) > 0

    def closest(cands):
        best, best_d = None, None
        for xy, (prio, op, tag) in cands.items():
            if xy in claimed:
                continue
            d = _dist(pos, xy)
            key = (prio, d)
            if best is None or key < best[0]:
                best = ((key), (xy, op))
        return best[1] if best else None

    if carrying_animal:
        want = [xy for xy, a in place_map.items() if a == carrying_animal and xy not in claimed]
        if want:
            tgt = min(want, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                return ["PLACE", carrying_animal]
            return _step_toward(pos, tgt)
        return ["PASS"] if pos in shed_set else _step_toward(pos, _nearest_shed(pos, shed_tiles))

    if carrying_fert:
        cands = {xy: v for xy, v in task_map.items() if v[2] == "fertilize" and xy not in claimed}
        hit = closest(cands)
        if hit:
            (tx, ty), op = hit
            if pos == (tx, ty):
                claimed.add((tx, ty))
                return op
            return _step_toward(pos, (tx, ty))
        # No fertilize target: drop it back if at shed, else keep carrying.
        if pos in shed_set:
            return ["PLACE", "FERTILIZER", inv.get("FERTILIZER", 0)]

    if carrying_wheat:
        feeds = [xy for xy in feed_set if xy not in claimed]
        if feeds:
            tgt = min(feeds, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                return ["FEED"]
            return _step_toward(pos, tgt)
        # Feed done: fall through to harvest/water work while carrying extra wheat.

    # 0) Dedicated builder: first hand builds so coops exist, farmer keeps watering.
    # (Farmer building day-0-hour-0 starves watering and crops die to weeds.)
    build = {xy: v for xy, v in task_map.items() if v[1][0].startswith("BUILD_") and xy not in claimed}
    if build and ukey == "h0":
        tgt = min(build, key=lambda xy: _dist(pos, xy))
        if pos == tgt:
            claimed.add(tgt)
            return build[tgt]
        return _step_toward(pos, tgt)

    # 1) HARVEST ready crops/animals.
    cands = {xy: v for xy, v in task_map.items() if v[2] in ("harvest", "aharvest") and xy not in claimed}
    hit = closest(cands)
    if hit:
        (tx, ty), op = hit
        if pos == (tx, ty):
            claimed.add((tx, ty))
            return op
        return _step_toward(pos, (tx, ty))

    # 2) WATER (bonus-window waters have prio 0 in task_map).
    cands = {xy: v for xy, v in task_map.items() if v[2] == "water" and xy not in claimed}
    hit = closest(cands)
    if hit:
        (tx, ty), op = hit
        if pos == (tx, ty):
            claimed.add((tx, ty))
            return op
        return _step_toward(pos, (tx, ty))

    # 3) FEED animals: grab wheat from shed if needed.
    feeds = [xy for xy in feed_set if xy not in claimed]
    if feeds:
        if inv.get("WHEAT", 0) > 0:
            tgt = min(feeds, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                return ["FEED"]
            return _step_toward(pos, tgt)
        if shed.get("WHEAT", 0) > 0:
            if pos in shed_set:
                take = min(len(feeds), shed["WHEAT"])
                for xy in sorted(feeds, key=lambda q: _dist(pos, q))[:take]:
                    claimed.add(xy)
                _S["cargo"][ukey] = True
                return ["PICKUP", "WHEAT", take]
            return _step_toward(pos, _nearest_shed(pos, shed_tiles))

    # 4) CARE + COLLECT.
    cands = {xy: v for xy, v in task_map.items() if v[2] in ("care", "collect") and xy not in claimed}
    hit = closest(cands)
    if hit:
        (tx, ty), op = hit
        if pos == (tx, ty):
            claimed.add((tx, ty))
            return op
        return _step_toward(pos, (tx, ty))

    # 5) PLANT / DIG.
    cands = {xy: v for xy, v in task_map.items() if v[2] in ("plant", "dig") and xy not in claimed}
    hit = closest(cands)
    if hit:
        (tx, ty), op = hit
        if pos == (tx, ty):
            claimed.add((tx, ty))
            return op
        return _step_toward(pos, (tx, ty))

    # 6) PICKUP animal for placement.
    fetchable = [(xy, a) for xy, a in place_map.items() if xy not in claimed and shed.get(a, 0) > 0]
    if fetchable:
        xy, a = min(fetchable, key=lambda p: _dist(pos, p[0]))
        if pos in shed_set:
            claimed.add(xy)
            return ["PICKUP", a, 1]
        return _step_toward(pos, _nearest_shed(pos, shed_tiles))

    # 7) PICKUP fertilizer for bonus windows.
    cands = {xy: v for xy, v in task_map.items() if v[2] == "fertilize" and xy not in claimed}
    if cands and shed.get("FERTILIZER", 0) > 0 and inv.get("FERTILIZER", 0) <= 0:
        if pos in shed_set:
            return ["PICKUP", "FERTILIZER", min(2, shed["FERTILIZER"])]
        return _step_toward(pos, _nearest_shed(pos, shed_tiles))
    hit = closest(cands)
    if hit:
        (tx, ty), op = hit
        if pos == (tx, ty):
            claimed.add((tx, ty))
            return op
        return _step_toward(pos, (tx, ty))

    # 8) BUILD (lowest: never starve watering for construction).
    build = {xy: v for xy, v in task_map.items() if v[1][0].startswith("BUILD_") and xy not in claimed}
    if build:
        tgt = min(build, key=lambda xy: _dist(pos, xy))
        if pos == tgt:
            claimed.add(tgt)
            return build[tgt]
        return _step_toward(pos, tgt)

    return None


def agent(obs, config):
    return _agent_impl(obs, config)
