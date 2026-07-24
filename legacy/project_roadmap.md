# PyATPG Capstone: Roadmap & Reality Check (Revised)

## 🛑 Reality Check & The "Smart Pivot" (As of March 24, 2026)
**Deadline:** October 20, 2026 (Approx. 7 months remaining)

### The Brilliant Pivot 🔄
You have correctly identified that building a robust, commercial-grade ATPG from scratch is nearly impossible for a capstone (and unnecessary). **The industry uses Cadence Modus.** 

Therefore, PyATPG is pivoting to an **Oracle-Guided Learning Methodology**. 

Because an open-source tool shouldn't require a $100k Cadence license to run in the future, we will use Cadence Modus strictly as a **"Teacher" (Oracle)** *during development only*:
1. **Oracle Training Phase (Present):** We generate bulk TCL scripts and run Trust-Hub circuits through Modus on your college server. We extract the exact fault coverage, test patterns, and Trojan detection rates.
2. **Knowledge Transfer:** We use those exact Modus telemetry logs to fine-tune our LLM prompts, train our models, and improve the native Python `atpg_hybrid.py` engine to mimic Modus's efficiency.
3. **Standalone Open-Source Delivery (Future):** The final PyATPG version severs the Modus dependency. It uses its newly "Modus-trained" AI heuristics paired with its native Python engine, remaining 100% independent and usable by anyone without a Cadence license!

### What You Have Done (The Foundation) ✅
1. **Parsers:** You can read `.v` and `.bench` files, translating them into NetworkX graphs.
2. **The Web UI:** You have a gorgeous Cytoscape.js frontend for analyzing gates and visualizing circuit metrics, which is perfect for this new direction.
3. **Toggle Rate Simulator:** ✅ `graph_analysis.py` computes per-gate toggle rates via random pattern simulation (5000 vectors).
4. **SCOAP Metrics Engine:** ✅ Full CC0, CC1, CO computation with topological traversals.
5. **LLM Analyzer Module (`llm_analyzer.py`):** ✅ Supports OpenAI & Anthropic APIs with few-shot prompting, plus an `OfflineLLMAnalyzer` heuristic fallback.
6. **Trojan Heatmap Visualization:** ✅ "Run Trojan Analysis" button on the UI triggers the offline/LLM analyzer and dynamically colors suspect nodes on the Cytoscape graph (Red > 0.7, Orange > 0.5, Yellow > 0.3). Clicking a flagged gate card zooms into it.
7. **Modus Job Exporter (`modus_exporter.py`):** ✅ Generates TCL scripts + fault lists and exports them as downloadable `.zip` Job Bundles.
8. **Modus Log Parser (`modus_log_parser.py`):** ✅ Parses Modus coverage reports, pattern counts, CPU time, and generates comparison metrics (AI-Guided vs Baseline).
9. **LLM Feature Profiles:** ✅ Per-gate JSON profiles with SCOAP, toggle rate, fan-in/out, level, and 2-hop neighborhood for few-shot prompting.

### What Is Still Missing ❌
1. **Modus Environment Config (Blocked):** Waiting on college server details — launch command, cell library paths, batch scheduler info, and a "Hello World" `.tcl` + log file.
2. **Trust-Hub Benchmark Collection:** Need to download and integrate 50+ Hardware Trojan benchmark circuits from Trust-Hub.
3. **Headless Experiment Loop (`run_experiments.py`):** Automate batch Modus runs across all Trust-Hub circuits.
4. **LLM Fine-Tuning with Oracle Data:** Use Modus ground-truth results to improve the standalone offline heuristic weights.

---

## 📅 Project Gantt Chart

```mermaid
gantt
    title PyATPG -> Modus Wrapper Roadmap (Mar 24 - Oct 20)
    dateFormat  YYYY-MM-DD
    axisFormat  %b %Y
    
    section Ph 1: Feature Extraction ✅
    Toggle Rate Simulator / Estimator      :done, a1, 2026-03-24, 2026-03-27
    Graph Feature Engineering (SCOAP)      :done, a2, 2026-03-24, 2026-03-27
    JSON Profile Generator to LLMs         :done, a3, 2026-03-24, 2026-03-27

    section Ph 2: AI & UI Visuals ✅
    LLM API Wrapper (OpenAI/Anthropic)     :done, b1, 2026-03-24, 2026-03-27
    Offline Heuristic Analyzer             :done, b1b, 2026-03-24, 2026-03-27
    UI: Trojan Heatmap + Suspect Nodes     :done, b3, 2026-03-24, 2026-03-27
    Modus Job Exporter + Log Parser        :done, b4, 2026-03-24, 2026-03-27

    section Ph 3: Training Oracle (Cadence)
    Get Server Environment Details         :active, c0, 2026-03-27, 2026-04-05
    Validate TCL Scripts on Server         :c1, 2026-04-06, 2026-04-15
    Job Transfer & Remote Execution        :c2, 2026-04-16, 2026-04-30
    Oracle Log Parser & LLM Fine-tuning    :c3, 2026-05-01, 2026-05-31

    section Ph 4: Benchmarks
    Gather Trust-Hub HT Circuits           :d1, 2026-06-01, 2026-06-07
    Headless Experiment Loop (100 runs)    :d2, 2026-06-08, 2026-07-31
    Baseline vs Guided Testing Protocol    :d3, 2026-08-01, 2026-08-31

    section Ph 5: Final Polish
    Data Analysis & CSV Charts             :e1, 2026-09-01, 2026-09-15
    Modus Comparisons & Heatmaps UI        :e2, 2026-09-16, 2026-09-30
    Write Final Thesis & Presentation      :e3, 2026-10-01, 2026-10-20
```

---

## 👥 Task Allocation Matrix (Team of 4)

| Task | Assignee | Est. Effort | Dependency | Describe |
| :--- | :--- | :--- | :--- | :--- |
| **P1: Toggle Tracker / Metrics** | Member A | High | `simulator.py` | Finalize logic simulator to collect random vector toggle rates OR parse them directly from a standard logic simulator tool. |
| **P1: Advanced Graphing** | Member B | Med | `graph_analysis.py`| Calculate pathway depths, SCOAP, and generate the `[CC0, CO, ToggleRate]` JSON feature profile. |
| **P2: LLM Integrator** | Member C | Med | P1 Features | Write `llm_analyzer.py` to auto-POST features to the LLM and parse the returned Trojan probabilities. |
| **P2: Dynamic UI Heatmaps** | Member D | Med | P2 AI | Update `app.js` and `index.html` to visually highlight LLM predictions and Modus coverage heatmaps directly on the circuit graph. |
| **P3: Oracle Script Gen.** | Member A | High | P2 AI | Build python functions that package `.tcl` scripts into a zip file meant for the college server to extract ground-truth Modus data. |
| **P3: LLM Fine-Tuner** | Member B | High | P3 Scripts | Write a backend parser that reads Modus's log files and uses that exact data to fine-tune the LLM/Native PyATPG engine. |
| **P4: Prompt Engineering** | Member C | High | P1 Features | Tune the LLM prompt with the Modus-verified Ground Truth examples from Trust-Hub to improve standalone detection rates. |
| **P4: Headless Modus Loop** | Member D | Med | P3 Modus Env | Build `run_experiments.py` to silently loop through 50 Trust-Hub circuits on the college server to mass-generate Oracle data. |
| **P5: Diagnostic Runner** | Member A & B | High | P4 Setup | Bulk transfer bundles to remote server, run Modus overnight, and download the logs back to local PC for the final "knowledge transfer". |
| **P5: Comparison Models** | Member C | Med | P5 Runner | Compute metrics comparing "Blind Modus" vs "AI-Guided Modus" (e.g., 90% fewer patterns needed to flag the Trojan). |
| **P5: Write Thesis** | All Members | High | All Previous | Assemble the charts, screenshots of the UI, Modus heatmaps, and write the final document. |
