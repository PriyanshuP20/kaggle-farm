import json, sys
from kaggle_environments import make
import concurrent.futures as cf

def run(seed):
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=False)
    env.run(["agents/farm_v13.py", "starter"])
    o = env.steps[-1][0].observation
    return seed, o["farms"][0]["money"]

if __name__ == "__main__":
    seeds = [int(x) for x in sys.argv[1].split(",")]
    with cf.ProcessPoolExecutor(8) as ex:
        res = dict(ex.map(run, seeds))
    print("RESULTS", json.dumps(res))
