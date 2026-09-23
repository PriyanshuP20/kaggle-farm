"""Local-search a recorded tape's MARKET orders on its home seed.

Market orders (SELL/BUY_*/HIRE) don't move units, so mutating them can't break
the position stream — the tape stays valid. Hill-climb: mutate one order,
resim the whole tape, keep if the final money improves.

Usage: python opt_tape.py TAPE_JSON SEED OUT_JSON ITERS
"""
import copy, json, random, sys
from kaggle_environments import make

PASS = {"farmer": ["PASS"], "hands": [], "market": []}
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON",
            "EGG", "MILK", "WOOL", "FERTILIZER")


def score(tape, seed):
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=False)
    env.reset(2)
    for i in range(min(len(tape), 719)):
        obs = env.step([tape[i], PASS])
        if obs[0].status != "ACTIVE":
            break
    return env.steps[-1][0].observation["farms"][0]["money"]


def mutate(tape, rng):
    """One market-order mutation on a copy of the tape."""
    t = copy.deepcopy(tape)
    steps_with_market = [i for i, a in enumerate(t) if a.get("market")]
    if not steps_with_market:
        return t
    i = rng.choice(steps_with_market)
    orders = t[i]["market"]
    if not orders:
        return t
    move = rng.random()
    if move < 0.4 and len(orders) < 10:
        # insert a sell of a likely-stocked premium item
        item = rng.choice(["MILK", "WOOL", "STRAWBERRY", "TOMATO", "CARROT", "FERTILIZER", "EGG"])
        orders.append(["SELL", item, rng.choice([1, 2, 4, 8, 20, 99])])
    elif move < 0.7 and orders:
        # tweak a quantity
        j = rng.randrange(len(orders))
        o = orders[j]
        if len(o) >= 3:
            o[2] = max(1, int(o[2] * rng.choice([0.5, 0.75, 1.5, 2.0, 4.0])))
    elif orders:
        # drop an order (frees a market slot / saves cash)
        orders.pop(rng.randrange(len(orders)))
    return t


if __name__ == "__main__":
    raw = json.load(open(sys.argv[1]))
    tape = raw["tape"] if isinstance(raw, dict) and "tape" in raw else raw
    seed, out, iters = int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
    rng = random.Random(0)
    best = score(tape, seed)
    print(f"start: {best:.0f}")
    for it in range(iters):
        cand = mutate(tape, rng)
        s = score(cand, seed)
        if s > best:
            best, tape = s, cand
            print(f"  iter {it}: {s:.0f} (+{s - 0:.0f})")
    json.dump({"seed": seed, "money": best, "tape": tape}, open(out, "w"))
    print(f"final: {best:.0f}")
