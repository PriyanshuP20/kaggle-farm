import json, collections
from kaggle_environments import make
import concurrent.futures as cf

def run(seed):
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=False)
    env.run(["agents/farm_v12.py", "starter"])
    o = env.steps[-1][0].observation
    shops = o.get("town", {}).get("unlocked_shops", [])
    return seed, o["farms"][0]["money"], tuple(shops[:2]), dict(collections.Counter(shops))

if __name__ == "__main__":
    with cf.ProcessPoolExecutor(8) as ex:
        results = list(ex.map(run, range(24)))
    milk_shops = {"PIZZA_SHOP","ICE_CREAM_SHOP","SMOOTHIE_SHOP"}
    by_draw = collections.defaultdict(list)
    for seed, money, s2, allc in results:
        yarn = allc.get("YARN_STORE",0)
        milk = sum(allc.get(s,0) for s in milk_shops)
        by_draw[("yarn"+str(min(yarn,2)), "milk"+str(min(milk,2)))].append(round(money/1000))
    for k,v in sorted(by_draw.items()):
        print(f"{k}: n={len(v)} avg=${sum(v)/len(v):.0f}k  {v}")
    print("\nper-seed:", [(s,round(m/1000),f2) for s,m,f2,c in results])
