import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  ShieldAlert, BarChart2, Zap, ListOrdered, Target,
  ArrowRight, ChevronDown, Sparkles, Wrench, HelpCircle as HelpIcon, CircuitBoard,
  GitBranch, BookOpen, Sigma,
} from 'lucide-react';

const NAV_SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'workflow', label: 'Workflow' },
  { id: 'concepts', label: 'Concepts' },
  { id: 'results', label: 'Understanding Results' },
  { id: 'benchmark-explained', label: 'The Benchmark Page' },
  { id: 'math', label: 'Mathematical Foundations' },
  { id: 'faq', label: 'FAQ' },
  { id: 'technical', label: 'Technical Details' },
  { id: 'links', label: 'Project & Links' },
];

export default function Help() {
  const [active, setActive] = useState('overview');
  const observed = useRef<Set<string>>(new Set());

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(entry.target.id);
        }
      },
      { rootMargin: '-15% 0px -70% 0px' }
    );
    NAV_SECTIONS.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el && !observed.current.has(s.id)) {
        observer.observe(el);
        observed.current.add(s.id);
      }
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div className="flex h-full overflow-hidden">
      <nav className="w-56 shrink-0 border-r border-border bg-card p-4 overflow-y-auto hidden lg:flex flex-col h-full">
        <span className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-3">Help Center</span>
        <ul className="space-y-0.5">
          {NAV_SECTIONS.map((s) => (
            <li key={s.id}>
              <a
                href={`#${s.id}`}
                className={`block text-sm rounded-md px-2 py-1.5 transition-colors ${
                  active === s.id
                    ? 'bg-secondary text-foreground font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-secondary/50'
                }`}
              >
                {s.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <div className="flex-1 overflow-y-auto scroll-smooth">
        <div className="max-w-3xl mx-auto p-6 md:p-10 flex flex-col gap-16">
          {/* ---------------------------------------------------------- Hero */}
          <section className="flex flex-col gap-4 pt-4">
            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-primary w-fit">
              <Sparkles className="w-3.5 h-3.5" /> How does this work?
            </span>
            <h1 className="text-3xl md:text-4xl font-bold tracking-tight leading-tight">
              Find hidden malicious logic in a chip design — before it ever gets built.
            </h1>
            <p className="text-muted-foreground text-base leading-relaxed max-w-2xl">
              PyATPG scans a circuit's blueprint, points out the gates that look suspicious, and then runs a
              real hardware test on them to check if that suspicion holds up. You don't need a chip design
              background to use it — this page explains everything from scratch.
            </p>
            <div className="flex flex-wrap gap-3 mt-2">
              <Link
                to="/circuits"
                className="inline-flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2.5 rounded-md text-sm font-medium transition-colors"
              >
                Start Analyzing <ArrowRight className="w-4 h-4" />
              </Link>
              <a
                href="#workflow"
                className="inline-flex items-center gap-2 bg-secondary hover:bg-secondary/80 text-secondary-foreground px-4 py-2.5 rounded-md text-sm font-medium transition-colors"
              >
                View Workflow
              </a>
            </div>
          </section>

          {/* ------------------------------------------------------ Overview */}
          <section id="overview" className="scroll-mt-6 flex flex-col gap-4">
            <SectionHeading title="What can it do?" />
            <div className="grid sm:grid-cols-2 gap-4">
              <OverviewCard
                icon={BarChart2}
                title="Analyze a circuit"
                to="/features"
                cta="Extract Features"
              >
                Reads your circuit and measures, gate by gate, how easy each one is to control and to see the
                effects of — the two properties malicious logic usually tries to hide behind.
              </OverviewCard>
              <OverviewCard
                icon={ListOrdered}
                title="Detect suspicious regions"
                to="/ranking"
                cta="Run Ranking"
              >
                Scores every gate for suspicion — either with a rules-based heuristic or an AI model — and
                gives you a ranked shortlist instead of thousands of gates to check by hand.
              </OverviewCard>
              <OverviewCard
                icon={Zap}
                title="Generate & run tests"
                to="/atpg"
                cta="Run ATPG"
              >
                Automatically writes test inputs targeting the flagged gates and runs them for real, using
                the right ATPG engine for the circuit — combinational or sequential — to confirm or clear
                each suspicion.
              </OverviewCard>
              <OverviewCard
                icon={Target}
                title="Inspect results"
                to="/results"
                cta="View Results"
              >
                Brings the suspicion score and the real test outcome together into one table, so you can see
                exactly which gates are confirmed suspects and why.
              </OverviewCard>
            </div>
          </section>

          {/* ------------------------------------------------------ Workflow */}
          <section id="workflow" className="scroll-mt-6 flex flex-col gap-4">
            <SectionHeading title="Recommended Workflow" subtitle="Follow these steps in order — each one links straight to the page you need." />
            <ol className="flex flex-col">
              <WorkflowStep
                n={1}
                title="Upload your circuit"
                to="/circuits"
                cta="Upload Circuit"
                gets="A parsed circuit with basic stats: gate count, inputs/outputs, and depth."
              >
                Start by providing your circuit as a Verilog file. Every other tool in the app reuses this same
                upload automatically — you only do this once.
              </WorkflowStep>
              <WorkflowStep
                n={2}
                title="Analyze it"
                to="/features"
                cta="Extract Features"
                gets="A full table of measurements for every gate in the circuit."
              >
                The system measures how hard each gate is to control and to observe, plus how often it actually
                switches on and off when the circuit runs. Quiet, hard-to-see gates are worth a closer look.
              </WorkflowStep>
              <WorkflowStep
                n={3}
                title="Find suspicious gates"
                to="/ranking"
                cta="Run Ranking"
                gets="A ranked list of gates, most suspicious first, with a reason for each."
              >
                Instead of reading through every gate's measurements yourself, a ranking model scores and sorts
                them for you.
              </WorkflowStep>
              <WorkflowStep
                n={4}
                title="Generate & verify tests"
                to="/atpg"
                cta="Run ATPG"
                gets="A pass/fail verdict per suspicious gate, backed by real generated test patterns."
              >
                This is the verification step. Flagged gates are tested for real — a gate whose test can't
                reliably catch a fault on it is strong evidence something's actually wrong, not just a coincidence.
              </WorkflowStep>
              <WorkflowStep
                n={5}
                title="Inspect results"
                to="/results"
                cta="View Results"
                gets="One table: every ranked gate next to its verified verdict."
              >
                See which flagged gates were confirmed, which turned out clean, and which still need more data.
              </WorkflowStep>
              <WorkflowStep
                n={6}
                title="Export a report"
                to="/reports"
                cta="Go to Reports"
                gets="A downloadable summary of everything you just did."
                last
              >
                Optional, but useful if you need to save or share what you found.
              </WorkflowStep>
            </ol>

            <div className="mt-2 border border-dashed border-border rounded-lg p-4 bg-secondary/20 flex flex-col gap-2">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Wrench className="w-4 h-4 text-primary" /> A separate path: testing the ranker itself
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">
                <Link to="/trojan" className="text-primary hover:underline font-medium">Trojan Dataset</Link> and{' '}
                <Link to="/benchmark" className="text-primary hover:underline font-medium">Benchmark Suite</Link> aren't
                part of analyzing your circuit — they answer a different question: "how accurate is this ranking
                model in general?" Trojan Dataset creates synthetic infected circuits with a known answer key, and
                Benchmark Suite scores a ranker against that answer key. Use this if you want to evaluate or compare
                rankers, not when you're investigating a specific circuit.
              </p>
            </div>
          </section>

          {/* ------------------------------------------------------ Concepts */}
          <section id="concepts" className="scroll-mt-6 flex flex-col gap-4">
            <SectionHeading title="Concepts" subtitle="The handful of ideas you need — nothing more." />

            <ConceptCard icon={CircuitBoard} term="What is a circuit?">
              A digital design made of simple logic gates (AND, OR, NOT, and similar) wired together. This app
              works with a text description of that wiring, called a <Term>Verilog</Term> file — think of it as
              the blueprint for a chip, before it's physically manufactured.
            </ConceptCard>

            <ConceptCard icon={ShieldAlert} term="What is a Hardware Trojan?">
              <p>
                A malicious modification hidden inside a hardware design — usually snuck in somewhere in the
                supply chain, not by the original designer. Unlike a software virus, it's physically part of the
                chip, so it can't be patched after manufacturing.
              </p>
              <details className="mt-2 group">
                <summary className="cursor-pointer text-sm text-primary hover:underline w-fit list-none flex items-center gap-1">
                  <ChevronDown className="w-3.5 h-3.5 transition-transform group-open:rotate-180" /> Learn more
                </summary>
                <div className="mt-2 text-sm text-muted-foreground leading-relaxed flex flex-col gap-2 pl-1">
                  <p>Most hardware Trojans have two parts:</p>
                  <ul className="list-disc pl-5 space-y-1">
                    <li><strong>Trigger</strong> — a rare condition that stays silent during normal use, which is exactly why standard testing usually misses it.</li>
                    <li><strong>Payload</strong> — the harmful effect once triggered: leaking secret data, degrading performance, or breaking the circuit outright.</li>
                  </ul>
                  <p>Because they're built to stay quiet, Trojan gates tend to rarely switch on/off and are hard to observe from the circuit's outputs — exactly what this tool measures.</p>
                </div>
              </details>
            </ConceptCard>

            <ConceptCard icon={Zap} term="What is ATPG?">
              Short for <Term>Automatic Test Pattern Generation</Term> — a way of automatically creating input
              values that check whether a specific part of a circuit is working correctly. <InSimpleTerms>
              It's like automatically writing a targeted test case for one specific gate, instead of testing the
              whole circuit blindly.</InSimpleTerms>
            </ConceptCard>

            <ConceptCard icon={BarChart2} term="What are the gate measurements about?">
              <p>Every gate gets scored on three things:</p>
              <ul className="list-disc pl-5 space-y-1 mt-1">
                <li><strong>How hard it is to control</strong> — can you easily force this gate's output to 0 or 1 from the circuit's inputs?</li>
                <li><strong>How hard it is to observe</strong> — if this gate misbehaves, would you actually notice at an output?</li>
                <li><strong>How often it switches</strong> — during normal operation, does this gate's value change a lot, or almost never?</li>
              </ul>
              <InSimpleTerms>A gate that's hard to control, hard to observe, <em>and</em> almost never switches is exactly the kind of quiet corner a Trojan would hide in.</InSimpleTerms>
            </ConceptCard>
          </section>

          {/* ------------------------------------------------------ Results */}
          <section id="results" className="scroll-mt-6 flex flex-col gap-4">
            <SectionHeading title="Understanding Results" subtitle="What you'll actually see, and what it means." />

            <div className="grid sm:grid-cols-3 gap-3">
              <VerdictExplainer color="red" label="Trojan Candidate">
                The gate was flagged as suspicious, and the generated test genuinely couldn't detect a fault on
                it. This is your strongest signal — investigate these first.
              </VerdictExplainer>
              <VerdictExplainer color="green" label="Cleared">
                The gate was flagged, but the test fully confirmed it behaves like ordinary logic. Likely a false
                alarm from the ranking step.
              </VerdictExplainer>
              <VerdictExplainer color="yellow" label="No Data">
                The verification step didn't produce a usable result for this gate — not a verdict either way.
              </VerdictExplainer>
            </div>

            <div className="border border-border rounded-lg divide-y divide-border overflow-hidden">
              <MetricRow name="Suspicion score" meaning="How confident the ranker is that this gate is suspicious." direction="Higher = more suspicious" why="It's how the shortlist is ordered — start from the top." />
              <MetricRow name="Toggle rate" meaning="How often the gate's output actually changes during simulation." direction="Lower = more suspicious" why="Trojans are built to stay quiet, so real logic almost always toggles more." />
              <MetricRow name="CC0 / CC1" meaning="How hard it is to force this gate to 0 or to 1." direction="Higher = harder to control" why="A gate that's hard to control is hard to test — and hard to trigger by accident." />
              <MetricRow name="CO" meaning="How hard it is to see this gate's effect at an output." direction="Higher = harder to observe" why="Payload gates are specifically built to be hard to observe." />
              <MetricRow name="Fault coverage" meaning="The percentage of test targets the generated patterns successfully checked." direction="Lower on a targeted run = more concerning" why="A gate that resists a targeted test is exactly what 'Trojan Candidate' means." />
            </div>
          </section>

          {/* ---------------------------------------------- Benchmark page */}
          <section id="benchmark-explained" className="scroll-mt-6 flex flex-col gap-4">
            <SectionHeading
              title="Understanding the Benchmark Page"
              subtitle="It answers a different question than every other page in the app — this explains what, and why it's here at all."
            />

            <div className="border border-border bg-secondary/20 rounded-lg p-5 flex flex-col gap-3">
              <p className="text-sm text-muted-foreground leading-relaxed">
                Every other page answers <em className="text-foreground">"is this specific circuit I loaded suspicious?"</em>{' '}
                The <Link to="/benchmark" className="text-primary hover:underline font-medium">Benchmark</Link> page
                answers a completely different question: <em className="text-foreground">"if I hand a gate ranker a pile
                of circuits where I already know exactly where the Trojans are, how often does it actually find them?"</em>{' '}
                It exists because a ranker's suspicion scores are only useful if they're calibrated against ground
                truth — otherwise "flagged as suspicious" is just an opinion. This is also literally the evaluation
                methodology the accompanying research paper uses to report accuracy numbers for each ranker.
              </p>
            </div>

            <div className="flex flex-col gap-3">
              <h3 className="font-semibold text-sm">Step by step</h3>
              <ol className="flex flex-col gap-3">
                <li className="flex gap-3 text-sm">
                  <span className="w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center text-xs font-bold shrink-0">1</span>
                  <span className="text-muted-foreground">
                    <strong className="text-foreground">Build an answer key first, on the{' '}
                    <Link to="/trojan" className="text-primary hover:underline">Trojan Dataset</Link> page.</strong>{' '}
                    Upload a clean circuit and generate, say, 20 variants. Each variant is that circuit with one
                    synthetic Trojan inserted, plus a <code className="font-mono text-xs bg-secondary px-1 rounded">metadata.json</code>{' '}
                    recording <em>exactly</em> which gates are the trigger and payload — the ground truth. This is
                    saved on the server under <code className="font-mono text-xs bg-secondary px-1 rounded">datasets/&lt;circuit&gt;/</code>.
                  </span>
                </li>
                <li className="flex gap-3 text-sm">
                  <span className="w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center text-xs font-bold shrink-0">2</span>
                  <span className="text-muted-foreground">
                    <strong className="text-foreground">Point Benchmark at that directory.</strong> The "Dataset
                    Directory" field is a server-side path — <code className="font-mono text-xs bg-secondary px-1 rounded">datasets</code>{' '}
                    to sweep everything you've generated, or a narrower path for one circuit's variants only.
                  </span>
                </li>
                <li className="flex gap-3 text-sm">
                  <span className="w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center text-xs font-bold shrink-0">3</span>
                  <span className="text-muted-foreground">
                    <strong className="text-foreground">Pick a ranker and a Top-K</strong>, then start. The
                    ranker (RandomRanker as a floor baseline, ScoapRanker, RareNodeRanker, or the LLM ranker) is run
                    against <em>every</em> variant in the directory, blind — it never sees the metadata, only the
                    infected netlist. Top-K is how far down the ranked list counts as "found" (K=10 means: did the
                    real trigger/payload gate land somewhere in the ranker's top 10 most-suspicious guesses?).
                  </span>
                </li>
                <li className="flex gap-3 text-sm">
                  <span className="w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center text-xs font-bold shrink-0">4</span>
                  <span className="text-muted-foreground">
                    <strong className="text-foreground">Read the results table.</strong> One row per circuit, columns
                    Top-1/5/10 accuracy, Recall@K, Precision@K, MRR, and SSR — each is defined exactly in{' '}
                    <a href="#math" className="text-primary hover:underline">Mathematical Foundations</a> below.
                  </span>
                </li>
              </ol>
            </div>

            <div className="border border-dashed border-border rounded-lg p-4 bg-secondary/10">
              <p className="text-xs text-muted-foreground leading-relaxed">
                <strong className="text-foreground">Worked example:</strong> you generate 20 low-SCOAP-strategy
                variants of c880, run ScoapRanker at K=10 against them, and the table shows Top-10 accuracy = 0.85.
                That means in 17 of the 20 infected circuits, the real Trojan gate was somewhere in ScoapRanker's 10
                most-suspicious picks — a concrete, checkable number, not a guess about whether the ranker "seems
                good."
              </p>
            </div>
          </section>

          {/* ------------------------------------------------------ Math */}
          <section id="math" className="scroll-mt-6 flex flex-col gap-6">
            <SectionHeading
              title="Mathematical Foundations"
              subtitle="Every formula the pipeline actually runs, verified against the source. This is the deep-dive — skip it if the plain-language sections above already answered your question."
            />

            <MathSubsection title="1. SCOAP testability metrics" anchor="scoap">
              <p>
                For every node <Var>v</Var>, three costs are computed in a single forward topological pass
                (controllability) followed by one backward pass (observability). <Var>CC0(v)</Var>/<Var>CC1(v)</Var> are
                the minimum number of signal assignments needed to force <Var>v</Var> to 0 or 1 from the primary
                inputs; <Var>CO(v)</Var> is the minimum cost to propagate a fault at <Var>v</Var> to a primary output.
                Primary inputs, flip-flop outputs, and any node with no predecessors start at{' '}
                <Formula inline>CC0 = CC1 = 1</Formula>.
              </p>
              <Formula>
{`For an AND gate with predecessors p₁ … pₖ:
  CC1(AND) = Σᵢ CC1(pᵢ) + 1        CC0(AND) = minᵢ CC0(pᵢ) + 1

OR is the dual (sum on CC0, min on CC1); NAND/NOR swap CC0↔CC1 relative
to AND/OR; NOT and BUF pass the opposite/same cost through plus 1:
  CC0(NOT) = CC1(p) + 1            CC1(NOT) = CC0(p) + 1
  CC0(BUF) = CC0(p) + 1            CC1(BUF) = CC1(p) + 1

For a 2-input XOR with predecessors a, b:
  same  = min(CC0(a)+CC0(b), CC1(a)+CC1(b)) + 1   → CC0(XOR)
  differ = min(CC0(a)+CC1(b), CC1(a)+CC0(b)) + 1  → CC1(XOR)
(XNOR swaps the two results — its output is XOR inverted.)`}
              </Formula>
              <p>
                Observability runs backward from the outputs, where <Var>CO = 0</Var>. For a predecessor{' '}
                <Var>p</Var> of an AND/NAND gate with the other inputs <Var>others</Var>:
              </p>
              <Formula>
{`CO(p) = min( CO(p),  CO(gate) + Σ_{q ∈ others} CC1(q) + 1 )   [AND, NAND]
CO(p) = min( CO(p),  CO(gate) + Σ_{q ∈ others} CC0(q) + 1 )   [OR, NOR]
CO(p) = CO(gate) + 1                                          [NOT, BUF]
CO(p) = min( CO(p),  CO(gate) + min_{q∈others} min(CC0(q),CC1(q)) + 1 )  [XOR/XNOR]`}
              </Formula>
              <p>
                Interpretation: a gate with <Var>CC0(v)+CC1(v) ≫ 50</Var> is expensive to drive to either value from
                the inputs, and one with <Var>CO(v) ≫ 50</Var> is expensive to see at an output — exactly the two
                properties a Trojan trigger and payload are built to have.
              </p>
            </MathSubsection>

            <MathSubsection title="2. Dynamic toggle-rate estimation" anchor="toggle">
              <p>
                <Var>N</Var> random input vectors are simulated in sequence; every 0→1 and 1→0 transition at every
                gate is counted. With <Var>N</Var> patterns there are <Var>N−1</Var> consecutive pairs, and each pair
                can toggle in either direction, so:
              </p>
              <Formula>
{`ToggleRate(v) = ( transitions₀₁(v) + transitions₁₀(v) ) / ( 2·(N − 1) )`}
              </Formula>
              <p>
                A gate with <Var>ToggleRate(v) &lt; 0.01</Var> almost never changes value under random stimulus —
                "dynamically stealthy," the dynamic-activity half of the suspicion signal (SCOAP is the static half).
              </p>
            </MathSubsection>

            <MathSubsection title="3. Graph topology: centrality" anchor="centrality">
              <p>
                Betweenness centrality of node <Var>v</Var> is the fraction of shortest paths between other node
                pairs that pass through it:
              </p>
              <Formula>
{`C_B(v) = Σ_{s≠v≠t}  σ_st(v) / σ_st

σ_st = number of shortest paths from s to t
σ_st(v) = number of those paths passing through v`}
              </Formula>
              <p>
                Computed with <Var>k = min(|V|, 100)</Var> sampled source nodes rather than all pairs (the exact
                computation is <Var>O(|V|·|E|)</Var>, too slow to repeat per analysis on large circuits) — so on
                circuits over 100 nodes this is an unseeded statistical estimate, not a deterministic value; see{' '}
                <a href="#technical" className="text-primary hover:underline">Technical Details</a> for the measured
                consequence of that.
              </p>
            </MathSubsection>

            <MathSubsection title="4. Offline heuristic ranking (no LLM key required)" anchor="rankers">
              <p>
                Two deterministic scorers combine the features above into a single suspicion number per gate,
                sorted descending. <strong className="text-foreground">ScoapRanker</strong>:
              </p>
              <Formula>
{`suspicion(v) = 0.4·( CC0(v) + CC1(v) ) + 0.6·CO(v)`}
              </Formula>
              <p><strong className="text-foreground">RareNodeRanker</strong> combines three normalized signals:</p>
              <Formula>
{`toggle_rarity(v)     = 1 − min(ToggleRate(v), 1)
structural_rarity(v) = fan_in(v) / ( fan_out(v) + 1 )
depth_factor(v)      = pi_distance(v) / max( pi_distance(v) + po_distance(v), 1 )

suspicion(v) = 0.50·toggle_rarity(v) + 0.25·structural_rarity(v) + 0.25·depth_factor(v)`}
              </Formula>
            </MathSubsection>

            <MathSubsection title="5. Structural fault-list targeting (the LLM's deterministic fallback)" anchor="targeting">
              <p>
                When no API key is configured, the exact same targeting step that would otherwise call an LLM
                instead applies a fixed structural filter. The list size is bounded relative to circuit size:
              </p>
              <Formula>
{`max_gates = clamp( round(0.3 · |gates|),  20,  200 )

A gate is "structurally interesting" iff ALL three hold:
  ToggleRate(v) < 0.1   AND   fan_in(v) ≥ 2   AND   CO(v) > 5

Interesting gates are sorted by ( ToggleRate(v) ascending, CO(v) descending )
and the first max_gates are kept. If fewer than min(20, max_gates, |gates|)
qualify, the list is topped up from the same ranking so a targeted run is
never left testing only one or two faults.`}
              </Formula>
              <p>
                This is exactly the heuristic that produced the reported gate counts in the paper's evaluation
                whenever an LLM key wasn't available for that run — not a separate, lesser fallback.
              </p>
            </MathSubsection>

            <MathSubsection title="6. Trojan insertion strategy math" anchor="trojan-math">
              <p>
                Every generated Trojan is a <Var>k</Var>-input trigger gate (default AND, <Var>k=2</Var>) whose
                output feeds a payload gate (default XOR) spliced into an existing net. Boolean form, with trigger
                inputs <Var>x₁ … xₖ</Var> and target net <Var>y</Var>:
              </p>
              <Formula>
{`trigger  = x₁ ∧ x₂ ∧ … ∧ xₖ                    (generalizes to the chosen gate type)
y′       = y ⊕ trigger                          (XOR payload: flips y only when trigger = 1)`}
              </Formula>
              <p>
                Where the strategy picks <Var>x₁…xₖ</Var> and <Var>y</Var> from is itself a formula. Nets are ranked
                by a rarity criterion, then <em>sampled</em> — not the single rarest — from a pool sized:
              </p>
              <Formula>
{`pool_size = max( 8,  k,  round(0.05 · |nets|) )   ("rarest 5%, at least 8")

rare_node strategy:  rank nets by ToggleRate(v) ascending  → sample k for the trigger
low_scoap strategy:  rank nets by CC0(v)+CC1(v) descending → sample k for the trigger
                      (payload target ranked by CO(v) descending instead)`}
              </Formula>
              <p>
                Sampling instead of taking the strict argmax means regenerating the same circuit produces a{' '}
                <em>dataset</em> of differently-placed Trojans, not the same one repeated — and every payload target
                is checked against the trigger's ancestor set first, since splicing onto an ancestor of the trigger
                would close a combinational feedback loop that Atalanta cannot levelize.
              </p>
            </MathSubsection>

            <MathSubsection title="7. Cross-reference verdict logic" anchor="verdict">
              <p>
                Let <Var>F</Var> be the AI/heuristic-flagged gate set and <Var>U</Var> the set of gates whose faults
                the ATPG run could not detect (undetected, redundant, or aborted). The verdict per gate{' '}
                <Var>g</Var> is:
              </p>
              <Formula>
{`g ∈ F  ∧  g ∈ U   →  TROJAN_CANDIDATE   (flagged, and the oracle couldn't clear it)
g ∈ F  ∧  g ∉ U   →  CLEARED            (flagged, but the oracle detected it fine)
g ∉ F  ∧  g ∈ U   →  NO_DATA            (never flagged — no suspicion to confirm or deny)
g ∉ F  ∧  g ∉ U   →  (not reported — nothing suspicious, nothing undetected)`}
              </Formula>
              <p>
                Only the first row is ever surfaced as a positive verdict: agreement between two independent
                sources of evidence (structural/semantic suspicion, and a real generated test pattern's failure to
                detect it) is what the framework treats as signal.
              </p>
            </MathSubsection>
          </section>

          {/* ------------------------------------------------------ FAQ */}
          <section id="faq" className="scroll-mt-6 flex flex-col gap-4">
            <SectionHeading title="FAQ & Troubleshooting" />
            <div className="flex flex-col gap-2">
              <FaqItem q="What file format does it accept?">
                A gate-level Verilog netlist (<code className="font-mono text-xs bg-secondary px-1 rounded">.v</code>).
                It should already be synthesized into basic gates (AND/OR/NOT/etc.), not behavioral/RTL code.
              </FaqItem>
              <FaqItem q="Do I need to re-upload my circuit for every tool?">
                No. Upload it once on the <Link to="/circuits" className="text-primary hover:underline">Circuits</Link> page
                — Feature Analysis, ATPG, and Trojan Dataset all reuse that same upload automatically.
              </FaqItem>
              <FaqItem q="Does ATPG support sequential circuits (with flip-flops)?">
                Yes. Combinational circuits run through the standard ATPG tool as before. A circuit with
                flip-flops is routed automatically to a native sequential ATPG engine — time-frame expansion,
                PODEM search across clock cycles, and a deterministic multi-cycle fault simulator — with no
                extra steps on your end. It's only rejected if it has an asynchronous feedback loop that isn't
                broken by a clocked flip-flop, which neither engine can levelize.
              </FaqItem>
              <FaqItem q="Ranking says 'Please extract features first!' — why?">
                Ranking scores gates using the measurements from the Feature Analysis step, so it needs that step
                to have run at least once first.
              </FaqItem>
              <FaqItem q="No gates were flagged at all — is that normal?">
                Yes, for a clean or very small circuit. It means nothing crossed the suspicion threshold — that's
                a good sign, not an error.
              </FaqItem>
              <FaqItem q="What's the difference between Ranking and ATPG?">
                Ranking only scores gates by suspicion — it doesn't verify anything. ATPG does its own internal
                ranking <em>and</em> then runs a real test to confirm or clear each suspicion, which is why the{' '}
                <Link to="/results" className="text-primary hover:underline">Results</Link> page combines both.
              </FaqItem>
            </div>
          </section>

          {/* ------------------------------------------------------ Technical */}
          <section id="technical" className="scroll-mt-6 flex flex-col gap-4">
            <SectionHeading title="Technical Details" subtitle="For readers who already know the terminology." />
            <details className="group border border-border rounded-lg bg-card">
              <summary className="cursor-pointer list-none flex items-center justify-between px-5 py-4 select-none">
                <span className="flex items-center gap-2 text-sm font-semibold">
                  <HelpIcon className="w-4 h-4 text-primary" /> Show implementation details
                </span>
                <ChevronDown className="w-4 h-4 text-muted-foreground transition-transform group-open:rotate-180" />
              </summary>
              <div className="px-5 pb-5 flex flex-col gap-6 text-sm text-muted-foreground leading-relaxed border-t border-border pt-5">
                <div>
                  <h4 className="font-semibold text-foreground mb-1">SCOAP testability analysis</h4>
                  <p>
                    Sandia Controllability/Observability Analysis Program metrics. CC0/CC1 estimate the difficulty
                    of driving a signal to 0 or 1 from the primary inputs via a forward topological pass; CO
                    estimates the difficulty of propagating that signal's effect to a primary output via a
                    backward pass. Computed per-gate in <code className="font-mono text-xs">graph_analysis.py</code>.
                  </p>
                </div>
                <div>
                  <h4 className="font-semibold text-foreground mb-1">Toggle-rate simulation</h4>
                  <p>Thousands of random input vectors are simulated; switching activity (0→1 and 1→0 transitions) is tallied per gate and normalized against the number of valid simulated transitions.</p>
                </div>
                <div>
                  <h4 className="font-semibold text-foreground mb-1">Rankers</h4>
                  <ul className="list-disc pl-5 space-y-1">
                    <li><strong>RandomRanker</strong> — shuffles gates; a floor baseline other rankers must beat.</li>
                    <li><strong>ScoapRanker</strong> — scores by (CC0+CC1)×0.4 + CO×0.6.</li>
                    <li><strong>RareNodeRanker</strong> — scores by inverse toggle rate combined with fan-in/out and logic depth.</li>
                    <li><strong>LLMRanker</strong> — sends each gate's full structural profile to the configured LLM (Gemini, OpenAI, Anthropic, NVIDIA NIM, or any OpenAI-compatible endpoint — pick one in Settings) for few-shot reasoning, returning a suspicion score and justification per gate.</li>
                  </ul>
                </div>
                <div>
                  <h4 className="font-semibold text-foreground mb-1">ATPG verification pipeline</h4>
                  <p>
                    Runs the ATPG tool twice: a baseline pass (full fault list, no restrictions) and a
                    targeted pass restricted to faults on AI/heuristic-flagged gates. The targeted run's
                    undetected-fault report is cross-referenced against the flagged gate list — a flagged gate
                    absent from the undetected-fault report had all its faults detected (Cleared); a gate present
                    in it resisted detection (Trojan Candidate). Circuits with flip-flops skip that tool entirely
                    and use a native sequential engine instead — time-frame expansion, PODEM search across clock
                    cycles, and a deterministic multi-cycle fault simulator — reporting the same kind of
                    detected/undetected-fault outcome over multi-cycle test sequences rather than single vectors.
                  </p>
                </div>
                <div>
                  <h4 className="font-semibold text-foreground mb-1">Trojan Dataset generation</h4>
                  <p>
                    Inserts a configurable trigger gate (default AND) and payload gate (default XOR) into a cloned
                    copy of the circuit, targeting nets chosen either randomly or by rare toggle activity. Each
                    variant is exported with a <code className="font-mono text-xs">metadata.json</code> recording
                    the exact trigger/payload gates — the ground truth Benchmark scores against.
                  </p>
                </div>
                <div>
                  <h4 className="font-semibold text-foreground mb-1">Benchmark metrics</h4>
                  <p>Top-K accuracy, precision/recall at K, mean reciprocal rank, and search-space reduction, aggregated across every circuit/variant in a dataset directory for a given ranker.</p>
                </div>
              </div>
            </details>
          </section>

          {/* ------------------------------------------------------ Links */}
          <section id="links" className="scroll-mt-6 flex flex-col gap-4">
            <SectionHeading title="Project & Links" subtitle="Source code, the paper, and how the pieces fit together." />
            <div className="grid sm:grid-cols-2 gap-4">
              <a
                href="https://github.com/HiteshPranav267/py-atpg-comb"
                target="_blank"
                rel="noopener noreferrer"
                className="group border border-border bg-card rounded-lg p-5 flex flex-col gap-2 hover:border-primary/50 transition-colors"
              >
                <div className="w-9 h-9 rounded-md bg-secondary flex items-center justify-center text-primary">
                  <GitBranch className="w-4 h-4" />
                </div>
                <h3 className="font-semibold">Source code (GitHub)</h3>
                <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                  Full implementation — the ATPG service, both rankers, the sequential engine, the Trojan
                  insertion strategies, and this frontend. Every formula on this page traces to a file here.
                </p>
                <span className="text-sm font-medium text-primary flex items-center gap-1 mt-1">
                  github.com/HiteshPranav267/py-atpg-comb
                  <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform" />
                </span>
              </a>
              <div className="border border-border bg-card rounded-lg p-5 flex flex-col gap-2">
                <div className="w-9 h-9 rounded-md bg-secondary flex items-center justify-center text-primary">
                  <BookOpen className="w-4 h-4" />
                </div>
                <h3 className="font-semibold">The research paper</h3>
                <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                  "Few-Shot LLM-Guided ATPG Prioritization for Scalable Hardware Trojan Detection via Agentic
                  EDA Workflows" — the multi-circuit evaluation this tool's targeted-mode design is based on,
                  including the honest accounting of what's proven (pattern-count reduction at equal detection
                  accuracy) versus what remains future work.
                </p>
              </div>
            </div>
            <div className="border border-dashed border-border rounded-lg p-4 bg-secondary/10">
              <p className="text-xs text-muted-foreground leading-relaxed">
                <strong className="text-foreground">Tech stack:</strong> Python 3 backend (FastAPI, NetworkX)
                driving ATALANTA for combinational ATPG and a native time-frame-expansion/PODEM engine for
                sequential circuits, with Gemini/OpenAI/Anthropic/NVIDIA NIM/any-OpenAI-compatible-endpoint as
                pluggable LLM providers; this frontend is React + TypeScript + Vite + Tailwind.
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------- */

function SectionHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <h2 className="text-xl font-bold tracking-tight">{title}</h2>
      {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
    </div>
  );
}

function OverviewCard({ icon: Icon, title, to, cta, children }: { icon: any; title: string; to: string; cta: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="group border border-border bg-card rounded-lg p-5 flex flex-col gap-2 hover:border-primary/50 transition-colors"
    >
      <div className="w-9 h-9 rounded-md bg-secondary flex items-center justify-center text-primary">
        <Icon className="w-4 h-4" />
      </div>
      <h3 className="font-semibold">{title}</h3>
      <p className="text-sm text-muted-foreground leading-relaxed flex-1">{children}</p>
      <span className="text-sm font-medium text-primary flex items-center gap-1 mt-1">
        {cta} <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform" />
      </span>
    </Link>
  );
}

function WorkflowStep({
  n, title, to, cta, gets, children, last,
}: { n: number; title: string; to: string; cta: string; gets: string; children: ReactNode; last?: boolean }) {
  return (
    <li className="flex gap-4 relative">
      <div className="flex flex-col items-center">
        <span className="w-8 h-8 rounded-full bg-primary text-primary-foreground flex items-center justify-center text-sm font-bold shrink-0">
          {n}
        </span>
        {!last && <span className="w-px flex-1 bg-border mt-1" />}
      </div>
      <div className={`flex flex-col gap-2 ${last ? 'pb-0' : 'pb-8'} flex-1`}>
        <h3 className="font-semibold text-base leading-none pt-1">{title}</h3>
        <p className="text-sm text-muted-foreground leading-relaxed">{children}</p>
        <p className="text-xs text-muted-foreground">
          <strong className="text-foreground">What you get:</strong> {gets}
        </p>
        <Link
          to={to}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline w-fit mt-0.5"
        >
          {cta} <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      </div>
    </li>
  );
}

function ConceptCard({ icon: Icon, term, children }: { icon: any; term: string; children: ReactNode }) {
  return (
    <div className="border border-border bg-card rounded-lg p-5 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Icon className="w-4 h-4 text-primary" />
        <h3 className="font-semibold text-sm">{term}</h3>
      </div>
      <div className="text-sm text-muted-foreground leading-relaxed">{children}</div>
    </div>
  );
}

function Term({ children }: { children: ReactNode }) {
  return <span className="font-medium text-foreground">{children}</span>;
}

function InSimpleTerms({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2 flex items-start gap-2 text-sm bg-secondary/40 border border-border rounded-md px-3 py-2">
      <Sparkles className="w-3.5 h-3.5 text-primary shrink-0 mt-0.5" />
      <span className="text-muted-foreground"><strong className="text-foreground">In simple terms:</strong> {children}</span>
    </div>
  );
}

function VerdictExplainer({ color, label, children }: { color: 'red' | 'green' | 'yellow'; label: string; children: ReactNode }) {
  const styles = {
    red: 'text-red-400 bg-red-500/10 border-red-500/30',
    green: 'text-green-500 bg-green-500/10 border-green-500/30',
    yellow: 'text-yellow-500 bg-yellow-500/10 border-yellow-500/30',
  };
  return (
    <div className={`border rounded-lg p-4 flex flex-col gap-1.5 ${styles[color]}`}>
      <span className="text-sm font-bold">{label}</span>
      <span className="text-xs text-muted-foreground leading-relaxed">{children}</span>
    </div>
  );
}

function MetricRow({ name, meaning, direction, why }: { name: string; meaning: string; direction: string; why: string }) {
  return (
    <div className="p-4 flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-4">
      <span className="font-mono text-sm font-semibold text-foreground w-40 shrink-0">{name}</span>
      <div className="flex-1 flex flex-col gap-1">
        <p className="text-sm text-muted-foreground">{meaning}</p>
        <p className="text-xs"><span className="text-primary font-medium">{direction}.</span> <span className="text-muted-foreground">{why}</span></p>
      </div>
    </div>
  );
}

function FaqItem({ q, children }: { q: string; children: ReactNode }) {
  return (
    <details className="group border border-border rounded-lg bg-card">
      <summary className="cursor-pointer list-none flex items-center justify-between px-4 py-3 select-none text-sm font-medium">
        {q}
        <ChevronDown className="w-4 h-4 text-muted-foreground transition-transform group-open:rotate-180 shrink-0" />
      </summary>
      <div className="px-4 pb-4 text-sm text-muted-foreground leading-relaxed">{children}</div>
    </details>
  );
}

/**
 * One numbered subsection of the Mathematical Foundations section: a
 * heading, then prose/Formula blocks in document order (an ordered list of
 * children, not a grid — these read top to bottom like a paper section).
 */
function MathSubsection({ title, anchor, children }: { title: string; anchor: string; children: ReactNode }) {
  return (
    <div id={anchor} className="border border-border bg-card rounded-lg p-5 flex flex-col gap-3 scroll-mt-20">
      <h3 className="font-semibold text-sm flex items-center gap-2">
        <Sigma className="w-4 h-4 text-primary shrink-0" /> {title}
      </h3>
      <div className="text-sm text-muted-foreground leading-relaxed flex flex-col gap-3">{children}</div>
    </div>
  );
}

/**
 * A block of formula text, verbatim monospace. `inline` renders it as a
 * short inline span (for a one-liner mid-sentence) instead of its own block.
 */
function Formula({ children, inline }: { children: ReactNode; inline?: boolean }) {
  if (inline) {
    return (
      <code className="font-mono text-xs bg-secondary text-foreground px-1.5 py-0.5 rounded">
        {children}
      </code>
    );
  }
  return (
    <pre className="font-mono text-xs md:text-[13px] leading-relaxed text-foreground bg-secondary/50 border border-border rounded-md px-4 py-3 overflow-x-auto whitespace-pre">
      {children}
    </pre>
  );
}

/** A math variable/symbol, set apart from surrounding prose. */
function Var({ children }: { children: ReactNode }) {
  return <span className="font-mono text-foreground">{children}</span>;
}
