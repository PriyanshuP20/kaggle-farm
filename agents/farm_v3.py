"""Kaggriculture v3 — disciplined expanding farm.

Lessons burned in from v2's collapse:
  - Payroll first, seeds second, land third. A crew that can't be afforded is
    worse than none: unpaid days -> dead crops -> weeds -> death spiral.
  - Never plant more than the crew can water TODAY (a plant that misses water
    two consecutive nights turns into a weed — sunk seed + a DIG chore).
  - Hires keep going even when nearly broke (fib costs are tiny for the first
    few); only seeds/land respect the cash floor.
  - Premium crops (melon/strawberry) wait for real cash momentum; wheat and
    eggs-like staples carry the early game.

One unit services ~3-4 plots/day including travel. Crew is sized to what is
actually planted, not the tile count.
"""

CROPS = {
    "WHEAT":      {"seed": 10,  "first": 2,  "max": 4,  "ongoing": False},
    "CARROT":     {"seed": 20,  "first": 2,  "max": 3,  "ongoing": False},
    "TOMATO":     {"seed": 50,  "first": 8,  "max": 8,  "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first": 10, "max": 10, "ongoing": True},
    "MELON":      {"seed": 80,  "first": 10, "max": 12, "ongoing": False},
}
LAND_PRICES = [1000, 2000, 4000]  # NE, SW, SE

# Daily sell caps for glut-sensitive goods (staples sell freely).
SELL_CAP = {"TOMATO": 8, "STRAWBERRY": 6, "MELON": 6, "MILK": 5, "WOOL": 5, "FERTILIZER": 12}

CASH_FLOOR = 120          # seeds/land never spend below this
CREW_MAX = 12
PLOTS_PER_UNIT = 3        # watering capacity per unit per day

_S = {"day": -1, "sold": {}, "route": {}}

PRIO_HARVEST, PRIO_WATER, PRIO_DIG, PRIO_PLANT = 0, 1, 2, 3


def _shed_tiles(board):
    c = board // 2
    return {(c - 1, c - 1), (c, c - 1), (c - 1, c), (c, c)}


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


def _crop_targets(day, last_day, n_units, money):
    """{crop: desired live plants} — premiums gated on crew size + cash."""
    labor = n_units * PLOTS_PER_UNIT
    t = {}
    if n_units >= 6 and money > 700:
        t["MELON"] = min(10, labor // 3)
    if n_units >= 8 and money > 1500:
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


def agent(obs, config):
    global _S
    me = obs["farms"][obs["player"]]
    private = obs["private"]
    tiles = me["tiles"]
    board = len(tiles)
    day, hour = obs["day"], obs["hour"]
    money = me["money"]
    seeds = private["seeds"]
    shed = private["shed"]
    turns_per_day = int(config.get("turnsPerDay", 24))
    total_days = int(config.get("episodeSteps", 720) + turns_per_day - 1) // turns_per_day
    last_day = total_days - 1
    shed_tiles = _shed_tiles(board)

    if _S["day"] != day:
        _S = {"day": day, "sold": {}, "route": {}}

    growing = {}
    for row in tiles:
        for t in row:
            if isinstance(t, dict) and t.get("kind") == "PLANT":
                growing[t["crop"]] = growing.get(t["crop"], 0) + 1
    planted_total = sum(growing.values())
    unlocked_tiles = sum(1 for row in tiles for t in row if t != "LOCKED")
    n_units = 1 + len(me["hands"])
    plot_cap = min(unlocked_tiles - 4, n_units * PLOTS_PER_UNIT)

    # ---- market ------------------------------------------------------------
    market = []
    spend = 0  # track queued spend against live money

    # 1) Crew: ~1 hand per 3 plots plus the farmer. Always affordable early
    #    (fib(0)=1); we keep going while money > a token floor.
    crew_target = min(CREW_MAX, max(4, -(-planted_total // 3)))
    if day < last_day and hour <= turns_per_day // 2:
        hires_queued = 0
        while True:
            k = me["hires_today"] + hires_queued
            if k >= crew_target:
                break
            cost = _fib(k)
            if money - spend - cost < 2 or len(market) >= 10:
                break
            market.append(["HIRE"])
            spend += cost
            hires_queued += 1

    # 2) Seeds for today's crop mix, bounded by watering headroom.
    room_to_grow = max(0, plot_cap - planted_total)
    for crop, target in _crop_targets(day, last_day, n_units, money).items():
        deficit = min(target - growing.get(crop, 0) - seeds.get(crop, 0),
                      room_to_grow)
        if deficit <= 0 or len(market) >= 10:
            continue
        cost = deficit * CROPS[crop]["seed"]
        if money - spend - cost < CASH_FLOOR:
            deficit = max(0, (money - spend - CASH_FLOOR) // CROPS[crop]["seed"])
            cost = deficit * CROPS[crop]["seed"]
        if deficit > 0:
            market.append(["BUY_SEED", crop, deficit])
            spend += cost

    # 3) Land only when nearly out of plantable tiles and well-buffered.
    n_extra = len(me["unlocked_quadrants"]) - 1
    if (n_extra < 3 and planted_total >= unlocked_tiles - 8
            and money - spend > LAND_PRICES[n_extra] + 1500
            and day + 8 <= last_day and len(market) < 10):
        market.append(["BUY_LAND"])

    # 4) Sells last — leftovers retry next turn.
    dump_all = day >= last_day - 1
    for item, n in shed.items():
        if n <= 0 or item in ("GOOSE", "COW", "SHEEP") or len(market) >= 10:
            continue
        cap = SELL_CAP.get(item)
        allowed = n if (cap is None or dump_all) else max(0, cap - _S["sold"].get(item, 0))
        sell_n = min(n, allowed)
        if sell_n > 0:
            market.append(["SELL", item, sell_n])
            _S["sold"][item] = _S["sold"].get(item, 0) + sell_n

    # ---- tasks -------------------------------------------------------------
    tasks, plant_candidates = [], []
    can_plant = hour <= turns_per_day - 2

    allowance = {}
    for crop, target in _crop_targets(day, last_day, n_units, money).items():
        allowance[crop] = max(0, target - growing.get(crop, 0))

    for y, row in enumerate(tiles):
        for x, t in enumerate(row):
            if t == "LOCKED":
                continue
            if isinstance(t, dict):
                kind = t.get("kind")
                if kind == "WEED":
                    tasks.append((PRIO_DIG, x, y, ["DIG"]))
                elif kind == "PLANT":
                    crop = t["crop"]
                    cd = CROPS[crop]
                    age = day - t["planted_day"]
                    if cd["ongoing"]:
                        ready = t.get("yield_units", 0) > 0
                    else:
                        harvest_day = min(t["planted_day"] + cd["max"], last_day)
                        ready = (t.get("yield_units", 0) > 0
                                 and age >= cd["first"]
                                 and day >= harvest_day)
                    if ready:
                        tasks.append((PRIO_HARVEST, x, y, ["HARVEST"]))
                    elif not t["watered_today"]:
                        tasks.append((PRIO_WATER, x, y, ["WATER"]))
            elif t is None and can_plant and (x, y) not in shed_tiles:
                plant_candidates.append((x, y))

    # Fill empty plots nearest the shed corner first (short daily water runs).
    plant_candidates.sort(key=lambda p: _dist(p, (board // 2 - 1, board // 2 - 1)))
    seed_stock = dict(seeds)  # decrement as we queue; interpreter drops ALL
                              # PLANTs for a crop if demand exceeds stock
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
        tasks.append((PRIO_PLANT, x, y, ["PLANT", crop]))
        allowance[crop] -= 1
        seed_stock[crop] -= 1
        planted_total += 1

    # ---- assign units (sticky routes) ---------------------------------------
    task_map = {}
    for prio, x, y, op in tasks:
        cur = task_map.get((x, y))
        if cur is None or prio < cur[0]:
            task_map[(x, y)] = (prio, op)

    units = [me["farmer"]] + list(me["hands"])
    claimed, ops = set(), []
    for ui, pos in enumerate(units):
        pos = tuple(pos)
        ukey = "f" if ui == 0 else f"h{ui - 1}"
        target = _S["route"].get(ukey)
        if target not in task_map or target in claimed:
            target = None
            best = None
            for (x, y), (prio, _op) in task_map.items():
                if (x, y) in claimed:
                    continue
                key = (prio, _dist(pos, (x, y)))
                if best is None or key < best[0]:
                    best = (key, (x, y))
            if best is not None:
                target = best[1]
                _S["route"][ukey] = target
        if target is None:
            ops.append(["PASS"])
            continue
        _prio, op = task_map[target]
        ops.append(op if pos == target else _step_toward(pos, target))
        claimed.add(target)

    return {
        "farmer": ops[0] if ops else ["PASS"],
        "hands": ops[1:],
        "market": market[:10],
    }
