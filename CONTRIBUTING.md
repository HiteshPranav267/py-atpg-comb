# Contributing to PyATPG

Thanks for your interest. Bug reports, fixes, new netlist formats, better documentation and new rankers are all welcome.

## Reporting a problem

Open a GitHub issue with:

- what you ran (the CLI command, or the web app settings);
- the netlist, if you can share it, or its size and format if you cannot;
- the output or error message, and the `notes` field of the JSON report;
- your OS and Python version, and how ATALANTA was built.

## Setting up

```bash
git clone https://github.com/HiteshPranav267/py-atpg-comb.git
cd py-atpg-comb
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
```

Build ATALANTA as in the [README](README.md#install). For the web app, run `npm install` in `frontend/`.

## Where things are

- **Code layout:** [docs/architecture.md](docs/architecture.md).
- **The analysis pipeline in order:** [docs/how-it-works.md](docs/how-it-works.md).
- **Main entry point:** `AtpgAnalysisService.run()` in `pyatpg/atpg/service.py`. The CLI (`pyatpg/cli.py`) and the API route (`pyatpg/api/routes/atpg.py`) both call it.

## Making a change

1. Create a branch from `main`.
2. Keep each pull request to one change, and match the style of the code around it: type hints, docstrings that say *why*, no unexplained magic numbers.
3. Check your change by hand before opening the pull request:
   - **Python:** run the CLI on the [examples](examples/README.md) and compare with the expected results there. Use `--full-scan` for the sequential ones. If you changed ranking, targeting or the detectors, say in the pull request how the results moved.
   - **Frontend:** `npm run lint` and `npm run build` in `frontend/` must pass.
   - **API:** start the backend and open http://127.0.0.1:8000/docs to try the changed route.
4. Update the docs the change touches: the README, `docs/`, and the CLI `--help` text.
5. Open the pull request. Describe what changed, why, and how you checked it.

## Ground rules

- **No third-party code or data in the repository.** ATALANTA is fetched at build time, and its patch lives in `patches/`. Do not add netlists you are not free to redistribute. Trust-Hub benchmarks, for example, require registration.
- **No secrets.** Keys go in `.env`, which is git-ignored. Never commit them, and never put them in issues or pull requests.
- **Report numbers honestly.** Every metric in the report is read from the engine's own output, and a value an engine did not report shows as *not reported*, never as 0. Keep it that way.

## Licence

By contributing you agree that your contribution is released under the project's [MIT Licence](LICENSE).
