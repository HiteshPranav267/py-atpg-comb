# 🚀 PyATPG Operational Workflow

This document outlines the end-to-end process for detecting Hardware Trojans (HT) using the PyATPG framework, covering both the **Web Interface** and the **Command Line (CLI)** workflows.

---

## 👥 Team Task Assignments (Sem 6 Phase)

The project is split into two specialized working groups to ensure equal distribution of complexity and development load.

### Group 1: Core Logic & AI Intelligence (Hitesh & Dibyadyuti)
*   **Focus:** The "Brain" of the framework.
*   **Tasks:**
    1.  **Netlist & Graph Engine:** Maintenance of the Verilog/Bench parsers and graph modeling (NetworkX).
    2.  **Structural Metrics:** Implementation of SCOAP, Centrality, and Fan-in/Fan-out depth calculations.
    3.  **LLM Prompt Engineering:** Developing the "Few-Shot" multimodal prompt system and the Knowledge Base (Caching/Memory).
    4.  **Heuristic Development:** Creating the deterministic fallback "Stealth Index" for offline analysis.

### Group 2: Automation, UI & Industrial Validation (Architha & Manas)
*   **Focus:** The "Body" and "Execution" of the framework.
*   **Tasks:**
    1.  **Cadence Automation:** Developing the TCL script generators for Genus (Synthesis) and Modus (ATPG).
    2.  **Remote Server Uplink:** Building the Paramiko-based SSH/SFTP bridge for secure college server communication.
    3.  **Web Dashboard:** Developing the Flask-based interactive UI, Terminal emulator, and real-time log streamer.
    4.  **Security Visualization:** Implementing the Gate Insight panel and the D3.js/Cytoscape security heatmaps.

---

## 🔍 Detailed AI-Driven Partial Execution Strategy

A primary research gap in Hardware Trojan (HT) detection is the **state-space explosion** of industrial ATPG. Running full-scan N-detect ATPG on a billion-transistor chip is computationally prohibitive. PyATPG solves this through **AI-Guided Fault Pruning**.

### 1. How LLMs are utilized
We utilize LLMs (GPT-4o, Gemini, or DeepSeek) not as simple classifiers, but as **Structural Reasoners**.
*   **The Support Set:** We feed the LLM a "Few-Shot" prompt containing 3-5 examples of known malicious logic structures (e.g., a Comparator-based trigger) vs. benign arithmetic logic.
*   **Multi-Modal Context:** The LLM receives raw structural data (SCOAP costs, logic depth) alongside semantic labels (e.g., `gate_123 is an AND gate with 10 fan-ins`).
*   **Reasoning:** The LLM identifies "Logic Islets"—clusters of gates that are hard to control (high CC) and hard to observe (high CO), which semantically resemble the "stealth" requirements of a Trojan trigger.

### 2. Partial Execution vs. Full Execution
Instead of asking Cadence Modus to find *every* possible fault in the circuit, we perform **Targeted Fault Space Pruning**:
1.  **Pruning:** If a circuit has 10,000 gates, the AI-Prioritizer flags only the top **1% (100 gates)** that exhibit high "Trojan-like" structural signatures.
2.  **Targeted ATPG:** We generate a custom `.flist` (fault list) containing only these 100 nodes.
3.  **The Result:** Because Modus only has to solve for 100 targets instead of 10,000, the **runtime is reduced by >90%**.
4.  **Better/Equal Results:** Since the LLM is trained on adversarial HT patterns, it prioritizes the "Needles in the Haystack" that standard tools often ignore or mark as "Don't Cares." We achieve high-quality detection of *malicious* logic while ignoring thousands of lines of *functional* logic.

### 3. Pinpointing the Error Node
When the Modus Oracle returns its report, the **Phase 4: Validation Engine** executes:
*   **The Verdict:** If the AI flagged `Gate_X` as high-risk, and Modus reports that `Gate_X` is **"Redundant"** or **"Undetectable"**, it confirms that the logic is intentionally hidden from standard test patterns.
*   **Heatmap Tracing:** The Web UI highlights `Gate_X` in **Bright Red**. 
*   **Back-Tracing:** Users can click on the node to view its **Logic Cone**. This shows the exact Fan-in paths, allowing designers to see exactly which inputs are triggering the suspicious behavior, pinpointing the Trojan source gate with 100% precision.

---

## 🏗️ The 4-Phase Detection Pipeline

PyATPG operates on a structured 4-phase pipeline designed to bridge AI reasoning with industrial EDA verification.

### Phase 1: Structural Analysis Engine
*   **Goal:** Convert a raw netlist into a searchable mathematical graph and extract security metrics.
*   **Process:**
    1.  **Parsing:** Supports Verilog (`.v`) and Bench (`.bench`) formats.
    2.  **Graph Construction:** Builds a directed graph of gates and wires.
    3.  **SCOAP Calculation:** Calculates Combinational Controllability (CC0/CC1) and Observability (CO).
    4.  **Toggle Simulation:** Runs 3000+ random patterns through a logic simulator to find "statistically rare" nodes.
*   **Output:** `outputs/gate_profiles.json` (Contextual features for each gate).

### Phase 2: AI Prioritization Brain
*   **Goal:** Identify the most suspicious "stealthy" gates that a human or standard tool might miss.
*   **Process:**
    1.  The system fuses structural SCOAP costs with dynamic toggle rates.
    2.  **LLM Inference:** Sends gate profiles to an LLM (GPT-4o/Gemini) to perform "Few-Shot" reasoning.
    3.  **Heuristic Fallback:** If no API key is provided, a deterministic "Stealth Index" heuristic is used.
*   **Output:** `outputs/targeted_faults.txt` (A prioritized list of potential Trojan trigger/payload gates).

### Phase 3: Oracle Execution (Actuator)
*   **Goal:** Orchestrate the commercial "Oracle" (Cadence Modus) to verify the AI's hypotheses.
*   **Process:**
    1.  **TCL Generation:** Automatically writes Cadence Genus (DFT) and Modus (ATPG) scripts.
    2.  **Bundling:** Creates a `.zip` job bundle containing the netlist and scripts.
    3.  **Remote Uplink:** Uploads the bundle via SSH/SFTP to the college server and executes the tools.
*   **Output:** Modus coverage reports and fault detail logs.

### Phase 4: Validation & Security Heatmap
*   **Goal:** Cross-reference Modus results with AI predictions to provide a final security verdict.
*   **Process:**
    1.  **Log Parsing:** Extracts fault coverage, pattern counts, and CPU time.
    2.  **Cross-Verification:** If a gate was flagged by AI AND reported as "Undetected/Redundant" by Modus, it is marked as a **Hardware Trojan Candidate**.
*   **Output:** `outputs/security_report.json` and interactive Heatmap.

---

## 💻 CLI Workflow (Headless)

For batch processing or remote server work, use the scripts in the `scripts/` directory.

### 1. Run the Full Pipeline
The easiest way to run all 4 phases in one go:
```bash
python scripts/run_pipeline.py benchmarks/c432.v --local-only
```
*Use `--api-key` to enable LLM analysis. Remove `--local-only` if you have the remote server credentials configured.*

### 2. Individual Phase Execution
You can also run phases manually for granular control:
```bash
# Phase 1
python scripts/run_phase1.py benchmarks/c432.v -o outputs/gate_profiles.json

# Phase 2
python scripts/run_phase2.py outputs/gate_profiles.json -o outputs/targeted_faults.txt

# Phase 3 (Generate Bundle)
python scripts/run_phase3.py benchmarks/c432.v outputs/targeted_faults.txt --local-only
```

---

## 🌐 Web UI Workflow (Interactive)

1.  **Start the Server:** Run `python app.py`.
2.  **Connect SSH:** Open `localhost:5000`, go to the **Terminal** tab, and enter server credentials to establish the uplink.
3.  **Load Circuit:** In the **Agentic** tab, upload your Verilog netlist.
4.  **Analyze:** Click **"Analyze HT"**. The graph will update with a color-coded heatmap showing suspicion levels.
5.  **Dispatch Job:** Click **"Dispatch to Modus"**. The system will bundle the job, upload it, and stream the remote logs to your terminal in real-time.
6.  **Visualize:** Switch to the **Visualizer** tab to inspect specific gates and their logic cones.

---

## 📂 Data Management

*   **Benchmarks:** Store new netlists in the `benchmarks/` folder.
*   **Reports:** All generated metrics are saved in `outputs/`.
*   **Modus Jobs:** Exported bundles for the college server are stored in `modus_jobs/`.

### 3. LLM Configuration & Open Source Support

PyATPG supports a variety of LLM providers. You can configure them via environment variables or the `LLMAnalyzer` class.

#### **Supported Providers:**
-   **OpenAI**: `provider='openai'`, model='gpt-4o'
-   **Anthropic**: `provider='anthropic'`, model='claude-3-5-sonnet'
-   **Kimi (Moonshot AI)**: `provider='kimi'`, model='moonshot-v1-32k'
-   **Ollama (Local)**: `provider='ollama'`, model='llama3.1' (Defaults to `http://localhost:11434/v1`)
-   **Custom**: Use any OpenAI-compatible API by providing a `base_url`.

#### **How to use Kimi:**
1.  Set your API Key: `export MOONSHOT_API_KEY=your_key_here`
2.  In `app.py` or your script, initialize the analyzer:
    ```python
    analyzer = LLMAnalyzer(provider='kimi', model='moonshot-v1-128k')
    ```

#### **How to use Local Open Source Models (Ollama):**
1.  Install [Ollama](https://ollama.com/).
2.  Pull a reasoning model: `ollama pull deepseek-coder-v2` or `ollama pull llama3.1`.
3.  Initialize:
    ```python
    analyzer = LLMAnalyzer(provider='ollama', model='deepseek-coder-v2')
    ```

---

## 🧠 Learning & Memory System

PyATPG now features a **Persistent Knowledge Base** to improve speed and accuracy over time.

1.  **Exact Match Cache:** If you load the exact same circuit netlist again, the system detects the SHA-256 hash match and skips the LLM/Heuristic phase entirely, returning results instantly.
2.  **Experience-Based Reasoning:** Every time an analysis is completed, a summary (Circuit Name, Size, and Suspect Count) is saved to `outputs/knowledge_base.json`. 
3.  **Cross-Circuit Context:** When analyzing a *new* circuit, these past experiences are fed into the LLM's system prompt. This allows the LLM to "remember" patterns it has seen in other parts of your benchmark suite, leading to more consistent and refined Trojan detection.
