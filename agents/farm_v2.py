"""Kaggriculture v2 — expanding mixed-crop farm with a hired crew.

Improvements over the wheat baseline (main.py):
  - Buys land (NE $1k, SW $2k, SE $4k) as cash allows -> up to ~4x the plots.
  - Scales the daily crew with farm size instead of a flat 6 hands.
  - Crop portfolio by phase: fast wheat early, premium melon/strawberry/tomato
    once capital exists, quick wheat/carrot cycles at the end.
  - Throttled selling: premium goods crash quadratically on glut (melon 'sq',
    wool/milk heavy) — we drip them over days while staples sell freely.

State note: module-level _S persists across turns of one episode (the
framework execs this file once per episode) and resets on the next game.
"""

# --- crop data (from the interpreter) --------------------------------------
CROPS = {
    "WHEAT":      {"seed": 10,  "first": 2,  "max": 4,  "ongoing": False},
    "CARROT":     {"seed": 20,  "first": 2,  "max": 3,  "ongoing": False},
    "TOMATO":     {"seed": 50,  "first": 8,  "max": 8,  "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first": 10, "max": 10, "ongoing": True},
    "MELON":      {"seed": 80,  "first": 10, "max": 12, "ongoing": False},
}

# per-day sell caps for glut-sensitive goods; staples (wheat/egg/carrot) sell freely
SELL_CAP = {"TOMATO": 8, "STRAWBERRY": 5, "MELON": 4, "MILK": 4, "WOOL": 4, "FERTILIZER": 10}

MONEY_RESERVE = 60
# Post-purchase buffers: only buy land when this much cash remains after it.
LAND_BUY_BUFFER = [2200, 3500, 6000]   # NE $1k, SW $2k, SE $4k

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


def _crop_targets(day, last_day, n_units, money):
    """Desired plant counts {crop: n} given labor, cash, and season position.

    Premium crops unlock once the operation has cash momentum (first wheat
    revenue has landed); wheat fills whatever labor capacity is left over.
    """
    labor = n_units * 3                       # serviceable plots
    t = {}
    if n_units >= 7 and money > 800:
        t["MELON"] = min(12, labor // 3)      # needs ~12 days to mature
    if n_units >= 8 and money > 1400:
        t["STRAWBERRY"] = min(8, labor // 4)
        t["TOMATO"] = min(5, labor // 6)
    if day >= last_day - 6:                   # endgame: fast cycles only
        t = {"WHEAT": labor, "CARROT": min(8, labor // 4)}
    else:
        t["WHEAT"] = labor - sum(t.values())  # fill remaining capacity
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

    # daily reset of throttle counters + unit routes
    if _S["day"] != day:
        _S = {"day": day, "sold": {}, "route": {}}

    # ---- market orders -----------------------------------------------------
    # Queue priority: HIRE/BUY_LAND first (needed all day), then seeds, then
    # sells (sells can always retry next turn — the shed keeps the stock).
    # Orders are capped at 10/turn; extras are silently dropped.
    market = []

    # How many plots are we tending / planning? Crew scales with that.
    growing = {}
    for row in tiles:
        for t in row:
            if isinstance(t, dict) and t.get("kind") == "PLANT":
                growing[t["crop"]] = growing.get(t["crop"], 0) + 1
    planted_total = sum(growing.values())
    unlocked_tiles = sum(1 for row in tiles for t in row if t != "LOCKED")
    n_units = 1 + len(me["hands"])
    plot_cap = min(unlocked_tiles - 4, 4 + n_units * 3)

    # ~1 hand per 3 plots, plus the farmer. Hire early in the day.
    crew_target = min(13, max(5, -(-max(planted_total, plot_cap) // 3) - 1))
    if day < last_day and hour <= turns_per_day // 3:
        while me["hires_today"] + sum(1 for m in market if m[0] == "HIRE") < crew_target \
                and money > MONEY_RESERVE and len(market) < 10:
            market.append(["HIRE"])

    # Buy land only when the farm is running out of usable tiles.
    n_extra = len(me["unlocked_quadrants"]) - 1
    nearly_full = planted_total + 4 >= unlocked_tiles - 4
    if n_extra < 3 and nearly_full and money > LAND_BUY_BUFFER[n_extra] and len(market) < 10:
        market.append(["BUY_LAND"])

    # Seeds: keep enough to hit today's target mix, but never stockpile beyond
    # the plots we could actually service with the current crew.
    room_to_grow = max(0, plot_cap - planted_total)
    for crop, target in _crop_targets(day, last_day, n_units, money).items():
        cd = CROPS[crop]
        if day + cd["max"] > last_day:
            continue  # couldn't mature before season end
        deficit = min(target - growing.get(crop, 0) - seeds.get(crop, 0),
                      room_to_grow + 2)  # small buffer for same-day planting
        if deficit > 0 and money - deficit * cd["seed"] > MONEY_RESERVE and len(market) < 10:
            market.append(["BUY_SEED", crop, deficit])

    # Throttled selling with remaining slots. Last 2 days: dump everything —
    # inventory can't outlive the season anyway.
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

    # ---- task list ---------------------------------------------------------
    tasks, plant_candidates = [], []
    can_plant = hour <= turns_per_day - 2
    planted_total = sum(growing.values())

    # per-crop remaining plant allowance for today
    allowance = {}
    for crop, target in _crop_targets(day, last_day, n_units, money).items():
        if day + CROPS[crop]["max"] <= last_day:
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

    # Choose what to plant on each empty tile, closest-to-center first.
    plant_candidates.sort(key=lambda p: _dist(p, (board // 2 - 1, board // 2 - 1)))
    for x, y in plant_candidates:
        if planted_total >= plot_cap:
            break
        # pick the crop with the largest remaining allowance that we can afford
        crop = None
        for c in ("MELON", "STRAWBERRY", "TOMATO", "WHEAT", "CARROT"):
            if allowance.get(c, 0) > 0 and seeds.get(c, 0) > 0:
                crop = c
                break
        if crop is None:
            break
        tasks.append((PRIO_PLANT, x, y, ["PLANT", crop]))
        allowance[crop] -= 1
        planted_total += 1

    # ---- assign units ------------------------------------------------------
    # Units keep heading to their routed tile while its task is still pending
    # (kills the walk-a-bit-then-get-diverted thrash).
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
            for (x, y), (prio, op) in task_map.items():
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
        prio, op = task_map[target]
        ops.append(op if pos == target else _step_toward(pos, target))
        claimed.add(target)

    return {
        "farmer": ops[0] if ops else ["PASS"],
        "hands": ops[1:],
        "market": market[:10],
    }
