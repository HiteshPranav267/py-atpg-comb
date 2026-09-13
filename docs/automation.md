# Unattended batch runs

`scripts/run_suite.py` runs the ATPG pipeline (or a ranking benchmark) over a
batch of circuits without you babysitting it: it retries transient failures
(an LLM 503/429, a dropped connection) with backoff, skips circuits it has
already finished, and picks up cleanly wherever a previous run stopped —
whether that was a clean finish, a crash, or you closing the laptop.

It exists for the "my friends keep forgetting to run this / I want overnight
numbers for the paper" workflow: drop circuit files somewhere, let it run
unattended, come back to a `summary.csv`.

## Quick start

```bash
# One-off: everything in benchmarks/incoming/, deterministic-only.
python scripts/run_suite.py atpg --circuits benchmarks/incoming --no-llm

# With an LLM (any provider — see pyatpg/ranking/providers.py):
python scripts/run_suite.py atpg --circuits benchmarks/incoming \
    --mode compare --llm-provider gemini --api-key "$GEMINI_API_KEY"
```

Results land in `results/suite/` (override with `--output-dir`):

- `summary.csv` — one row per finished circuit, ready to pull into a paper table.
- `<circuit>.json` — the full `AtpgAnalysis` payload for that circuit.
- `manifest.json` — resumability bookkeeping; don't hand-edit it, but it's
  plain JSON if you want to inspect what happened.

Run the exact same command again and it only processes circuits that are new,
changed, or still retryable — see **Resumability** below.

## Giving it circuits without touching the command line

`benchmarks/incoming/` is a plain folder for this: anyone (you, a
collaborator, a script) drops a `.v`, `.verilog` or `.bench` file in there,
and the next run of the suite (manual, scheduled, or `--watch`) picks it up.
Nothing needs to be registered anywhere. Files already in `benchmarks/` are
untouched — point `--circuits` there too if you want the whole suite in one
run: `--circuits benchmarks benchmarks/incoming`.

## Resumability

Every circuit gets one record in `manifest.json`, keyed by its path and a
cheap fingerprint (file size + modified time):

| Situation | What happens on the next run |
|---|---|
| Circuit finished successfully, file unchanged | Skipped |
| Circuit finished successfully, file edited since | Re-run (fingerprint changed) |
| Circuit failed with a **transient** error (LLM 503/429, network blip, Atalanta hiccup) | Re-run, with a fresh retry budget |
| Circuit failed with a **permanent** error (malformed netlist, combinational cycle Atalanta can't levelize) | Left failed — retrying won't fix a bad netlist |
| The run was killed mid-circuit | That circuit shows as retryable, same as a transient failure |

Pass `--force` to ignore all of that and redo everything.

This means "keep retrying until it stops giving me 503s" doesn't need a
single very-long-running process — a circuit that exhausts its retry budget
*this run* (`--max-attempts`, default 6, with exponential backoff up to
`--max-delay` seconds) is simply retried again, with a full fresh budget,
the *next* time the suite runs. Scheduling it every 15–30 minutes (see
below) gives it, in effect, unlimited persistence against a rate limit that
clears up within a few hours — which covers essentially every real 503.

## Running it unattended

Two ways to do the "set it and forget it" part; pick one.

### Option A — scheduled, recurring (recommended)

The suite is cheap to invoke repeatedly (it skips finished work), so a
short-interval scheduled task is the robust choice: if your machine sleeps,
reboots, or the script crashes, the next scheduled tick just picks up where
things stood.

**Windows (Task Scheduler)**, running every 15 minutes:

```powershell
$action = New-ScheduledTaskAction -Execute "python.exe" `
    -Argument "scripts\run_suite.py atpg --circuits benchmarks\incoming --mode compare --llm-provider gemini --api-key YOUR_KEY" `
    -WorkingDirectory "C:\Users\Hitesh\Documents\projects\py-atpg-comb"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName "PyATPG-Suite" -Action $action -Trigger $trigger
```

Or without a key on the command line (safer — reads it from an environment
variable set on the task instead):

```powershell
$action = New-ScheduledTaskAction -Execute "python.exe" `
    -Argument "scripts\run_suite.py atpg --circuits benchmarks\incoming --mode compare" `
    -WorkingDirectory "C:\Users\Hitesh\Documents\projects\py-atpg-comb"
```
and set `GEMINI_API_KEY` (or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` /
`NVIDIA_API_KEY` / the generic `LLM_API_KEY`+`LLM_PROVIDER`) as a system or
user environment variable first, so the task picks it up without the key
ever appearing in Task Scheduler's own history/logs.

Check on it: `Get-ScheduledTaskInfo -TaskName "PyATPG-Suite"`; stop it with
`Unregister-ScheduledTask -TaskName "PyATPG-Suite" -Confirm:$false`.

**Linux/macOS (cron)**, every 15 minutes:

```cron
*/15 * * * * cd /path/to/py-atpg-comb && /usr/bin/python3 scripts/run_suite.py atpg --circuits benchmarks/incoming --mode compare >> logs/suite.log 2>&1
```

### Option B — one long-running process (`--watch`)

If you'd rather have one process that just keeps running (e.g. on a lab
server you leave on), `--watch SECONDS` loops forever, re-scanning
`--circuits` on that interval:

```bash
python scripts/run_suite.py atpg --circuits benchmarks/incoming --watch 900
```

The tradeoff versus Option A: if this process dies (crash, reboot, laptop
sleep), nothing restarts it automatically — you'd need to also wrap it in a
service manager (Task Scheduler's "at startup" trigger, systemd, `pm2`,
`nohup` + a supervisor) to get the same resilience Option A gets for free.
For an unattended paper-deadline run on a machine you don't want to babysit,
Option A is the safer default.

## Ranking benchmarks

Same idea, for `pyatpg.evaluation.benchmark.BenchmarkRunner` (scoring gate
rankings against a trojan dataset's ground truth) instead of the ATPG
pipeline:

```bash
python scripts/run_suite.py ranking --dataset-dir path/to/trojan_dataset \
    --ranker LLMRanker --llm-provider anthropic --api-key "$ANTHROPIC_API_KEY"
```

This retries the whole dataset (not per-circuit — `BenchmarkRunner` iterates
its dataset internally) and records one manifest entry for the dataset as a
whole, so a completed benchmark isn't silently re-run; `--force` redoes it.

## Tuning the retry behavior

- `--max-attempts N` (default 6) — retries per circuit *within one
  invocation* before it's marked retryable-later instead.
- `--base-delay SECONDS` (default 15) — delay before the first retry;
  doubles each attempt after that.
- `--max-delay SECONDS` (default 300) — cap on that backoff.

With the defaults, one invocation retries a stuck circuit over roughly
15+30+60+120+240 ≈ 8 minutes before giving up until the next scheduled run.

## Exit codes

`0` — everything that ran succeeded (or was skipped as already done).
`1` — no circuit files matched `--circuits` at all.
`2` — at least one circuit failed (permanently, or exhausted its retries) —
useful in a CI/cron context to distinguish "nothing to do" from "something
needs attention", via `$LASTEXITCODE` in PowerShell or `$?` in a shell script.
