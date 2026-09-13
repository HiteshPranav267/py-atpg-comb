# Unattended batch runs

`scripts/run_suite.py` runs the ATPG pipeline, a ranking benchmark, or the
full trojan-generation-through-ranking pipeline over a batch of circuits
without you babysitting it: it retries transient failures (an LLM 503/429,
a dropped connection) with backoff, skips circuits it has already
finished, notifies you (Discord/email) when something needs a human, and
picks up cleanly wherever a previous run stopped — whether that was a
clean finish, a crash, or you closing the laptop.

It exists for the "my friends keep forgetting to run this / I want
overnight numbers for the paper" workflow: drop circuit files somewhere,
let it run unattended, come back to result tables.

## Quick start

For a paper: generate trojan variants, run ATPG, and rank them against
ground truth, all per circuit dropped into `benchmarks/incoming/`:

```bash
python scripts/run_suite.py research --circuits benchmarks/incoming \
    --llm-provider gemini --api-key "$GEMINI_API_KEY"
```

Or the individual pieces, if that's all you need:

```bash
# ATPG only, everything in benchmarks/incoming/, deterministic-only.
python scripts/run_suite.py atpg --circuits benchmarks/incoming --no-llm

# ATPG only, with an LLM (any provider — see pyatpg/ranking/providers.py):
python scripts/run_suite.py atpg --circuits benchmarks/incoming \
    --mode compare --llm-provider gemini --api-key "$GEMINI_API_KEY"
```

Results land in `results/suite/` for `atpg` or `results/research/` for
`research` (override either with `--output-dir`). For `research`:

- `master_atpg_summary.csv` / `master_ranking_summary.csv` — every circuit
  rolled together, ready to pull into a paper table.
- `<circuit>/dataset/` — the generated trojan variants; `<circuit>/atpg/` and
  `<circuit>/ranking/` — that circuit's detailed results.
- `manifest.json` files (one per stage) — resumability bookkeeping; don't
  hand-edit them, but they're plain JSON if you want to inspect what happened.
- `notifications.log` — every notification ever sent, even ones a Discord/email
  channel silently dropped (e.g. no internet at the time).

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
| Circuit failed with a **credentials** error (LLM out of quota, key invalid/revoked) | Re-run, with a fresh retry budget — swapping the key or topping up billing takes effect on the next run, no `--force` needed |
| Circuit failed with a **permanent** error (malformed netlist, combinational cycle Atalanta can't levelize) | Left failed — retrying won't fix a bad netlist |
| The run was killed mid-circuit | That circuit shows as retryable, same as a transient failure |

Credentials failures are detected by matching common phrasing across
providers (`insufficient_quota`, `credit balance`, `401`/`403`, ...) —
heuristic, not a guarantee for every vendor's exact wording, but a miss
just falls back to being treated as a plain transient failure (retried
regardless, just without the "swap your key" notification).

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
    -Argument "scripts\run_suite.py research --circuits benchmarks\incoming" `
    -WorkingDirectory "C:\Users\Hitesh\Documents\projects\py-atpg-comb"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName "PyATPG-Suite" -Action $action -Trigger $trigger
```

The API key is deliberately **not** on that command line — Task Scheduler
logs command lines in places you don't want a key sitting in. Set it (and
optionally `NOTIFY_DISCORD_WEBHOOK` / `NOTIFY_EMAIL_TO`) as a system or
user environment variable once, in a normal PowerShell window:

```powershell
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "AIza...", "User")
```

(or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `NVIDIA_API_KEY` / the generic
`LLM_API_KEY`+`LLM_PROVIDER` — whichever provider you're using). Close and
reopen any terminal afterward; the scheduled task picks it up automatically
since it inherits your user environment.

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

## The full research pipeline

`research` is the one-command version of "give it a clean circuit, get
everything a paper needs back": per circuit, it

1. generates trojan variants with **every** insertion strategy
   (`random`, `rare_node`, `low_scoap` — override with `--strategies`),
   `--variants-per-strategy` attempts each (default 10);
2. runs the ATPG pipeline over the clean circuit and every variant
   (`run_atpg_suite` under the hood — same retries, same resumability);
3. runs a ranking benchmark (`--ranker`, default `LLMRanker`) comparing the
   ranker's guesses against the ground truth the generator recorded.

```bash
python scripts/run_suite.py research --circuits benchmarks/incoming \
    --strategies random rare_node low_scoap --variants-per-strategy 10 \
    --llm-provider gemini --api-key "$GEMINI_API_KEY" --seed 42
```

`--seed` fixes *where* trojans get inserted, so the dataset is reproducible
across runs (and so `--force` regenerates the *same* dataset rather than a
different random one — useful if you want to redo just the ranking step
with a different `--ranker` against unchanged data).

Per-circuit output lives under `<output-dir>/<circuit-stem>/` (`dataset/`,
`atpg/`, `ranking/`, each with their own `manifest.json`); the two
`master_*_summary.csv` files at the top level are every circuit rolled
together. They're kept as two files rather than one merged table because
the ranking benchmark scores once per *strategy folder*, not once per
individual trojan variant inside it — a single joined row would claim
more precision than the underlying data has.

If a circuit is too small for a strategy to find a valid insertion point,
that strategy just generates fewer variants (or none) for it — visible in
the run's log output, not a failure.

## Ranking benchmarks only

If you already have a trojan dataset and just want the ranking step, same
idea as `research`'s step 3 on its own, for
`pyatpg.evaluation.benchmark.BenchmarkRunner`:

```bash
python scripts/run_suite.py ranking --dataset-dir path/to/trojan_dataset \
    --ranker LLMRanker --llm-provider anthropic --api-key "$ANTHROPIC_API_KEY"
```

This retries the whole dataset (not per-circuit — `BenchmarkRunner` iterates
its dataset internally) and records one manifest entry for the dataset as a
whole, so a completed benchmark isn't silently re-run; `--force` redoes it.

## Notifications

Set these (as user/system environment variables, same as an LLM key) to get
pinged instead of only finding out by checking `results/` later:

- `NOTIFY_DISCORD_WEBHOOK` — a Discord incoming-webhook URL (channel
  Settings → Integrations → Webhooks → New Webhook → copy URL).
- `NOTIFY_EMAIL_TO` (comma-separated for multiple people) plus
  `NOTIFY_EMAIL_SMTP_HOST` (required once `_TO` is set), `_SMTP_PORT`
  (default 587), `_SMTP_USER`, `_SMTP_PASS`, `_FROM` (defaults to `_USER`).
  Most providers (Gmail, Outlook...) want an **app password** here, not
  your normal login password.

Both are optional and independent — set one, both, or neither. `--no-notify`
disables Discord/email even if they're configured (console output and
`notifications.log` still happen regardless).

What triggers a notification, and how often:

- A circuit's LLM account needs attention (out of quota, invalid key) —
  **urgent**, fires once when it first happens, not on every subsequent
  scheduled run that finds it still broken.
- A circuit is still failing after exhausting its retries this run (a
  503/429 that outlasted the backoff) — informational, same
  once-per-distinct-failure rule.
- The Atalanta binary itself is missing (the whole run stops) — urgent.
- A circuit recovers after previously failing — informational.
- A `research` run finishes — a one-line digest (circuits complete /
  still failing / skipped).

A circuit that keeps failing the *same* way tick after tick does **not**
re-notify every 15 minutes — only a change in what's wrong (or fixing it)
fires again. This is deliberate: a channel that pings every 15 minutes for
the same known issue gets muted, defeating the point.

If several friends are each running their own slice of circuits, each
person's own `NOTIFY_*` env vars (or a shared Discord webhook, if you want
one channel for the whole group) is the natural way to have everyone get
their own "your circuits are done" / "your key ran out" pings.

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
