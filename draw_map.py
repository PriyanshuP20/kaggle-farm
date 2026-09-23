import json, collections, sys
from kaggle_environments import make
import concurrent.futures as cf

PASS = {"farmer": ["PASS"], "hands": [], "market": []}

def draw(seed):
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=False)
    env.reset(2)
    for _ in range(170):          # through day 7: first two shops revealed
        obs = env.step([PASS, PASS])
        if obs[0].status != "ACTIVE":
            break
    return seed, env.steps[-1][0].observation.get("town", {}).get("unlocked_shops", [])

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    with cf.ProcessPoolExecutor(8) as ex:
        res = list(ex.map(draw, range(n)))
    m = {s: shops for s, shops in res}
    json.dump(m, open("seed_draws.json", "w"))
    cnt = collections.Counter(tuple(v[:2]) for v in m.values())
    print(f"seeds={n} distinct_pairs={len(cnt)}")
    for k, v in cnt.most_common():
        print(f"  {k}: {v}")
