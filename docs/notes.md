# Research Notes for IEEE Format Paper
> Comprehensive notes and outline tailored for writing an academic paper (e.g., IEEE conference/journal format) on Hardware Trojan Detection using LLMs and Oracle-Guided ATPG.

---

## I. INTRODUCTION

**1. Motivation**
*   **The Hardware Security Threat:** The globally distributed semiconductor supply chain (IP vendors, foundries, assembly) introduces zero-trust environments.
*   **Hardware Trojans (HTs):** Malicious, stealthy modifications. They remain dormant (trigger) until exceptional conditions occur, then execute an attack (payload), such as leaking keys or denial of service.
*   **The Detection Bottleneck:** Traditional ATPG (Automatic Test Pattern Generation) generates tests for manufacturing faults (stuck-at faults), not maliciously inserted, rare-condition Trojans.

**2. Limitations of Current State-of-the-Art**
*   *Traditional & Statistical ATPG (e.g., LATENT 2024):* While identifying rare nodes via SCOAP is effective, it suffers from severe time complexity and poor scalability when processing large designs without an intelligent filter.
*   *Pure LLM & RTL Methods (e.g., TrojanWhisper 2024, TrojanLoC 2025):* Prove LLMs outperform GNNs, but they operate at the RTL level—making them vulnerable to simple obfuscation and blind to post-synthesis supply-chain attacks.
*   *Adversarial AI (e.g., TrojanForge 2024):* Attackers are now using Reinforcement Learning (RL) to craft stealthy Trojans that evade static structural heuristics, necessitating dynamic, intelligent defenders.

**3. Proposed Novelty (Our Contribution)**
*   A novel **Oracle-Guided, Few-Shot LLM-Driven Agentic Framework** operating directly at the **Gate-Level Netlist**.
*   We extract multimodal structural features (SCOAP, Centrality, Toggle Rates) to bypass code-obfuscation vulnerabilities.
*   Following the paradigm of **Autonomous EDA Agents (e.g., ChatEDA 2024, COVERT 2026)**, our framework implements an end-to-end agentic workflow to automatically orchestrate Cadence Modus (the Oracle) to verify the LLM's hypotheses.
*   This drastically reduces pattern count, eliminating the scalability bottleneck of statistical ATPG while combating adversarial insertions.

---

## II. BACKGROUND AND RELATED WORK

### A. The Evolution of ATPG and Statistical Rareness
Traditional ATPG surveys establish that "blind coverage" targets random reliability defects, not adversarial insertions. Modern statistical frameworks like **LATENT (2024)** demonstrate that focusing on rare nodes (via SCOAP) improves detection but suffers from massive search spaces. Our approach uses an LLM to prune this search space, targeting only semantically suspicious rare nodes.

### B. Large Language Models in Hardware Security
Recent works like **TrojanWhisper (2024)** and **TrojanLoC (2025)** validate that LLMs possess strong reasoning capabilities for HT detection, outperforming traditional Graph Neural Networks (GNNs). However, their reliance on pre-synthesis RTL code leaves them blind to deep payloads and vulnerable to variable obfuscation. We address this by applying Multimodal Few-Shot Learning (as theorized in recent 2022 FSL surveys) directly to gate-level structural graphs.

### C. The Rise of Agentic EDA Workflows
The automation of complex EDA tasks via autonomous agents is a critical emerging paradigm, demonstrated by tools like **ChatEDA (2024)** for design and **COVERT (2026)** for test program generation. By extending this agentic philosophy to hardware security, our framework eliminates error-prone manual TCL scripting and enables closed-loop, Oracle-guided Trojan verification against state-of-the-art adversaries like **TrojanForge (2024)**.

---

## III. PROPOSED METHODOLOGY

This section outlines the proposed framework architecture. The system operates in four distinct phases: (1) Graph Modeling, (2) Structural Feature Extraction, (3) LLM Suspicion Inference, and (4) Oracle-Guided Pattern Generation.

### A. Graph Extraction and Modeling ✅ *Implemented*
**Algorithm 1: Netlist to Directed Graph Transformation** — `graph_analysis.py: CircuitGraph._build()`
1. **Input:** Gate-level netlist file (e.g., ISCAS-85 `.bench` format).
2. **Process:** 
   *   Parse logic gates (AND, OR, NAND, NOR, XOR, NOT, BUF).
   *   Map Primary Inputs (PI) and Primary Outputs (PO).
   *   Construct a directed acyclic graph $G = (V, E)$ where vertices $V$ represent logic gates/ports and edges $E$ represent wire connections.
3. **Output:** Navigable topological graph representation.

### B. Structural Feature Extraction ✅ *Implemented*
To locate stealthy Trojan triggers, we extract heuristic metrics indicating "rareness" for every node $v \in V$.

**1. Controllability and Observability (SCOAP):** — `graph_analysis.py: compute_scoap()`
Calculated using linear time topological traversals.
*   **CC0 / CC1:** Combinational Controllability (difficulty of driving a node to 0 or 1).
*   **CO:** Combinational Observability (difficulty of propagating a node's value to a PO).
*   *Relevance:* Trojans intentionally utilize nodes with exceptionally high CC0/CC1 (hard to trigger) and CO (hard to observe payloads).

**2. Dynamic Toggle Rate Estimation:** — `graph_analysis.py: compute_toggle_rates(num_patterns=5000)`
**Algorithm 2: Random Pattern Simulation**
1. Generate $N$ random Boolean input vectors (e.g., $N=5000$).
2. Simulate the logic state of $G$ for each vector.
3. For each node $v$, calculate $ToggleRate(v) = \frac{\text{Transitions } (0 \to 1 \text{ or } 1 \to 0)}{N-1}$.
*   *Relevance:* Nodes with $ToggleRate < 0.05$ are considered statistically rare.

**3. Topological Depth and Fan-in/Fan-out:**
Extracted via Breadth-First Search (BFS) to determine the logic level from PIs. Deeply nested logic with low fan-out is a common signature of Trojan payloads avoiding accidental observation.

### C. LLM-Based Suspicion Inference ✅ *Implemented*
Instead of training a GNN, the extracted features are serialized into compact JSON profiles. — `llm_analyzer.py`

**Algorithm 3: Zero-Shot Heuristic Scoring** — `LLMAnalyzer.analyze_circuit()` + `OfflineLLMAnalyzer`
1. **Input:** JSON profiles containing `[gate_type, level, fan_in, fan_out, CC0, CC1, CO, toggle_rate, neighborhood]`.
2. **Prompt Engineering:** A system prompt constraints the LLM to act as a hardware security expert, evaluating the structural profile against known Trojan insertion strategies (e.g., "high control difficulty combined with low structural activity"). Includes 3 few-shot examples of known Trojan signatures.
3. **Output:** A continuous Suspicion Score $S \in [0, 1]$ for each gate, along with a textual reasoning explanation. Gates exceeding a defined threshold $\theta = 0.3$ are flagged as $\mathbb{S}_{suspect}$.

**Offline Heuristic Fallback (Implemented):** — `OfflineLLMAnalyzer`
If an API is unavailable, a weighted linear combination of normalized features is used:
$S = w_1 \cdot \text{norm}(CC0+CC1) + w_2 \cdot \text{norm}(CO) + w_3 \cdot (1 - ToggleRate) + w_4 \cdot \text{depth} + w_5 \cdot \text{hidden\_gate}$

### D. Oracle-Guided Pattern Generation ✅ *Implemented (Exporter ready; awaiting Modus server config)*
The final step bridges AI heuristics with deterministic EDA tools. — `modus_exporter.py`

**Algorithm 4: ATPG Targeting** — `ModusExporter.generate_tcl()` + `generate_fault_list()`
1. **Input:** Set of flagged suspect gates $\mathbb{S}_{suspect}$.
2. **Process:** 
   *   Translate $\mathbb{S}_{suspect}$ into a commercial ATPG script format (e.g., Cadence Modus TCL).
   *   Instruct the ATPG Oracle to target specific stuck-at anomalies (stuck-at-0, stuck-at-1) *only* at the structural pins of $\mathbb{S}_{suspect}$.
   *   Bundle netlist + TCL + fault list into a downloadable `.zip` Job Bundle.
3. **Output:** A minimized, highly optimized test pattern set designed exclusively to activate the suspected anomalous logic.

---

## IV. EXPERIMENTAL RESULTS & EVALUATION

*Note: This section will contain charts and tables once Modus server experiments are run. The web UI visualization (heatmaps + suspect highlighting) is already operational for offline analysis.*

### A. Experimental Setup
*   **Benchmarks:** ISCAS-85 circuits (e.g., c17, c432) and custom circuits injected with synthetic rare-trigger Trojans (e.g., 4-input AND triggers with XOR payloads). Built-in benchmarks: c17, mixed_logic, reconvergent, sequential_small, trojan_example.
*   **Environment:** Python-based graph analytics (Flask + NetworkX + Cytoscape.js), connected to enterprise LLM APIs (GPT-4o, Claude), with offline heuristic fallback, exporting to Cadence Modus ATPG.

### B. Evaluation Metrics
1.  **Detection Accuracy:** 
    *   True Positive Rate (TPR): Percentage of actual Trojan gates flagged.
    *   False Positive Rate (FPR): Clean logic incorrectly flagged.
2.  **Coverage Comparison (AI-Guided vs. Baseline):**
    *   Compare the Fault Coverage (%) achieved by the commercial ATPG when running *unconstrained* (Baseline) versus when targeting only the LLM-flagged nodes (AI-Guided).
3.  **Pattern Reduction & Efficiency:**
    *   Measure the percentage reduction in the total number of test patterns generated. 
    *   Record computational Speedup (Baseline CPU Time / AI-Guided CPU Time).

### C. Expected Observations
*   The LLM successfully filters out >95% of standard logic, eliminating the data imbalance problem.
*   The Oracle-Guided ATPG run yields near identical targeted coverage compared to exhaustive N-detect methods, but with drastically fewer patterns and lower computational overhead.

---

## V. CONCLUSION
*   Summarize the impact: Bridging the gap between modern generative AI reasoning and rigorous, mathematically sound EDA verification tools.
*   By utilizing LLMs to solve the heuristic search problem and commercial ATPG to solve the Boolean logic problem, the framework provides a scalable, training-free approach to Hardware Trojan detection at the gate-level.

---

## VI. REFERENCES (Key Citations to Include)

1. [LATENT] A. Jayasena and P. Mishra, "LATENT: Leveraging Automated Test Pattern Generation for Hardware Trojan Detection," 2023. *(Use to contrast your efficient LLM method against their computationally heavy fan-in cone tracing).*
2. [TrojanWhisper] Y. Zhang, S. Chattopadhyay, and S. Zhao, "TrojanWhisper: Evaluating Pre-Trained Large Language Models to Detect and Localize Hardware Trojans," arXiv:2401.07961, 2024. *(Use to show LLMs work, but emphasize your novelty of moving from RTL to Gate-Level).*
3. S. Saha, D. Sengupta, and H. Li, "Scalable Detection of Hardware Trojans Using ATPG-Based Activation of Rare Events," IEEE TCAD, 2023. *(Foundational citation for using Toggle Rates and rare events).*
4. [GHOST] "Unleashing GHOST: An LLM-Powered Framework for Automated Hardware Trojan Design," 2024. *(Use in introduction to emphasize the growing threat urgency).*
