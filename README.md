# Kaggle Farm — Kaggriculture dev workspace

Local setup for developing, testing, and benchmarking agents for the
[Kaggriculture](https://www.kaggle.com/competitions/kaggriculture) competition
(2-player farming sim in `kaggle-environments`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Layout

| File | Purpose |
|---|---|
| `main.py` | Your agent. Submission-ready: defines `agent(obs)`. Currently a wheat-farm baseline that hires hands (~$8.4k avg vs starter's ~$3.5k). |
| `server.py` + `web/` | Local web arena: pick/upload agents, watch games live, download replays. |
| `run_game.py` | Run one game, print final banks, dump a replay JSON. |
| `bench.py` | Benchmark harness: N seeded episodes vs any opponents, alternating sides, optional parallelism. |
| `agents/` | Drop extra agent `.py` files here — they show up in the web UI. |
| `replays/` | Replay JSONs land here (gitignored). |

## Web arena (live viewer)

```bash
python server.py          # then open http://localhost:5050
```

- Pick your agent (any `.py` in the workspace) or **upload** one, pick an
  opponent (built-ins `pass`/`random`/`starter` or any `.py`), set seed/speed,
  hit **Run**.
- The board renders live: crops by letter+color (`w`=wheat, `c`=carrot,
  `t`=tomato, `s`=strawberry, `m`=melon), `✕`=weed, `C`/`P`=coop/pasture,
  animals as `G`/`C`/`S`. Blue dot = farmer, orange = hired hands, 💧 = watered
  today, red inset = unwatered, dashed outline = shed-adjacent tiles.
- Speed slider = steps/sec; tick **max** to run at full speed (~2s/game).
- **⬇ replay** downloads the last episode JSON for the Kaggle visualizer.

## Develop / test

```bash
# One full game (720 steps, ~2s) with replay dump
python run_game.py -o starter
python run_game.py -o random --seed 7 --render

# Benchmark: 8 seeded episodes each vs starter + random, 4 workers
python bench.py -a main.py -o starter random -n 8 -j 4

# A/B test two versions of your agent (any .py path works as an opponent)
python bench.py -a agents/new.py -o agents/old.py -n 10 -j 4

# Save replays while benchmarking
python bench.py -a main.py -o starter -n 4 --save-replays replays/
```

Any `.py` file whose **last callable** is the agent function can be used as an
agent — the framework execs the file and calls the last defined callable.
Signatures may be `agent(obs)` or `agent(obs, config)`.

## Mechanics worth knowing (verified in the interpreter)

- **Same-day watering is mandatory.** A new plant starts with
  `consecutive_unwatered=1`; unwatered at end of day → weed overnight. Only
  plant when a turn remains to water (`hour <= 22`).
- **End-of-day is generous:** unit inventories auto-drop into the shed, the
  farmer teleports back to the NW shed tile (4,4), and hands/hire counts reset.
  Hands only live for the day they're hired.
- **Hands are cheap:** hire cost `fib(hires_today)` — the 1st–6th hires of a
  day cost 1,1,2,3,5,8 = $20 total for 6 extra actions/turn.
- **Market orders:** ≤10/player/turn, excess silently dropped. `SELL` pulls
  from the shed; `BUY_*`/`HIRE`/`BUY_LAND` are fixed-price.
- **Reproducible episodes:** `configuration={"seed": N}` seeds weeds/shops
  (bench.py does this; `seed` is also preserved in replay JSONs).
- **Reward = final bank.** Most money after 720 turns wins; ties possible.

## Gotcha: EPISODE_STEPS

If you test in a Kaggle notebook with `configuration={"episodeSteps": N}`,
keep `N = 720` (the competition default and value used in real matches). A
short season starves long-term strategies — e.g. at `episodeSteps=100` this
baseline finishes at ~$2.7k (it spends on seeds+hires before revenue lands)
while at 720 it reaches ~$8.5k. The agent reads `config["episodeSteps"]` and
stops planting/hiring when crops can no longer mature before season end, but
a 4-day season is simply too short to farm profitably.

## Submit

Kaggle MCP server is configured (user scope `~/.config/devin/mcp_config.json`,
OAuth'd — no token file needed). Just ask in chat: "submit main.py to
kaggriculture" and I'll use `start_competition_submission_upload` +
`submit_to_competition`. Useful MCP tools: `list_submission_episodes`,
`get_episode_replay`, `get_episode_agent_logs`, `get_competition_leaderboard`.

CLI equivalent (needs `~/.kaggle/access_token`):

```bash
kaggle competitions submit kaggriculture -f main.py -m "wheat baseline v1"
kaggle competitions submissions kaggriculture
kaggle competitions leaderboard kaggriculture -s
```

Multi-file: `tar -czf submission.tar.gz main.py helper.py ...` — but `main.py`
must still be at the tar root.

## References

- Game rules, crop/animal/shop tables, price function:
  `.venv/lib/python3.13/site-packages/kaggle_environments/envs/kaggriculture/README.md`
- Agent guide (AGENTS.md) sits in the same directory.
- Interpreter source (ground truth for mechanics): `kaggriculture.py` alongside.
