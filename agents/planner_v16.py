"""planner_v16 — forward-scheduling tape generator.

Computes each unit's whole day at dawn as a list of job phases (a trip to the
shed plus an ordered tile circuit), then executes it blind. This is the tape
generator: run it through tape_record.py to emit a clean 719-step tape.

Phases per unit (built at dawn):
  fetch     -> walk to a shed tile, PICKUP <item> n
  circuit   -> visit each tile, run its queued ops (FEED/CARE/COLLECT/WATER/
               HARVEST/DIG/PLANT/PLACE)
  dump      -> walk to shed, DROP

The market schedule is a fixed plan (hire ramp, land buys, seed top-ups,
dump sells) tuned to a dense crop + mid-size herd engine.
"""
import json, os
from collections import deque

_P = json.loads(os.environ.get("KAG_PARAMS", "{}"))
def _p(k, d): return _P[k] if k in _P else d

BOARD = 10
TURNS = 24
SHED_TILES = [(4, 4), (5, 4), (4, 5), (5, 5)]
LAST_STEP = 718
MAX_ORDERS = 10

CROPS = {"WHEAT": 10, "CARROT": 20, "TOMATO": 50, "STRAWBERRY": 100, "MELON": 80}
FIRST_YIELD = {"WHEAT": 2, "CARROT": 2, "TOMATO": 8, "STRAWBERRY": 10, "MELON": 10}
ONGOING = {"TOMATO", "STRAWBERRY"}
ANIMAL_COST = {"GOOSE": 300, "COW": 400, "SHEEP": 500}
STRUCT_FOR = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON",
            "EGG", "MILK", "WOOL", "FERTILIZER")

# --- plan knobs ---
CREW_RAMP = [int(x) for x in _p("crew_ramp", "3,5,7,9,10,10").split(",")]  # hands/day
CREW_MAX = int(_p("crew", 10))
HERD_TARGET = int(_p("herd", 16))
HERD_BUYS = {"COW": _p("cow", 9), "SHEEP": _p("sheep", 4), "GOOSE": _p("goose", 3)}
PLOTS = int(_p("plots", 55))
CROP_MIX = {"STRAWBERRY": 0.55, "WHEAT": 0.27, "CARROT": 0.11, "TOMATO": 0.07}
LAND_DAYS = [int(x) for x in _p("land_days", "8,12,16").split(",")]
SELL_FROM = int(_p("sell_from", 4))
WHEAT_RESERVE = int(_p("wheat_res", 60))
RANCH = bool(int(_p("ranch", 1)))          # 0 = crops-only mode

_S = {"day": -1, "plans": {}, "bought_animals": {}, "built": {}}


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _step(pos, tgt):
    x, y = pos; tx, ty = tgt
    if x != tx: return ["EAST" if tx > x else "WEST"]
    return ["SOUTH" if ty > y else "NORTH"]


def _shed(pos):
    return min(SHED_TILES, key=lambda p: _dist(pos, p))


def _scan(tiles):
    plants, weeds, structs, animals, empty = {}, [], {}, {}, []
    for y, row in enumerate(tiles):
        for x, t in enumerate(row):
            if t == "LOCKED" or (x, y) in [tuple(s) for s in SHED_TILES]:
                continue
            if isinstance(t, dict):
                k = t.get("kind")
                if k == "PLANT": plants[(x, y)] = t
                elif k == "WEED": weeds.append((x, y))
                elif "animal" in t: animals[(x, y)] = t
                elif k in ("COOP", "PASTURE"): structs[(x, y)] = k
            elif t is None:
                empty.append((x, y))
    return plants, weeds, structs, animals, empty


def _nn_chain(start, tiles):
    """Order a tile set by nearest-neighbour from start pos."""
    out, cur, rest = [], start, list(tiles)
    while rest:
        nxt = min(rest, key=lambda p: _dist(cur, p))
        out.append(nxt); cur = nxt; rest.remove(nxt)
    return out


def _chunk(lst, n):
    if not lst: return []
    k = -(-len(lst) // n)
    return [lst[i:i + k] for i in range(0, len(lst), k)]


def _crop_wanted(day, n_unlocked):
    cap = min(PLOTS, max(0, n_unlocked - 8))
    if day >= 26:
        return {"WHEAT": cap}
    if day < 8:
        # fast crops fund the ramp — wheat yields day 2, carrot day 2
        return {"WHEAT": int(cap * 0.55), "CARROT": int(cap * 0.45)}
    # steady state: ongoing strawberry engine + wheat feed base + carrot mix
    return {"STRAWBERRY": int(cap * 0.45), "WHEAT": int(cap * 0.30),
            "CARROT": int(cap * 0.15), "TOMATO": int(cap * 0.10)}


def _plan_day(obs):
    """Return {unit_index: deque of job phases}."""
    me = obs["farms"][obs["player"]]
    tiles = me["tiles"]
    day = obs["day"]
    units = [tuple(me["farmer"])] + [tuple(h) for h in me["hands"]]
    seeds = obs["private"]["seeds"]
    shed = obs["private"]["shed"]
    plants, weeds, structs, animals, empty = _scan(tiles)
    # Plan for today's expected crew (ramped), not just whoever's hired yet —
    # at dawn only the farmer exists; hand slots fill as HIRE orders land.
    n_units = max(len(units), 1 + CREW_RAMP[min(day, len(CREW_RAMP) - 1)])
    spawn = (5, 5)
    pos_of = [units[i] if i < len(units) else spawn for i in range(n_units)]

    # --- ranch jobs ---
    feed_tiles, tend_tiles = [], []
    for xy, t in animals.items():
        if not t["fed_today"]:
            feed_tiles.append(xy)
        tend_ops = []
        if t.get("yield_units", 0) > 0: tend_ops.append(["HARVEST"])
        if t["fed_today"] and not t["cared_today"]: tend_ops.append(["CARE"])
        if t.get("fertilizer_available"): tend_ops.append(["COLLECT_FERTILIZER"])
        if tend_ops:
            tend_tiles.append((xy, tend_ops))
    # unplaced animals: carry from shed to empty struct
    place_jobs = []   # (animal, struct_xy)
    for xy, kind in structs.items():
        for a in ANIMAL_COST:
            if STRUCT_FOR[a] == kind and shed.get(a, 0) > 0:
                place_jobs.append((a, xy))
                break
    # Structures build ahead of the herd on a schedule — animals only arrive
    # once homes exist, so build toward the herd target whether or not buys
    # have landed yet.
    wanted_structs = []
    n_pastures = sum(1 for k in structs.values() if k == "PASTURE") + \
        sum(1 for t in animals.values() if t.get("animal") != "GOOSE")
    n_coops = sum(1 for k in structs.values() if k == "COOP") + \
        sum(1 for t in animals.values() if t.get("animal") == "GOOSE")
    want_past = min(int(HERD_TARGET * (HERD_BUYS["COW"] + HERD_BUYS["SHEEP"]) /
                        max(1, HERD_TARGET)) + 1, HERD_TARGET)
    want_coop = HERD_BUYS["GOOSE"]
    # pace construction over days 1-8
    build_through = max(0, min(HERD_TARGET, day))
    for _ in range(0 if not RANCH else max(0, min(want_past, build_through) - n_pastures)):
        wanted_structs.append("PASTURE")
    for _ in range(0 if not RANCH else max(0, min(want_coop, build_through // 2) - n_coops)):
        wanted_structs.append("COOP")

    # --- field jobs ---
    water_tiles, dig_tiles, harvest_tiles = [], [], []
    for xy, t in plants.items():
        if t.get("yield_units", 0) > 0 and (day - t["planted_day"]) >= FIRST_YIELD.get(t["crop"], 99):
            harvest_tiles.append(xy)
        if not t["watered_today"]:
            water_tiles.append(xy)
    dig_tiles = list(weeds)
    # plant empties up to crop targets (densest near shed first)
    crop_t = _crop_wanted(day, sum(1 for r in tiles for c in r if c != "LOCKED"))
    planted_ct = {}
    for t in plants.values():
        planted_ct[t["crop"]] = planted_ct.get(t["crop"], 0) + 1
    plant_jobs = []  # (xy, crop)
    avail = dict(seeds)
    for xy in sorted(empty, key=lambda p: _dist(p, (4, 4))):
        if len(plant_jobs) + len(plants) >= min(PLOTS, max(0, sum(1 for r in tiles for c in r if c != "LOCKED") - 10)):
            break
        for c, w in CROP_MIX.items():
            want = crop_t.get(c, 0)
            if want > planted_ct.get(c, 0) + sum(1 for _, pc in plant_jobs if pc == c) and avail.get(c, 0) > 0:
                plant_jobs.append((xy, c))
                avail[c] -= 1
                break

    # --- build phases ---
    # Split units: first n_ranch do animal work; the rest do field work.
    n_ranch = 0 if not RANCH else min(n_units - 1, max(1, -(-max(len(animals), len(feed_tiles) + len(tend_tiles), 1) // 4)))
    ranchers = list(range(n_units - n_ranch, n_units))
    field = list(range(0, n_units - n_ranch))

    plans = {i: deque() for i in range(n_units)}

    # ranch: feeder units carry wheat then circuit animals
    feed_chunks = _chunk(feed_tiles, max(1, n_ranch))
    tend_chunks = _chunk(tend_tiles, max(1, n_ranch))
    place_chunks = _chunk(place_jobs, max(1, n_ranch))
    build_chunks = _chunk(wanted_structs, max(1, n_ranch))
    for ri, ui in enumerate(ranchers):
        q = plans[ui]
        fc = feed_chunks[ri] if ri < len(feed_chunks) else []
        if fc:
            need = min(len(fc) * 2, shed.get("WHEAT", 0))
            if need > 0:
                q.append({"type": "fetch", "item": "WHEAT", "n": need})
            q.append({"type": "circuit", "tiles": _nn_chain(_shed(pos_of[ui]), fc),
                      "ops": {xy: [["FEED"]] for xy in fc}})
        bc = build_chunks[ri] if ri < len(build_chunks) else []
        for kind in bc:
            if empty:
                xy = min(empty, key=lambda p: _dist(p, _shed(pos_of[ui])))
                q.append({"type": "circuit", "tiles": [xy], "ops": {xy: [["BUILD_" + kind]]}})
        pc = place_chunks[ri] if ri < len(place_chunks) else []
        for a, sxy in pc:
            q.append({"type": "fetch", "item": a, "n": 1})
            q.append({"type": "circuit", "tiles": [sxy], "ops": {sxy: [["PLACE", a]]}})
        tc = tend_chunks[ri] if ri < len(tend_chunks) else []
        if tc:
            q.append({"type": "circuit", "tiles": _nn_chain(pos_of[ui], [xy for xy, _ in tc]),
                      "ops": {xy: ops for xy, ops in tc}})
        q.append({"type": "dump"})

    # field: WATER first (daily deadline — miss 2 days and the plant dies),
    # then harvest, dig, plant. Starving water for any other op kills the crop
    # engine outright.
    digs = _chunk(dig_tiles, max(1, len(field)))
    harvs = _chunk(_nn_chain((4, 4), harvest_tiles), max(1, len(field)))
    waters = _chunk(_nn_chain((4, 4), water_tiles), max(1, len(field)))
    plants_ = _chunk(plant_jobs, max(1, len(field)))
    for fi, ui in enumerate(field):
        q = plans[ui]
        wv = waters[fi] if fi < len(waters) else []
        if wv:
            q.append({"type": "circuit", "tiles": wv,
                      "ops": {xy: [["WATER"]] for xy in wv}})
        hv = harvs[fi] if fi < len(harvs) else []
        if hv:
            q.append({"type": "circuit", "tiles": hv,
                      "ops": {xy: [["HARVEST"]] for xy in hv}})
        dd = digs[fi] if fi < len(digs) else []
        if dd:
            q.append({"type": "circuit", "tiles": _nn_chain(pos_of[ui], dd),
                      "ops": {xy: [["DIG"]] for xy in dd}})
        pj = plants_[fi] if fi < len(plants_) else []
        if pj:
            # PLANT then WATER same visit — planting day already counts as the
            # first unwatered day, so a dry plant dies before tomorrow's pass.
            q.append({"type": "circuit", "tiles": _nn_chain(pos_of[ui], [xy for xy, _ in pj]),
                      "ops": {xy: [["PLANT", c], ["WATER"]] for xy, c in pj}})
        q.append({"type": "dump"})

    return plans


def _market(obs, shed):
    me = obs["farms"][obs["player"]]
    day = obs["day"]; hour = obs["hour"]
    money = me["money"]; seeds = obs["private"]["seeds"]
    orders, spend = [], 0
    # Payroll floor: reserve tomorrow's wages before discretionary spend.
    crew_today = len(me["hands"]) + me.get("hires_today", 0)
    payroll = 0; a_, b_ = 1, 1
    for _ in range(crew_today): payroll += a_; a_, b_ = b_, a_ + b_
    floor = payroll + 100
    hires = me.get("hires_today", 0)
    want = (CREW_RAMP[min(day, len(CREW_RAMP) - 1)] if day < 30 else CREW_MAX)
    if hour >= 20: want = 0
    a, b = 1, 1
    for _ in range(hires): a, b = b, a + b
    while hires < want and len(orders) < MAX_ORDERS:
        c = a
        if money - spend - c < floor: break
        orders.append(["HIRE"]); spend += c; hires += 1; a, b = b, a + b
    # land buys on schedule — only with real surplus
    if day in LAND_DAYS and len(me["unlocked_quadrants"]) < 4:
        price = [1000, 2000, 4000][len(me["unlocked_quadrants"]) - 1]
        if money - spend - price > floor + 1000 and len(orders) < MAX_ORDERS:
            orders.append(["BUY_LAND"]); spend += price
    # seeds for today's plan — keep a full stock; a bare field is lost revenue
    for c, want in _crop_wanted(day, 100).items():
        have = seeds.get(c, 0)
        if have < want and len(orders) < MAX_ORDERS:
            n = min(want - have + 4, 16)
            cst = n * CROPS[c]
            if money - spend - cst > floor:
                orders.append(["BUY_SEED", c, n]); spend += cst
    # wheat feed reserve — only once animals exist to feed
    n_animals = sum(1 for r in me["tiles"] for t in r
                    if isinstance(t, dict) and "animal" in t)
    if n_animals > 0 and shed.get("WHEAT", 0) < n_animals * 2 and len(orders) < MAX_ORDERS:
        n = min(n_animals * 2 - shed.get("WHEAT", 0), 20)
        c = n * 25
        if money - spend - c > floor:
            orders.append(["BUY_PRODUCT", "WHEAT", n]); spend += c
    # animals only once their structures exist (else they sit in the shed)
    n_structs = sum(1 for r in me["tiles"] for t in r
                    if isinstance(t, dict) and t.get("kind") in ("COOP", "PASTURE"))
    if RANCH and n_structs > 0 and day >= 3:
        for a, target in HERD_BUYS.items():
            have = _S["bought_animals"].get(a, 0)
            placed = sum(1 for r in me["tiles"] for t in r
                         if isinstance(t, dict) and t.get("animal") == a)
            want = min(target - have - placed, 2)
            if want > 0 and len(orders) < MAX_ORDERS:
                c = want * ANIMAL_COST[a]
                if money - spend - c > floor + 300:
                    orders.append(["BUY_ANIMAL", a, want]); spend += c
                    _S["bought_animals"][a] = have + want
    # dump sells (not wheat/animals needed for herd)
    if day >= SELL_FROM:
        for item, n in list(shed.items()):
            if item in ANIMAL_COST or item == "WHEAT" or n <= 0 or len(orders) >= MAX_ORDERS:
                continue
            orders.append(["SELL", item, n])
    return orders


def _agent_impl(obs, config=None):
    step = int(obs.get("step", obs["day"] * TURNS + obs["hour"]))
    me = obs["farms"][obs["player"]]
    shed = obs["private"]["shed"]
    units = [tuple(me["farmer"])] + [tuple(h) for h in me["hands"]]
    tiles = me["tiles"]

    if step >= 648:
        return {"farmer": ["PASS"], "hands": [],
                "market": [["SELL", i, n] for i, n in shed.items()
                           if n > 0 and i != "WHEAT"][:MAX_ORDERS]}

    # New episode reset — _S is module-global and survives across games.
    if step == 0 or obs["day"] < _S["day"]:
        _S.update({"day": -1, "plans": {}, "bought_animals": {}, "built": {}})

    # Plan once at dawn; hands hired later today idle until tomorrow's plan.
    if obs["day"] != _S["day"]:
        _S["day"] = obs["day"]
        _S["plans"] = _plan_day(obs)

    market = _market(obs, shed)
    invs = obs["private"].get("inventories") or []
    # Reactive fallback task map — units whose plan is exhausted pick up the
    # nearest remaining work instead of idling.
    claimed = set()
    task_map = _task_map(obs, shed)

    ops = []
    for i, pos in enumerate(units):
        plan = _S["plans"].get(i)
        acted = None
        # inventory-full guard: carrying ~4 items, dump before more work
        inv = invs[i] if i < len(invs) else {}
        if sum(v for v in inv.values() if isinstance(v, int)) >= 4:
            if pos in [tuple(s) for s in SHED_TILES]:
                acted = ["DROP"]
            else:
                acted = _step(pos, _shed(pos))
        while plan and acted is None:
            job = plan[0]
            if job["type"] == "fetch":
                if pos in [tuple(s) for s in SHED_TILES]:
                    acted = ["PICKUP", job["item"], job["n"]]
                    plan.popleft()
                else:
                    acted = _step(pos, _shed(pos))
            elif job["type"] == "dump":
                if pos in [tuple(s) for s in SHED_TILES]:
                    acted = ["DROP"]
                    plan.popleft()
                else:
                    acted = _step(pos, _shed(pos))
            else:  # circuit
                if not job["tiles"]:
                    plan.popleft()
                    continue
                # Prune stale targets: skip tiles whose ops are all invalid
                # now, so we don't waste turns walking to dead work.
                while job["tiles"]:
                    tgt = job["tiles"][0]
                    tile_ops = job["ops"].get(tgt) or []
                    if not tile_ops:
                        job["tiles"].pop(0)
                        continue
                    if not any(_op_valid(c, tiles, tgt, obs["private"]["seeds"], shed, obs["day"]) for c in tile_ops):
                        job["tiles"].pop(0)
                        job["ops"].pop(tgt, None)
                        continue
                    break
                if not job["tiles"]:
                    plan.popleft()
                    continue
                tgt = job["tiles"][0]
                if pos != tgt:
                    # opportunistic: service the tile we're standing on
                    t = tiles[pos[1]][pos[0]]
                    side = _side_op(t, obs["private"]["seeds"], obs["day"])
                    acted = side if side else _step(pos, tgt)
                else:
                    tile_ops = job["ops"].get(tgt) or []
                    op = None
                    while tile_ops:
                        cand = tile_ops.pop(0)
                        if _op_valid(cand, tiles, pos, obs["private"]["seeds"], shed, obs["day"]):
                            op = cand; break
                    if op is None:
                        job["tiles"].pop(0)
                        continue
                    acted = op
                    if not tile_ops:
                        job["tiles"].pop(0)
        # Reactive fallback: plan exhausted -> nearest unclaimed task anywhere.
        if acted is None:
            best = None
            for xy, (prio, op) in task_map.items():
                if xy in claimed:
                    continue
                key = (_dist(pos, xy), prio)
                if best is None or key < best[0]:
                    best = (key, xy)
            if best is not None:
                tgt = best[1]
                claimed.add(tgt)
                if pos == tgt:
                    acted = task_map[tgt][1]
                else:
                    acted = _step(pos, tgt)
        ops.append(acted or ["PASS"])
    return {"farmer": ops[0], "hands": ops[1:], "market": market}


def _task_map(obs, shed):
    """Global pending-work map for the reactive fallback (prio -> op per tile)."""
    me = obs["farms"][obs["player"]]
    day = obs["day"]
    seeds = obs["private"]["seeds"]
    out = {}
    plants, weeds, structs, animals, empty = _scan(me["tiles"])
    for xy, t in plants.items():
        if t.get("yield_units", 0) > 0 and (day - t["planted_day"]) >= FIRST_YIELD.get(t["crop"], 99):
            out[xy] = (0, ["HARVEST"])
        elif not t["watered_today"]:
            out[xy] = (1, ["WATER"])
    for xy in weeds:
        out[xy] = (2, ["DIG"])
    for xy, t in animals.items():
        if t.get("yield_units", 0) > 0:
            out[xy] = (0, ["HARVEST"])
        elif not t["fed_today"]:
            out[xy] = (1, ["FEED"])
        elif t["fed_today"] and not t["cared_today"]:
            out[xy] = (3, ["CARE"])
        elif t.get("fertilizer_available"):
            out[xy] = (3, ["COLLECT_FERTILIZER"])
    crop_t = _crop_wanted(day, sum(1 for r in me["tiles"] for c in r if c != "LOCKED"))
    planted_ct = {}
    for t in plants.values():
        planted_ct[t["crop"]] = planted_ct.get(t["crop"], 0) + 1
    for xy in empty:
        for c, want in crop_t.items():
            if planted_ct.get(c, 0) < want and seeds.get(c, 0) > 0:
                out[xy] = (4, ["PLANT", c])
                break
    return out


def _side_op(t, seeds, day):
    if isinstance(t, dict):
        if t.get("kind") == "WEED": return ["DIG"]
        if t.get("kind") == "PLANT":
            if t.get("yield_units", 0) > 0 and (day - t["planted_day"]) >= FIRST_YIELD.get(t["crop"], 99):
                return ["HARVEST"]
            if not t["watered_today"]: return ["WATER"]
    return None


def _op_valid(op, tiles, pos, seeds, shed, day):
    x, y = pos
    t = tiles[y][x]
    k = t.get("kind") if isinstance(t, dict) else None
    if op[0] == "WATER": return k == "PLANT" and not t["watered_today"]
    if op[0] == "HARVEST":
        if not isinstance(t, dict) or (t.get("yield_units", 0) or 0) <= 0:
            return False
        if t.get("kind") == "PLANT":
            return (day - t["planted_day"]) >= FIRST_YIELD.get(t["crop"], 99)
        return True
    if op[0] == "DIG": return k in ("WEED", "PLANT")
    if op[0] == "PLANT": return t is None and seeds.get(op[1], 0) > 0
    if op[0] == "FEED": return "animal" in t if isinstance(t, dict) else False
    if op[0] == "CARE": return isinstance(t, dict) and "animal" in t and t["fed_today"] and not t["cared_today"]
    if op[0] == "COLLECT_FERTILIZER": return isinstance(t, dict) and t.get("fertilizer_available")
    if op[0] in ("BUILD_COOP", "BUILD_PASTURE"): return t is None
    if op[0] == "PLACE": return isinstance(t, dict) and t.get("kind") in ("COOP", "PASTURE")
    return True


def agent(obs, config=None):
    try:
        return _agent_impl(obs, config)
    except Exception:
        me = obs["farms"][obs["player"]]
        return {"farmer": ["PASS"], "hands": [["PASS"] for _ in me["hands"]], "market": []}
