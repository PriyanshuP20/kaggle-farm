"""Record an agent's actions into a replayable tape.

Runs the agent on a fixed seed with weeds disabled (clean canonical board) and
a passive opponent, capturing the exact action dict it emits each step. The
result is a 719-step tape that the tape player replays under the shop router.

Usage: python tape_record.py AGENT SEED OUT_JSON
"""
import copy, json, sys
from kaggle_environments import make


def record(agent_path, seed):
    env = make("kaggriculture",
               configuration={"episodeSteps": 720, "seed": seed,
                              "weedSpawnChance": 0.0},  # clean tape: no weeds
               debug=False)
    import importlib.util
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
    final = obs[0].observation["farms"][0]["money"]
    shops = obs[0].observation.get("town", {}).get("unlocked_shops", [])
    return tape, final, shops


if __name__ == "__main__":
    agent, seed, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    tape, money, shops = record(agent, seed)
    json.dump({"seed": seed, "money": money, "shops": shops, "tape": tape},
              open(out, "w"))
    print(f"recorded {len(tape)} steps | money={money} | shops={shops}")
