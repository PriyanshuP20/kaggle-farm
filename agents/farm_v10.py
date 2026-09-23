"""Kaggriculture v10 — cow/sheep pasture ranch, rebuilt from top-agent replays.

What changed vs v9 (learned reviewing a $163K and a $76K winning episode):

  - PASTURES ONLY. Cows >> sheep >> geese. Milk rides town drain (3 shop
    types + center eat ~19+/day) so its price ratchets $160 -> ~$307; wool
    ~$150-230; eggs are pinned at ~$51 forever. A cared cow is ~$420/day
    late-game, a goose ~$100. Geese are a trap — v10 buys zero of them.
  - EVERY animal gets FEED + CARE + COLLECT_FERTILIZER daily. CARE banks +1
    per fed day into the next production (roughly doubles milk/wool output);
    fertilizer sells ~$60-100/animal/day on top, or doubles crop yield when
    spent on FERTILIZE instead.
  - All-in opening: animals are bought from day 0 and kept buying while cash
    allows (winners ran at ~$0 days 1-11). Payback: a cow placed by day 10
    returns ~10x its $400 cost.
  - Feed logistics: hands spawn ON shed tiles each morning, so a feeder does
    PICKUP wheat -> hop the pasture cluster feeding animals (1 wheat each);
    cargo-free keepers sweep the cluster doing CARE/COLLECT/HARVEST. Chores
    chain on one tile across turns — feed, then care, then collect.
  - Crops: heavy wheat (feed + ratcheting price), strawberry mid-game (4 shop
    types drain it -> price climbs), melon early only (NO town demand — sell
    it the day it harvests, it never recovers from a glut), tomato/carrot
    filler. Everything liquidates day 29.
  - Labor is cheap: 6 hires/day = $20, 11/day = $232. Hires go out at hour 0
    so hands work the whole day.
  - Land: NE ~day 7, SW ~day 10; SE only if the farm genuinely fills 75 tiles.

Market orders still run hire -> animals -> seeds -> feed -> land -> sells
(sells last so leftovers retry next turn; the 10-order cap recycles daily).
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
PRODUCTS = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER"]

import os, json
# All tunables live in _P — tune.py sweeps them via the KAG_PARAMS env var.
_P = json.loads(os.environ.get("KAG_PARAMS", "{}"))
def _p(k, d):
    return _P[k] if k in _P else d

# ---- crew ------------------------------------------------------------------
CREW_MAX = int(_p("crew_max", 10))        # hands/day; 11 hires = $232/day
CREW_MIN = int(_p("crew_min", 5))
HIRE_LAST_HOUR = int(_p("hire_last_hour", 10))

# ---- ranch ------------------------------------------------------------------
HERD_TARGET = int(_p("herd_target", 16))
BUY_PER_TURN = int(_p("buy_per_turn", 2))
BUY_BUF = _p("buy_buf", 130)              # cash kept in hand after each animal
BUY_LAST_DAY = int(_p("buy_last_day", 14))  # cows need placed_day+8 <= 29
FEED_BUY_MAX = int(_p("feed_buy_max", 9))
FEED_RESERVE_X = _p("feed_res_x", 11)      # shed wheat target = herd*2 + this
FEED_EST = _p("feed_est", 48)             # conservative wheat price estimate
STRUCT_AHEAD = int(_p("struct_ahead", 3))
FEED_TRIP_MAX = int(_p("feed_trip_max", 6))
PLACE_TRIP_MAX = int(_p("place_trip_max", 3))

# ---- crops ------------------------------------------------------------------
PLOTS_PER_UNIT = _p("ppu", 5.59)
WHEAT_CAP = int(_p("wheat_cap", 23))
WHEAT_X = _p("wheat_x", 7)                # wheat target = herd + this
SB_CAP = int(_p("sb_cap", 15))
SB_FIRST_DAY = int(_p("sb_first_day", 4))
SB_LAST_PLANT = int(_p("sb_last_plant", 13))
MELON_CAP = int(_p("melon_cap", 14))
MELON_LAST_PLANT = int(_p("melon_last_plant", 16))
TOM_CAP = int(_p("tom_cap", 5))
CARROT_CAP = int(_p("carrot_cap", 8))

# ---- money / land -----------------------------------------------------------
CASH_FLOOR = _p("cash_floor", 121)
LAND_GAP = int(_p("land_gap", 4))
LAND_BUF = _p("land_buf", 800)
LAND_DAYS = [int(_p("land1_day", 7)), int(_p("land2_day", 8)), 99]  # NE, SW, SE

# ---- selling ----------------------------------------------------------------
# Drain-paced caps only for products shops actually eat — their price recovers
# overnight, so trickling sells out harvests the ratchet. Melon/fertilizer/
# wheat/egg are uncapped: no recovery (melon) or no real curve risk.
SELL_CAP = {"MILK": _p("cap_milk", 25), "WOOL": _p("cap_wool", 15),
            "STRAWBERRY": _p("cap_sb", 26), "TOMATO": _p("cap_tom", 16)}
# Sell only above this fraction of base price — holding a day or two lets town
# drain pull the price back up. Defeated markets get dumped at the end anyway.
SELL_FLOOR_FRAC = {"MILK": _p("floor_milk", 0.66), "WOOL": _p("floor_wool", 0.7),
                   "STRAWBERRY": _p("floor_sb", 1.0), "TOMATO": _p("floor_tom", 0.8)}
BASE_PRICE = {"WHEAT": 25, "CARROT": 60, "TOMATO": 60, "STRAWBERRY": 120,
              "MELON": 250, "EGG": 50, "MILK": 160, "WOOL": 200, "FERTILIZER": 100}
# If milk stays crashed this many days, the opponent is flooding it — the
# drain-aware herd plan already de-weights cows via the live price signal.
MILK_BAD_PRICE = _p("milk_bad_price", 99)
MILK_BAD_DAYS = int(_p("milk_bad_days", 3))
FERT_KEEP = int(_p("fert_keep", 11))       # fertilizer held back for FERTILIZE
FERT_USE_MIN = int(_p("fert_use_min", 6)) # bother fertilizing above this shed level

ANIMAL_STRUCT = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}

_S = {"day": -1, "sold": {}, "route": {}, "cargo": {},
      "feed_claims": set(), "place_claims": set(), "fert_claims": set()}

(PRIO_WATER, PRIO_AHARVEST, PRIO_CARE, PRIO_COLLECT,
 PRIO_HARVEST, PRIO_PLANT, PRIO_DIG) = range(7)


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


def _herd_plan(drain, prices):
    """Rank animals by expected daily gross = production rate x price, tempered
    by how much the town actually drains (demand headroom before our own
    supply crashes the curve). Shop draws vary per seed — seed 301 had three
    yarn stores and no milk shops, making sheep the play; a typical draw keeps
    milk king. Wool gets a discount because its sq curve crashes hard when two
    ranches dump into it."""
    milk_d = max(drain.get("MILK", 0), 13)   # prior: ~2 milk shops until seen
    wool_d = max(drain.get("WOOL", 0), 7)
    egg_d = max(drain.get("EGG", 0), 7)
    scores = {
        "COW":   1.5 * min(prices.get("MILK", 160), 320) * min(1.2, milk_d / 18),
        "SHEEP": 1.33 * min(prices.get("WOOL", 200), 260) * min(1.2, wool_d / 12) * 0.75,
        "GOOSE": 2.0 * prices.get("EGG", 52) * min(1.2, egg_d / 12),
    }
    order = sorted(scores, key=scores.get, reverse=True)
    a, b = order[0], order[1]
    plan = []
    while len(plan) < HERD_TARGET:
        plan += [a, a, b]          # ~2:1 split favoring the best product
    return plan[:HERD_TARGET]


def _crop_targets(day, last_day, herd, n_units):
    """Desired live-plant counts per crop for today. Caps are absolute —
    Pushkar ran ~45 plots on 12 units; labor is already what throttles
    planting, so don't double-throttle by crew size."""
    if day >= last_day - 4:
        return {"WHEAT": n_units * 3, "CARROT": min(8, n_units)}
    t = {}
    if day + CROPS["WHEAT"]["max"] <= last_day:
        t["WHEAT"] = min(WHEAT_CAP, max(10, herd + WHEAT_X))
    if day <= MELON_LAST_PLANT:
        t["MELON"] = MELON_CAP
    if SB_FIRST_DAY <= day <= SB_LAST_PLANT:
        t["STRAWBERRY"] = SB_CAP
    if 6 <= day <= last_day - 11:      # tomato: 4 productions days 8-11
        t["TOMATO"] = TOM_CAP
    if day + CROPS["CARROT"]["max"] <= last_day:
        t["CARROT"] = CARROT_CAP
    return {c: int(n) for c, n in t.items() if n > 0}


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
    shed_set = set(shed_tiles)
    drain = _drain_rates(obs.get("town", {}))
    prices = obs.get("market", {}).get("prices", {})

    if _S["day"] != day:
        _S = {"day": day, "sold": {}, "route": {}, "cargo": {},
              "feed_claims": set(), "place_claims": set(), "fert_claims": set()}
    herd_plan = _herd_plan(drain, prices)

    # ---- tile scan -----------------------------------------------------------
    growing = {}
    animals = []           # (x, y, animal, tile)
    empty_structs = []     # (x, y, kind)
    weeds = []
    for y, row in enumerate(tiles):
        for x, t in enumerate(row):
            if isinstance(t, dict):
                k = t.get("kind")
                if k == "PLANT":
                    growing[t["crop"]] = growing.get(t["crop"], 0) + 1
                elif "animal" in t:
                    animals.append((x, y, t["animal"], t))
                elif k in ("COOP", "PASTURE"):
                    empty_structs.append((x, y, k))
                elif k == "WEED":
                    weeds.append((x, y))
    planted_total = sum(growing.values())
    unlocked_tiles = sum(1 for row in tiles for t in row if t != "LOCKED")
    n_units = 1 + len(me["hands"])

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
    wheat_reserve = herd * 2 + FEED_RESERVE_X if day < last_day - 1 else 0

    # ---- market --------------------------------------------------------------
    market = []
    spend = 0
    # Survival floor: early days have no income — going to $0 means no payroll,
    # no feed, and the whole herd escapes (learned the hard way). Keep enough
    # for ~2 days of crew + feed until animal revenue starts (~day 8+).
    floor = _p("early_floor", 165) if day < 9 else CASH_FLOOR
    # Payroll reserve: hands quit the moment we can't pay — keep tomorrow's
    # wages before discretionary spend. The day-4 collapse trace: spent to
    # $2, every hand walked off, farm idled 8 days.
    floor = max(floor, sum(_fib(k) for k in range(1, n_units + 2)))
    #    Ranch chores (~3.5 tile-ops/animal/day) drive crew size harder than
    #    crops do; each placement in flight also needs a fetch trip.
    crew_target = min(CREW_MAX, max(CREW_MIN,
        int(-(-(herd_total * 0.6 + planted_total * 0.25 + in_transit) // 1))))
    if day < last_day and hour <= HIRE_LAST_HOUR:
        while len(market) < 10:
            k = me["hires_today"] + sum(1 for m in market if m[0] == "HIRE")
            if k >= crew_target:
                break
            cost = _fib(k)
            if money - spend - cost < 2:
                break
            market.append(["HIRE"])
            spend += cost
            # keep order slots free for seeds/buys/sells — hires continue
            # on later turns; a hand hired at hour 3 still gets ~20 turns
            if sum(1 for m in market if m[0] == "HIRE") >= 4:
                break

    # 2) Feed wheat: shed reserve = ~2 days of herd consumption. A missed feed
    #    day starts the 2-day escape clock — buy insurance aggressively.
    wheat_in_shed = shed.get("WHEAT", 0)
    while (herd_total > 0 and len(market) < 10
           and wheat_in_shed < wheat_reserve):
        need = min(wheat_reserve - wheat_in_shed, FEED_BUY_MAX)
        if need <= 0 or money - spend - need * FEED_EST <= 0:
            break
        market.append(["BUY_PRODUCT", "WHEAT", need])
        spend += need * FEED_EST
        wheat_in_shed += need

    # 3) Seeds for today's crop mix — funded before animals: a cow bought by
    #    starving the fields feeds nobody.
    plot_cap = min(unlocked_tiles - 4, n_units * PLOTS_PER_UNIT)
    room_to_grow = max(0, plot_cap - planted_total)
    crop_targets = _crop_targets(day, last_day, herd_total, n_units)
    for crop in ("WHEAT", "MELON", "STRAWBERRY", "TOMATO", "CARROT"):
        target = crop_targets.get(crop, 0)
        deficit = min(target - growing.get(crop, 0) - seeds.get(crop, 0), room_to_grow)
        if deficit <= 0 or len(market) >= 10:
            continue
        cost = deficit * CROPS[crop]["seed"]
        if money - spend - cost < floor:
            deficit = max(0, int((money - spend - floor) // CROPS[crop]["seed"]))
            cost = deficit * CROPS[crop]["seed"]
        if deficit > 0:
            market.append(["BUY_SEED", crop, deficit])
            spend += cost

    # 4) Animals: all-in while the herd is incomplete. A cow placed by ~day 10
    #    returns ~10x its cost — cash in the bank earns 0%. Shed animals are
    #    safe (no feeding, no escape), but in-transit pileups are dead capital,
    #    so pause buying while >2 animals wait for homes.
    remaining = list(herd_plan)
    for _, _, a, _t in animals:
        if a in remaining:
            remaining.remove(a)
    for a, n in transit_by_type.items():
        for _ in range(n):
            if a in remaining:
                remaining.remove(a)

    if day <= BUY_LAST_DAY and herd_total < HERD_TARGET and in_transit <= 2:
        bought = 0
        for a in remaining:
            if bought >= BUY_PER_TURN or len(market) >= 10:
                break
            struct = ANIMAL_STRUCT[a]
            homes = sum(1 for _, _, k in empty_structs if k == struct) \
                - sum(1 for aa, n in transit_by_type.items()
                      if ANIMAL_STRUCT[aa] == struct for _ in range(n))
            if homes <= 0 and in_transit >= 3:
                continue
            if money - spend > ANIMALS[a]["cost"] + max(BUY_BUF, floor):
                market.append(["BUY_ANIMAL", a, 1])
                spend += ANIMALS[a]["cost"]
                transit_by_type[a] = transit_by_type.get(a, 0) + 1
                bought += 1

    # 5) Land: NE ~day 7, SW ~day 10 — or earlier when tiles genuinely fill.
    n_extra = len(me["unlocked_quadrants"]) - 1
    if n_extra < 3 and len(market) < 10:
        future_structs = max(0, HERD_TARGET - herd_total)
        used_tiles = planted_total + herd + len(empty_structs)
        want_land = ((used_tiles + min(future_structs, 6) >= unlocked_tiles - LAND_GAP
                      and day >= 3)
                     or day >= LAND_DAYS[n_extra])
        if (want_land and day + 8 <= last_day
                and money - spend > LAND_PRICES[n_extra] + LAND_BUF):
            market.append(["BUY_LAND"])
            spend += LAND_PRICES[n_extra]

    # 6) Sells last — leftovers retry next turn. Drain-paced caps only for
    #    products whose price recovers overnight; dump everything day 29.
    dump_all = day >= last_day - 1
    for item, n in shed.items():
        if n <= 0 or item in ANIMALS or len(market) >= 10:
            continue
        if item == "WHEAT":
            # Feed stock, not a sellable — dumping it just forces a rebuy at
            # spread loss every morning. Only sell at endgame dump or when the
            # shed is about to cap out.
            if not dump_all and sum(shed.values()) < 90:
                continue
            n = max(0, n - wheat_reserve)
            if n <= 0:
                continue
        if item == "FERTILIZER":
            n = max(0, n - FERT_KEEP)
            if n <= 0:
                continue
        cap = SELL_CAP.get(item)
        if cap is not None and not dump_all:
            # price floor: hold crashed inventory and let town drain recover
            # the price overnight — unless the shed is about to cap.
            fbase = BASE_PRICE.get(item)
            if (fbase and item in SELL_FLOOR_FRAC
                    and prices.get(item, fbase) < SELL_FLOOR_FRAC[item] * fbase
                    and sum(shed.values()) < 90):
                continue
            cap = max(cap, drain.get(item, 0))
        allowed = n if (cap is None or dump_all) else max(0, cap - _S["sold"].get(item, 0))
        sell_n = min(n, allowed)
        if sell_n > 0:
            market.append(["SELL", item, sell_n])
            _S["sold"][item] = _S["sold"].get(item, 0) + sell_n

    # ---- tasks ---------------------------------------------------------------
    # Animal chores: FEED needs wheat cargo (feed_set, ranch-only); CARE /
    # COLLECT / product-HARVEST are cargo-free and live in task_map so any
    # unit can pitch in. Chores chain: a fed tile exposes care -> collect ->
    # harvest over consecutive turns and a unit standing on it just stays.
    tasks = []
    plant_candidates = []
    feed_set, place_map, build_map, fert_set = set(), {}, {}, set()
    feed_urg = {}    # (x,y) -> consecutive_unfed (escape countdown)
    chore_map = {}   # (x,y) -> (prio, op): rancher-only animal chores
    can_plant = hour <= turns_per_day - 2

    allowance = {c: max(0, t - growing.get(c, 0)) for c, t in crop_targets.items()}

    for y, row in enumerate(tiles):
        for x, t in enumerate(row):
            if t == "LOCKED":
                continue
            if isinstance(t, dict):
                kind = t.get("kind")
                if kind == "WEED":
                    # weeds don't kill crops but they eat planting space —
                    # once the farm is >~1/4 weeds they outrank new planting
                    dig_prio = PRIO_HARVEST if len(weeds) > 12 else PRIO_DIG
                    tasks.append((dig_prio, x, y, ["DIG"], "dig"))
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
                    else:
                        if not t["watered_today"]:
                            # streak>=1 dies tonight — outrank everything else
                            wp = PRIO_WATER - 1 if t.get("consecutive_unwatered", 0) >= 1 else PRIO_WATER
                            tasks.append((wp, x, y, ["WATER"], "water"))
                        # Fertilize melons in their bonus window and producing
                        # strawberries — +1-2 units/day beats selling the fert.
                        if (crop in ("MELON", "STRAWBERRY")
                                and t.get("fertilized_until_day", -1) < day
                                and (cd["ongoing"] or age >= (cd["max"] + 1) // 2)):
                            fert_set.add((x, y))
                elif "animal" in t:
                    if not t["fed_today"]:
                        feed_set.add((x, y))
                        feed_urg[(x, y)] = t.get("consecutive_unfed", 0)
                    # Chores are rancher-primary (chore_map) but also go into
                    # task_map at bottom priority — an idle crop hand standing
                    # next to a pasture should still grab a spare care/collect.
                    if t.get("yield_units", 0) > 0:
                        chore_map[(x, y)] = (PRIO_AHARVEST, ["HARVEST"])
                    elif t["fed_today"] and not t["cared_today"]:
                        chore_map[(x, y)] = (PRIO_CARE, ["CARE"])
                    elif t.get("fertilizer_available"):
                        chore_map[(x, y)] = (PRIO_COLLECT, ["COLLECT_FERTILIZER"])
                    if (x, y) in chore_map:
                        tasks.append((PRIO_DIG + 1, x, y, chore_map[(x, y)][1], "chore"))
                elif kind in ("COOP", "PASTURE"):
                    for a, d in ANIMALS.items():
                        if d["struct"] != kind:
                            continue
                        if shed.get(a, 0) > 0 or any(inv.get(a, 0) > 0 for inv in inventories):
                            place_map[(x, y)] = a
                            break
            elif t is None and can_plant and (x, y) not in shed_set:
                plant_candidates.append((x, y))

    # Structures a few ahead of the herd plan, clustered beside the shed —
    # every feed/place/collect trip is a shed round-trip, so tile distance is
    # paid daily. Crops never visit the shed (produce auto-drops overnight).
    structs_total = len(empty_structs) + herd
    if n_units >= 3 and day < last_day - 4:
        have_struct = {}
        for _, _, k in empty_structs:
            have_struct[k] = have_struct.get(k, 0) + 1
        for _, _, a, _t in animals:
            k = ANIMAL_STRUCT[a]
            have_struct[k] = have_struct.get(k, 0) + 1
        want_struct = {}
        for _, _, a, _t in animals:
            k = ANIMAL_STRUCT[a]
            want_struct[k] = want_struct.get(k, 0) + 1
        # in-transit animals need homes too — otherwise they park in the shed
        for a, n in transit_by_type.items():
            k = ANIMAL_STRUCT[a]
            want_struct[k] = want_struct.get(k, 0) + n
        for a in remaining[:STRUCT_AHEAD]:
            k = ANIMAL_STRUCT[a]
            want_struct[k] = want_struct.get(k, 0) + 1
        need_ops = []
        for k, want in want_struct.items():
            for _ in range(max(0, want - have_struct.get(k, 0))):
                need_ops.append(k)

        def _adj_struct(p):
            x, y = p
            n = 0
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < board and 0 <= ny < board:
                    tt = tiles[ny][nx]
                    if isinstance(tt, dict) and tt.get("kind") in ("COOP", "PASTURE"):
                        n += 1
            return n

        if need_ops:
            plant_candidates.sort(key=lambda p: (-_adj_struct(p), _dist(p, shed_tiles[0])))
            for (x, y), k in zip(plant_candidates[:len(need_ops)], need_ops):
                build_map[(x, y)] = k
            plant_candidates = plant_candidates[len(need_ops):]

    plant_candidates.sort(key=lambda p: _dist(p, shed_tiles[0]))
    seed_stock = dict(seeds)
    for x, y in plant_candidates:
        if planted_total >= plot_cap:
            break
        crop = None
        for c in ("WHEAT", "STRAWBERRY", "MELON", "TOMATO", "CARROT"):
            if allowance.get(c, 0) > 0 and seed_stock.get(c, 0) > 0:
                crop = c
                break
        if crop is None:
            break
        tasks.append((PRIO_PLANT, x, y, ["PLANT", crop], "plant"))
        allowance[crop] -= 1
        seed_stock[crop] -= 1
        planted_total += 1

    # ---- assign units ---------------------------------------------------------
    task_map = {}
    for prio, x, y, op, tag in tasks:
        cur = task_map.get((x, y))
        if cur is None or prio < cur[0]:
            task_map[(x, y)] = (prio, op, tag)

    units = [me["farmer"]] + list(me["hands"])
    ranch_work = bool(feed_set or place_map or build_map or in_transit
                      or any(t.get("fertilizer_available") or (t["fed_today"] and not t["cared_today"])
                             or t.get("yield_units", 0) > 0
                             for _, _, _, t in animals))
    n_ranchers = 0
    if ranch_work:
        # ~4 tile-ops/animal/day; a unit inside a tight cluster does ~12+/day.
        # Animals waiting in the shed each need a fetch trip too. When the
        # crew is tiny the farmer ranches too — unfed animals escape.
        n_ranchers = min(len(units),
                         max(1, int(-(-herd_total // 2.5))))
    rancher_idx = set(range(len(units) - n_ranchers, len(units)))

    fert_available = shed.get("FERTILIZER", 0) > FERT_USE_MIN and fert_set

    claimed, ops = set(), []
    for ui, pos in enumerate(units):
        pos = tuple(pos)
        ukey = "f" if ui == 0 else f"h{ui - 1}"
        inv = inventories[ui] if ui < len(inventories) else {}
        carrying = (inv.get("WHEAT", 0) > 0 or inv.get("FERTILIZER", 0) > 0
                    or any(inv.get(a, 0) > 0 for a in ANIMALS))
        is_rancher = ui in rancher_idx or carrying

        if is_rancher:
            op = _ranch_op(ukey, pos, inv, chore_map, claimed, shed_tiles, shed,
                           feed_set, feed_urg, place_map, build_map,
                           fert_set if fert_available or inv.get("FERTILIZER", 0) > 0 else set())
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
            # idle crop hands pitch in on construction — an unbuilt structure
            # stalls the whole herd pipeline
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


def _ranch_op(ukey, pos, inv, chore_map, claimed, shed_tiles, shed,
              feed_set, feed_urg, place_map, build_map, fert_set):
    """Action for a ranch-duty unit. Returns None if it should do crops.

    Cargo first (a carried animal/wheat/fertilizer must be delivered — at
    midnight it all drops back into the shed anyway). Then, in order: feed
    runs (escape deadline), placement fetches, chore sweeps, fertilize runs,
    builds.
    """
    feeds = feed_set - claimed - _S["feed_claims"]
    places = {xy: a for xy, a in place_map.items()
              if xy not in claimed and xy not in _S["place_claims"]}
    chores = {xy: v for xy, v in chore_map.items() if xy not in claimed}
    builds = {xy: k for xy, k in build_map.items() if xy not in claimed}
    ferts = fert_set - claimed - _S["fert_claims"]
    shed_set = set(shed_tiles)
    shed_animals = any(shed.get(a, 0) > 0 for a in ANIMALS)
    mine = _S["cargo"].get(ukey)          # tiles this unit claimed at pickup

    carrying_animal = next((a for a in ANIMALS if inv.get(a, 0) > 0), None)
    carrying_wheat = inv.get("WHEAT", 0)
    carrying_fert = inv.get("FERTILIZER", 0)

    # --- carrying an animal -> deliver to a matching empty structure ---
    if carrying_animal:
        own = [xy for xy in (mine or ()) if place_map.get(xy) == carrying_animal]
        want = own or [xy for xy, a in places.items() if a == carrying_animal]
        if want:
            tgt = min(want, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                _S["place_claims"].discard(tgt)
                if mine is not None:
                    mine.discard(tgt)
                return ["PLACE", carrying_animal]
            return _step_toward(pos, tgt)
        return ["PASS"] if pos in shed_set else _step_toward(pos, _nearest_shed(pos, shed_tiles))

    # --- carrying wheat -> feed the nearest hungry animal ---
    if carrying_wheat:
        targets = feeds
        if mine:
            own = [xy for xy in mine if xy in feed_set]
            if own:
                targets = own
        if targets:
            # streak-1 animals are one missed day from escaping — save them first
            tgt = min(targets, key=lambda xy: (-feed_urg.get(xy, 0), _dist(pos, xy)))
            if pos == tgt:
                claimed.add(tgt)
                _S["feed_claims"].discard(tgt)
                if mine is not None:
                    mine.discard(tgt)
                return ["FEED"]
            return _step_toward(pos, tgt)
        # nothing left to feed — the leftover wheat is dead weight until the
        # midnight auto-drop. Fall through to the shared work tail so the unit
        # can still place/build/chore instead of parking for the day.

    # --- carrying fertilizer -> hit pending crop targets ---
    if carrying_fert:
        if ferts:
            tgt = min(ferts, key=lambda xy: _dist(pos, xy))
            if pos == tgt:
                claimed.add(tgt)
                _S["fert_claims"].discard(tgt)
                if mine is not None:
                    mine.discard(tgt)
                return ["FERTILIZE"]
            return _step_toward(pos, tgt)
        # fall through to shared work

    # ---- shared work tail -----------------------------------------------------
    # 1) feed run: pickup enough wheat for a cluster sweep, claiming the tiles
    if feeds and not carrying_fert and shed.get("WHEAT", 0) > 0:
        if pos in shed_set:
            take = min(len(feeds), shed["WHEAT"], FEED_TRIP_MAX)
            chosen = set(sorted(feeds,
                                key=lambda xy: (-feed_urg.get(xy, 0), _dist(pos, xy)))[:take])
            _S["cargo"][ukey] = chosen
            claimed.update(chosen)
            _S["feed_claims"].update(chosen)
            return ["PICKUP", "WHEAT", take]
        return _step_toward(pos, _nearest_shed(pos, shed_tiles))

    # 2) placement fetch: shed animals earn nothing — get them onto structures
    if places:
        fetchable = [(xy, a) for xy, a in places.items() if shed.get(a, 0) > 0]
        if fetchable:
            xy, a = min(fetchable, key=lambda p: _dist(pos, p[0]))
            if pos in shed_set:
                homes = [h for h, a2 in places.items() if a2 == a]
                take = min(PLACE_TRIP_MAX, shed.get(a, 0), len(homes))
                claimed.update(homes[:take])
                _S["place_claims"].update(homes[:take])
                _S["cargo"][ukey] = set(homes[:take])
                return ["PICKUP", a, take]
            return _step_toward(pos, _nearest_shed(pos, shed_tiles))

    # 3) urgent construction — animals parked in the shed produce nothing.
    #    Outrank chores while the pipeline is blocked; idle structure
    #    lookahead can wait behind daily care once everyone is housed.
    if builds and shed_animals:
        tgt = min(builds, key=lambda xy: _dist(pos, xy))
        if pos == tgt:
            claimed.add(tgt)
            return ["BUILD_" + builds[tgt]]
        return _step_toward(pos, tgt)

    # 4) chore sweep — the engine of the whole ranch (care doubles output,
    #    fertilizer is ~$70+/animal/day, unharvested product caps out)
    if chores:
        tgt = min(chores, key=lambda xy: _dist(pos, xy))
        if pos == tgt:
            claimed.add(tgt)
            return chores[tgt][1]
        return _step_toward(pos, tgt)

    # 5) fertilize run when the shed has more than we want to keep
    if ferts and not carrying_wheat and shed.get("FERTILIZER", 0) > FERT_USE_MIN:
        if pos in shed_set:
            take = min(len(ferts), shed["FERTILIZER"] - 2, 4)
            if take > 0:
                chosen = set(sorted(ferts, key=lambda xy: _dist(pos, xy))[:take])
                _S["cargo"][ukey] = chosen
                claimed.update(chosen)
                _S["fert_claims"].update(chosen)
                return ["PICKUP", "FERTILIZER", take]
        else:
            return _step_toward(pos, _nearest_shed(pos, shed_tiles))

    # 6) structures
    if builds:
        tgt = min(builds, key=lambda xy: _dist(pos, xy))
        if pos == tgt:
            claimed.add(tgt)
            return ["BUILD_" + builds[tgt]]
        return _step_toward(pos, tgt)

    return None  # no ranch work -> do crops (leftover cargo drops at midnight)


# IMPORTANT: kaggle_environments binds the LAST callable defined in this file
# as the agent (see agent.get_last_callable). Keep this wrapper last.
def agent(obs, config):
    return _agent_impl(obs, config)
