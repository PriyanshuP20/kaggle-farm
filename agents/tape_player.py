"""Tape player: replays pre-computed action tapes chosen by the shop draw.

Tapes live in tapes.json as {route_name: [719 actions]}. At step 144 (day 6,
first two shops known) the router picks the route for this draw; at step 648
it switches to the liquidation tape. Repair layers keep the tape alive when
the live board diverges from the canonical recording:

  weed_repair   DIG a weed blocking PLANT/BUILD_*, requeue the op same day
  hand_align    pad/truncate the tape's hands list to today's real count
  sell_lead     sell next step's planned lots early when the price is decent
  liquidate     final step: dump the projected shed
"""
import copy, json, os
from collections import deque
from pathlib import Path

TURNS_PER_DAY = 24
ROUTE_STEP = 144          # day 6: first two shops revealed
FINAL_STEP = 648          # day 27: liquidation tape
LAST_STEP = 718
SHED_CAPACITY = 100
MAX_ORDERS = 10
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON",
            "EGG", "MILK", "WOOL", "FERTILIZER")
WEED_BLOCKED = {"PLANT", "BUILD_COOP", "BUILD_PASTURE"}
ANIMALS = {"GOOSE", "COW", "SHEEP"}
PASS = {"farmer": ["PASS"], "hands": [], "market": []}

# shop-pair -> route name. Tuned per generated tape library; default route is
# the generic tape (index of the most common draw).
SHOP_ROUTES = json.loads(os.environ.get("KAG_SHOP_ROUTES", "{}"))
_TAPE_FILE = os.environ.get("KAG_TAPE_FILE", "tapes.json")
TAPES = {}
_ROUTES = {}
_STATE = {}


def _load():
    global TAPES, _ROUTES
    if TAPES:
        return
    try:
        folder = Path(agent.__code__.co_filename).resolve().parent
        data = json.loads((Path(folder).parent / _TAPE_FILE).read_text())
    except Exception:
        try:
            data = json.loads(Path(_TAPE_FILE).read_text())
        except Exception:
            data = {}
    TAPES = data.get("tapes", {})
    _ROUTES = {tuple(k.split(",")): v for k, v in
               data.get("routes", {}).items()}
    _ROUTES.update({tuple(k.split(",")): v for k, v in SHOP_ROUTES.items()})


def _projected_shed(action, shed, positions, inventories, tiles):
    """Estimate shed stock after this turn's shed-adjacent PICKUP/DROP/PLACE."""
    stock = dict(shed)
    total = sum(stock.values())
    center = len(tiles) // 2
    shed_tiles = {(center - 1, center - 1), (center, center - 1),
                  (center - 1, center), (center, center)}
    workers = [action.get("farmer") or ["PASS"], *(action.get("hands") or [])]
    for w in range(min(len(workers), len(positions))):
        if tuple(positions[w]) not in shed_tiles:
            continue
        work = workers[w]
        op = work[0] if work else "PASS"
        inv = inventories[w] if w < len(inventories) else {}
        if op == "PICKUP" and len(work) >= 2 and work[1] in stock:
            n = max(0, int(work[2]) if len(work) >= 3 else 1)
            taken = min(stock[work[1]], n)
            stock[work[1]] -= taken
            total -= taken
        elif op == "DROP":
            for item, held in inv.items():
                add = min(max(0, int(held)), max(0, SHED_CAPACITY - total))
                stock[item] = stock.get(item, 0) + add
                total += add
        elif op == "PLACE" and len(work) >= 2 and work[1] not in ANIMALS:
            n = max(0, int(work[2]) if len(work) >= 3 else 1)
            add = min(n, max(0, int(inv.get(work[1], 0))),
                      max(0, SHED_CAPACITY - total))
            if add:
                stock[work[1]] = stock.get(work[1], 0) + add
                total += add
    return stock


def _repair_weeds(action, positions, tiles, state, step):
    """Conservative weed repair: only DIG when a queued op is a blocked
    PLANT/BUILD on a tile that is CURRENTLY a weed, then requeue the op for
    next turn. Never rewrites unrelated ops — the tape's ops replay verbatim."""
    day = step // TURNS_PER_DAY
    if day != state["day"]:
        state["day"] = day
        state["queues"].clear()
    workers = [action.get("farmer") or ["PASS"], *(action.get("hands") or [])]
    for w in range(min(len(workers), len(positions))):
        q = state["queues"].setdefault(w, deque())
        q.append(list(workers[w]))
        op = q[0] if q else None
        if op and op[0] in WEED_BLOCKED:
            x, y = positions[w]
            tile = tiles[y][x] if 0 <= y < len(tiles) and 0 <= x < len(tiles) else None
            if isinstance(tile, dict) and tile.get("kind") == "WEED":
                workers[w] = ["DIG"]
                continue
        workers[w] = q.popleft() if q else ["PASS"]
    action["farmer"], action["hands"] = workers[0], workers[1:]


def _sell_lead(action, shed, tape, step):
    """If the tape sells an item next step and we hold stock now, sell early
    (rides transient prices; the tape's sell next step will just clamp)."""
    nxt = step + 1
    if nxt > LAST_STEP or nxt % 72 == 0 or step % 4 == 0:
        return
    planned = {}
    for o in tape[nxt].get("market") or []:
        if o and o[0] == "SELL" and len(o) >= 3 and o[1] in PRODUCTS:
            planned[o[1]] = planned.get(o[1], 0) + max(0, int(o[2]))
    already = {o[1] for o in action.get("market") or []
               if o and o[0] == "SELL" and len(o) > 1}
    for item, qty in planned.items():
        if item in already or len(action["market"]) >= MAX_ORDERS:
            continue
        n = min(shed.get(item, 0), qty)
        if n > 0:
            action["market"].append(["SELL", item, n])


def _liquidate(shed):
    return {"farmer": ["PASS"], "hands": [],
            "market": [["SELL", item, n] for item, n in shed.items()
                       if n > 0][:MAX_ORDERS]}


def _agent_impl(observation, configuration=None):
    _load()
    if not TAPES:
        return copy.deepcopy(PASS)
    step = int(observation["step"])
    player = int(observation["player"])
    st = _STATE.get(player)
    if st is None or step <= st["last"]:
        st = _STATE[player] = {"last": -1, "day": -1, "queues": {},
                               "route": "default"}
    st["last"] = step

    if step >= ROUTE_STEP and not st.get("routed"):
        shops = tuple(observation["town"]["unlocked_shops"][:2])
        st["route"] = _ROUTES.get(shops, st["route"])
        st["routed"] = True
    if step >= FINAL_STEP:
        st["route"] = "final" if "final" in TAPES else st["route"]

    tape = TAPES.get(st["route"]) or TAPES.get("default") or next(iter(TAPES.values()))
    if step >= len(tape):
        return _liquidate(observation["private"]["shed"])

    action = copy.deepcopy(tape[step])
    farm = observation["farms"][player]
    positions = [farm["farmer"], *farm["hands"]]
    inventories = observation["private"].get("inventories", [])
    shed = observation["private"]["shed"]

    # hand_align: tape was recorded with N hands; today may differ
    real_hands = len(farm["hands"])
    have = len(action.get("hands") or [])
    if have < real_hands:
        action["hands"] = (action.get("hands") or []) + [["PASS"]] * (real_hands - have)
    elif have > real_hands:
        action["hands"] = action["hands"][:real_hands]

    _repair_weeds(action, positions, farm["tiles"], st, step)
    _sell_lead(action, shed, tape, step)
    action["market"] = (action.get("market") or [])[:MAX_ORDERS]
    if step == LAST_STEP:
        return _liquidate(shed)
    return action


def agent(observation, configuration=None):
    try:
        return _agent_impl(observation, configuration)
    except Exception:
        try:
            farm = observation["farms"][int(observation.get("player", 0))]
            return {"farmer": ["PASS"],
                    "hands": [["PASS"] for _ in farm.get("hands", [])],
                    "market": []}
        except Exception:
            return copy.deepcopy(PASS)
