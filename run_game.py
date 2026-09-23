#!/usr/bin/env python3
"""Run a single Kaggriculture game and dump a replay.

Examples:
    python run_game.py                        # main.py vs starter
    python run_game.py -o random
    python run_game.py -a agents/v1.py -o main.py --seed 7
    python run_game.py --render               # ascii render of each day boundary
"""

import argparse
import json
import os
import time


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-a", "--agent", default="main.py", help="agent .py path or builtin (default: main.py)")
    p.add_argument("-o", "--opponent", default="starter", help="opponent (default: starter)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--steps", type=int, default=720)
    p.add_argument("--render", action="store_true", help="print ascii board at each day boundary")
    p.add_argument("--replay", default="replays", help="directory for replay JSON (default: replays/)")
    args = p.parse_args()

    from kaggle_environments import make

    env = make(
        "kaggriculture",
        configuration={"episodeSteps": args.steps, "seed": args.seed},
        debug=True,
    )
    t0 = time.time()
    env.run([args.agent, args.opponent])
    elapsed = time.time() - t0

    final = env.steps[-1]
    for i, s in enumerate(final):
        name = args.agent if i == 0 else args.opponent
        try:
            money = s.observation["farms"][i]["money"]
        except Exception:
            money = s.reward
        print(f"Player {i} ({name}): money=${money:.0f} reward={s.reward} status={s.status}")

    print(f"\n{len(env.steps)} steps in {elapsed:.1f}s")

    if args.replay:
        os.makedirs(args.replay, exist_ok=True)
        path = os.path.join(args.replay, f"replay_seed{args.seed}_{int(t0)}.json")
        with open(path, "w") as f:
            json.dump(env.toJSON(), f)
        print(f"replay written to {path}")

    if args.render:
        # Re-render the final board state in ascii.
        print(env.render(mode="ansi"))


if __name__ == "__main__":
    main()
