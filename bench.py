#!/usr/bin/env python3
"""Kaggriculture benchmark harness.

Runs N episodes of your agent against one or more opponents and reports
win rate, average bank, and average margin. Alternates which side your
agent plays to cancel any first-mover advantage.

Examples:
    python bench.py                              # main.py vs starter, 4 eps
    python bench.py -a main.py -o starter random -n 8
    python bench.py -a main.py -o starter -n 8 -j 4
    python bench.py -a main.py -o starter -n 4 --save-replays replays/
    python bench.py -a agents/v1.py -o agents/v2.py -n 10   # self-play A/B
"""

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor


def play_episode(job):
    """Run one episode. `job` is a plain tuple so it is picklable for -j > 1."""
    agent, opponent, seed, steps, swap, replay_dir, tag = job
    from kaggle_environments import make

    lineup = [opponent, agent] if swap else [agent, opponent]
    me_idx = 1 if swap else 0

    env = make(
        "kaggriculture",
        configuration={"episodeSteps": steps, "seed": seed},
        debug=False,
    )
    env.run(lineup)

    final = env.steps[-1]
    money = []
    status = []
    for i, s in enumerate(final):
        try:
            money.append(float(s.observation["farms"][i]["money"]))
        except Exception:
            money.append(float(s.reward) if s.reward is not None else 0.0)
        status.append(str(s.status))

    if replay_dir:
        os.makedirs(replay_dir, exist_ok=True)
        path = os.path.join(replay_dir, f"{tag}_seed{seed}_{'swapped' if swap else 'normal'}.json")
        with open(path, "w") as f:
            json.dump(env.toJSON(), f)

    return {
        "seed": seed,
        "swap": swap,
        "my_money": money[me_idx],
        "opp_money": money[1 - me_idx],
        "my_status": status[me_idx],
        "opp_status": status[1 - me_idx],
    }


def bench(agent, opponents, n, steps, jobs, seed0, replay_dir):
    jobs_list = []
    for opp in opponents:
        for i in range(n):
            jobs_list.append(
                (agent, opp, seed0 + i, steps, i % 2 == 1, replay_dir, _tag(agent) + "_vs_" + _tag(opp))
            )

    t0 = time.time()
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(play_episode, jobs_list))
    else:
        results = [play_episode(j) for j in jobs_list]
    elapsed = time.time() - t0

    print(f"\nagent={agent}  episodes/opponent={n}  steps={steps}  ({elapsed:.0f}s)\n")
    for opp in opponents:
        rs = [r for r, j in zip(results, jobs_list) if j[1] == opp]
        wins = sum(1 for r in rs if r["my_money"] > r["opp_money"])
        ties = sum(1 for r in rs if r["my_money"] == r["opp_money"])
        losses = len(rs) - wins - ties
        errs = sum(1 for r in rs if r["my_status"] != "DONE")
        margins = [r["my_money"] - r["opp_money"] for r in rs]
        print(f"vs {opp:<12} W{wins} T{ties} L{losses}  "
              f"avg ${statistics.mean(r['my_money'] for r in rs):8.0f}  "
              f"opp ${statistics.mean(r['opp_money'] for r in rs):8.0f}  "
              f"margin ${statistics.mean(margins):+8.0f}"
              + (f"  ⚠ {errs} error(s)" if errs else ""))
        for r in rs:
            print(f"    seed={r['seed']:<4} {'P2' if r['swap'] else 'P1'}  "
                  f"me=${r['my_money']:8.0f}  opp=${r['opp_money']:8.0f}  "
                  f"status={r['my_status']}/{r['opp_status']}")
    print()


def _tag(spec):
    base = os.path.basename(str(spec))
    return base[:-3] if base.endswith(".py") else base


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-a", "--agent", default="main.py", help="your agent: .py path or builtin name (default: main.py)")
    p.add_argument("-o", "--opponent", nargs="+", default=["starter"], help="opponent(s): builtin names or .py paths")
    p.add_argument("-n", "--episodes", type=int, default=4, help="episodes per opponent (default: 4)")
    p.add_argument("--steps", type=int, default=720, help="episodeSteps (default: 720 = full season)")
    p.add_argument("-j", "--jobs", type=int, default=1, help="parallel worker processes")
    p.add_argument("--seed", type=int, default=0, help="base seed; episode i uses seed+i")
    p.add_argument("--save-replays", metavar="DIR", default=None, help="write replay JSONs to DIR")
    args = p.parse_args()

    bench(args.agent, args.opponent, args.episodes, args.steps, args.jobs, args.seed, args.save_replays)


if __name__ == "__main__":
    main()
