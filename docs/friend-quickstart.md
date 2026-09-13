# Running your circuits — quickstart

You've been handed a slice of circuits to test for the paper. This runs
on your own laptop — nothing to host — but the actual ATPG/LLM work
happens on the already-deployed backend, so setup here is light. Takes
about 2 minutes.

## 1. Get Python (skip if you already have it)

Download from [python.org/downloads](https://www.python.org/downloads/) —
get the latest 3.x version. **During install, check the box that says "Add
python.exe to PATH."** That's the one setting that matters.

## 2. Get the code

If you don't use git: go to the repo's GitHub page → green **Code** button →
**Download ZIP** → extract it somewhere.

If you do: `git clone <repo-url>`

## 3. Install the (small) dependencies

Open the extracted/cloned folder in a terminal (right-click inside it →
**Open in Terminal**, or open PowerShell and `cd` into it), then:

```powershell
pip install -r requirements-api-client.txt
```

That's `requests` and `networkx` — nothing else. You do **not** need
Atalanta, and you do **not** need an LLM SDK installed — those run on the
server, not your laptop.

## 4. Get your own LLM key (optional but recommended)

Free key from any of:
- [aistudio.google.com](https://aistudio.google.com) — Gemini, starts with `AIza`
- [platform.openai.com](https://platform.openai.com) — starts with `sk-`
- [console.anthropic.com](https://console.anthropic.com) — starts with `sk-ant-`
- [build.nvidia.com](https://build.nvidia.com) — NVIDIA NIM, starts with `nvapi-`

Skip this and it still runs, using structural heuristics instead of an LLM
for ranking/targeting.

## 5. Drop in your circuits

Put your `.v`, `.verilog`, or `.bench` circuit files into the
`benchmarks\incoming\` folder inside the repo.

## 6. Run it

```powershell
python scripts\run_via_api.py --circuits benchmarks\incoming --api-key YOUR_KEY_HERE
```

(Leave off `--api-key` to skip the LLM.) This uploads each circuit to the
already-deployed website's API, which generates trojan variants, runs
ATPG on all of them, and ranks them — you'll see it work through each one
in the terminal. Results save as it goes, so closing the window early
doesn't lose finished work.

To keep the window open and pick up new circuits automatically as you add
them (what "leave your laptop on" means in practice):

```powershell
python scripts\run_via_api.py --circuits benchmarks\incoming --api-key YOUR_KEY_HERE --watch 1800
```

That re-checks the folder every 30 minutes for as long as the window stays
open. `Ctrl+C` stops it; running it again later picks up right where it
left off (it never redoes circuits it already finished).

## 7. Where your results end up

`results\api_research\master_atpg_summary.csv` and
`results\api_research\master_ranking_summary.csv` — send these two files
back whenever you're asked for them.

## If something goes wrong

- **"pyatpg.automation.api_client needs the 'requests' package"** — run
  `pip install -r requirements-api-client.txt` again.
- **The first circuit takes a long time / seems stuck** — the backend may
  be waking up from sleep (it's on a free hosting tier that sleeps when
  idle); it can take 30-60 seconds to respond to the very first request.
  It'll proceed normally after that.
- **A circuit keeps failing** — the terminal says whether it'll auto-retry
  (a temporary server hiccup) or needs a look (something wrong with that
  specific circuit file).
- **LLM errors about quota/billing** — your API key ran out of free
  credits. Get a new key or add billing, then it picks back up
  automatically next time it runs — no need to redo anything.

## Prefer running everything fully offline instead?

If you'd rather not depend on the shared website at all (e.g. you want to
run Atalanta on your own machine), see the main
[docs/automation.md](automation.md) and `scripts/friend_setup.ps1` instead
— heavier to set up (installs the full dependency list, uses the Atalanta
binary already checked into the repo for Windows), but doesn't touch the
shared backend at all.
