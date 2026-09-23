"""Generate the tape library: record an agent across seeds (weeds disabled so
the tape is a clean canonical line), then keep the highest-scoring tape per
first-two-shop pair. Output: tapes.json for tape_player.py.

Usage: python gen_tapes.py AGENT OUT_JSON [N_SEEDS]
"""
import copy, json, sys, collections
from kaggle_environments import make
import concurrent.futures as cf
import importlib.util


def record(args):
    agent_path, seed = args
    try:
        env = make("kaggriculture",
                   configuration={"episodeSteps": 720, "seed": seed,
                                  "weedSpawnChance": 0.0}, debug=False)
        spec = importlib.util.spec_from_file_location("gen_agent", agent_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        obs = env.reset(2)
        tape = []
        for _ in range(720):
            act = mod.agent(obs[0].observation, env.configuration)
            tape.append(copy.deepcopy(act))
            obs = env.step([act, {"farmer": ["PASS"], "hands": [], "market": []}])
            if obs[0].status != "ACTIVE":
                break
        o = obs[0].observation
        return seed, tape, o["farms"][0]["money"], o["town"]["unlocked_shops"][:2]
    except Exception as e:
        return seed, None, 0, str(e)


if __name__ == "__main__":
    agent, out, n = sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 64
    with cf.ProcessPoolExecutor(8) as ex:
        results = list(ex.map(record, [(agent, s) for s in range(n)]))

    best = {}          # shop_pair -> (money, seed, tape)
    fails = 0
    for seed, tape, money, pair in results:
        if tape is None:
            fails += 1
            continue
        key = tuple(pair)
        if key not in best or money > best[key][0]:
            best[key] = (money, seed, tape)

    # dedupe tapes into a shared action/action-sequence pool? keep simple:
    # store each winning tape by route name "r{seed}"
    tapes, routes = {}, {}
    for pair, (money, seed, tape) in best.items():
        name = f"r{seed}"
        tapes[name] = tape
        routes[",".join(pair)] = name
    # default route = the single highest-money tape (most-represented generic line)
    dflt = max(best.values(), key=lambda x: x[0])[2]
    tapes["default"] = dflt
    json.dump({"tapes": tapes, "routes": routes}, open(out, "w"))
    print(f"seeds={n} pairs_covered={len(best)} fails={fails} "
          f"best={max(v[0] for v in best.values()):.0f}")
