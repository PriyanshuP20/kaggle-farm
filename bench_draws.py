"""Benchmark an agent on an explicit seed list, grouped by draw class.
Usage: python bench_draws.py AGENT --seeds 6,10,28 --opp starter -j 8
       KAG_DRAW_PROFILES='{"dry":{...}}' python bench_draws.py ... (passed through env)
"""
import argparse, collections, json, os
from kaggle_environments import make
import concurrent.futures as cf

SHOPS = {"BAKERY":["EGG","WHEAT"],"PIZZA_SHOP":["MILK","TOMATO","WHEAT"],
         "BRUNCH_SPOT":["EGG","WHEAT","STRAWBERRY"],"YARN_STORE":["WOOL"],
         "ICE_CREAM_SHOP":["STRAWBERRY","MILK","WHEAT"],"PET_CAFE":["CARROT"],
         "SMOOTHIE_SHOP":["STRAWBERRY","MILK"],"FARMERS_MARKET":["WHEAT","CARROT","TOMATO","STRAWBERRY"]}

def cls(shops):
    d = {}
    for s in shops[:2]:
        prods = SHOPS[s]; mult = 2 if len(prods) == 1 else 1
        for p in prods: d[p] = d.get(p, 0) + mult
    milk, wool, egg = d.get("MILK",0), d.get("WOOL",0), d.get("EGG",0)
    veg = d.get("CARROT",0)+d.get("TOMATO",0)+d.get("STRAWBERRY",0)
    if wool >= 2: return "yarn"
    if milk >= 2: return "milk_rich"
    if egg >= 2: return "egg"
    if milk == 0 and wool == 0: return "veg" if veg >= 4 else "dry"
    return "default"

def run(args):
    agent, opp, seed = args
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=False)
    env.run([agent, opp])
    o = env.steps[-1][0].observation
    return seed, o["farms"][0]["money"], o["farms"][1]["money"], tuple(o["town"]["unlocked_shops"][:2])

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("agent"); ap.add_argument("--opp", default="starter")
    ap.add_argument("--seeds", required=True); ap.add_argument("-j", type=int, default=8)
    a = ap.parse_args()
    seeds = [int(x) for x in a.seeds.split(",")]
    with cf.ProcessPoolExecutor(a.j) as ex:
        res = list(ex.map(run, [(a.agent, a.opp, s) for s in seeds]))
    by_cls = collections.defaultdict(list)
    for seed, me, opp, s2 in res:
        by_cls[cls(s2)].append((seed, me, opp))
    tot_me = tot_opp = 0
    for c in sorted(by_cls):
        rs = by_cls[c]
        avg = sum(r[1] for r in rs)/len(rs)
        print(f"{c:10s} n={len(rs):2d} avg=${avg:7.0f}  " +
              " ".join(f"{r[0]}:{round(r[1]/1000)}k" for r in rs))
        tot_me += sum(r[1] for r in rs); tot_opp += sum(r[2] for r in rs)
    print(f"TOTAL n={len(res)} avg=${tot_me/len(res):.0f} opp_avg=${tot_opp/len(res):.0f}")
