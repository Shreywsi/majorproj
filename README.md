# TrustRoute: Trust-Aware Multi-Agent Routing Testbed

**Major project:** Trust-Aware Routing in Multi-Agent System using Manipulation Detection and
Optimized Agent Selection  
**Submitted by:** Shreyasi Soumya · **Roll No:** B230029CS  
**Institution:** National Institute of Technology Sikkim  
**Supervisor:** Dr. Pankaj Kumar Keserwani

This repository contains a controlled, reproducible simulation of the provenance paradox: agents
can inflate self-reported quality, so trusting claims can produce worse routing than random
selection. The simulator compares R0–R8, including signed evaluator evidence, task-aware reputation,
confidence bounds, identity/collusion checks and behavioural monitoring. The related base paper is
Prakash, *The Provenance Paradox in Multi-Agent LLM Routing* (arXiv:2603.18043).

The base paper's "attested" condition simply routes by hidden true quality, which is an oracle. The
simulation keeps that condition separate from deployable routing: only the evaluator/oracle receives
ground-truth quality; R0–R8 receive agent profiles and outcomes observed after task execution.
Evaluator attestations protect evidence integrity and signer identity; they do not prove objective
correctness beyond the simulator's trusted-evaluator assumption.

## Current inspection and implementation status

- The root Python modules (`agents.py`, `router.py`, `simulate.py`, `crypto_ledger.py`, and
  `config.py`) already implement the simulation, seven controlled attack models plus an optional
  drifter, and router versions R0–R8.
- `metrics.py`, `run_experiments.py`, and `analyze.py` provide evaluation and batch analysis.
- `ldp_layer/` contains the separate contract/provenance prototype.
- `ForensiQ/` is the earlier Streamlit digital-forensics application. Its UI and evidence backend
  are separate from the new root-level `app.py` ProvenanceGuard research dashboard.
- Phase 1–2 hardening isolates the simulator's hidden ground truth, separates agent and router
  random-number streams, introduces a trusted evaluator/public verifier boundary for R2, validates
  run configuration, and tests the R0/R1/R2 foundation.
- The Stage 4 TAMAS provenance detector is separate from R0–R8. It checks structured claims against
  runtime traces and explicitly trusted, signed evidence; current TAMAS traces contain no claims,
  so this is not yet a benchmark evaluation.
- Stage 9 adds a separate opt-in trust-aware router and a paired controlled comparison with the
  unchanged R0–R8 algorithms. The legacy quality/reward simulator and the claim-bearing local
  simulation remain distinct experimental interfaces.

## 1. Setup (VS Code, Windows / Linux / Mac)

```bash
cd router_testbed
python -m venv .venv
.venv\Scripts\activate            # Windows      (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
python -m pytest -q               # all tests must pass before you run experiments
```
In VS Code: *File > Open Folder* -> `router_testbed`, then *Python: Select Interpreter* -> `.venv`.
Python 3.11+ is required.

## 2. Files

| File | Role | LaTeX section |
|---|---|---|
| `config.py` | all hyper-parameters, router/attack names | suggested defaults |
| `agents.py` | honest agents + 7 attacks (+ `drifter` extra) | Alg. 2 |
| `crypto_ledger.py` | Trusted evaluator attestations and public-key replay / stale / tamper checks | signed record, R2 |
| `provenance_detector.py` | Stage 4 claim-level evidence, identity, freshness, replay, and trace checks | detector extension |
| `router.py` | R0..R8 + oracle: scoring, selection, update, CUSUM, clustering | Alg. 3-5 |
| `simulate.py` | main loop, optional LDP contract reroute | Alg. 1 |
| `metrics.py` | ASR, dQ, FAR, detection latency, recovery time, overhead | Metrics |
| `run_experiments.py` | attack x router x rho x seed grid -> `results/raw.csv` | Alg. 6 |
| `analyze.py` | bootstrap CIs, heatmaps, "smallest defence" table | Steps 7-8 |
| `ldp_layer/contracts.py` | contracts, claim types, typed errors, lineage | base paper Sec. 3 |
| `real_validation.py` | optional: real Claude models (paper Sec. 5.4) | Mode B |
| `llm_injection_test.py` | optional: attack 6 against a real LLM router | attack 6 |
| `tests/` | R0/R1/R2 boundary, reproducibility, crypto, contracts, per-defence checks | Step 1-2 |

## 3. Running

```bash
python -m pytest -q                                   # Step 1-2: testbed + baseline
python run_experiments.py --quick --out results/quick.csv   # ~25 s smoke test
python analyze.py --raw results/quick.csv --outdir results/quick

python run_experiments.py                             # T=5000, 20 seeds (hours on a laptop)
python run_experiments.py --T 5000 --seeds 30         # final protocol values
python analyze.py                                     # tables + figures in results/
```
Useful flags: `--attacks liar sleeper`, `--versions 1 5 7`, `--rhos 0.3`, `--workers 4`,
`--no-crypto` (3x faster, but then overhead numbers are not meaningful),
`--contracts` (enable LDP contract validation + reroute -> `viol_rate`, `recovery_rate`),
`--sleeper-rate 0.02` (sweep the trigger rate).
Develop with `T=500` and 2-3 seeds; scale up only for final runs.

Outputs in `results/`: `raw.csv`, `raw_config.json` (full config + git hash), `summary.csv`
(means + 95% bootstrap CIs), `asr_heatmap.png`, `quality_drop_heatmap.png`, `quality_vs_rho.png`,
`smallest_defence.csv`.

## 4. How to read the router ladder

`R0` random, `R1` trust self-claims, `R2` signed ledger, `R3` + decay, `R4` + per-task-type record,
`R5` + lower credible bound, `R6` + identity/collusion detection, `R7` + CUSUM quarantine and probes,
`R8` + ignore free text. `Oracle` (100) is the paper's "attested" condition and serves as upper bound.
Mapping to the paper's claim types: R1 uses `self_claimed`; R2-R8 use `runtime_observed`.

## 5. Modelling decisions you should state in the report

These are choices the LaTeX leaves open. Say so explicitly, and test sensitivity where it matters.

* **Random streams.** Agent construction, task assignment, router choices, probe recognition, and
  each agent's output noise use seed-derived streams. This prevents router random draws from
  consuming an agent's outcome-noise stream. Agent populations and task sequences are paired by seed.
* **Task types.** The last type is rare (`sleeper_rate`=4%) and is the sleeper's trigger.
* **Probe recognition.** A two-faced agent recognises a probe with probability `probe_detect`=0.3.
* **Sybil identities** are opaque integers to the router. A new identity reuses the old claim key
  with probability `sybil_key_reuse`=0.7; this is what R6 links on. Without key reuse only probation
  and the lower bound protect the router.
* **Collusion.** Routers R2-R5 treat peer endorsements as a small social-proof bonus
  (`endorse_w`); ring members share an output drift. R6 removes the bonus and caps the cluster at its
  pooled score (links: shared key, mutual endorsement, correlated block residuals).
* **CUSUM** is run against the agent-level mean (`cusum_pooled=True`); the LaTeX algorithm uses
  `mu_{a,k}`. Per-type means react too slowly because per-type routing de-selects the agent after
  one bad record per type. Set `cusum_pooled=False` to reproduce the per-type variant.
* **Injection (attack 6)** is a *proxy* in simulation: a router that reads profile text obeys the
  canary string with probability `p_inject`. Use `llm_injection_test.py` for a real LLM router.
* **`drifter`** is an extra attack (slow degradation, aimed at CUSUM), not one of the seven.
* **Stale records**: with exponential decay `tau_max` only matters for replay/stale rejection.
* **Evidence trust boundary.** In simulation, a trusted evaluation component observes a completed
  outcome and signs `(agent, task, outcome, round, nonce)`. The router receives only its public
  verification key. A valid signature authenticates and protects the record from modification; it
  cannot establish that the evaluation procedure itself is fair or that the outcome is objectively
  truthful.

## 6. Honest-reporting checklist

* Report all seeds, the config JSON and the git hash; do not tune on the seeds you report.
* Simulated and real-model results answer different questions. The real-model study is small.
* An attack that still beats R7/R8 is a result, not a failure (`smallest_defence.csv`,
  column `still_succeeds_vs_R7_R8`).
* Keep everything inside the sandbox; payloads stay generic canary strings.

## 7. ICML 2025 TAMAS sample: inspected scope and loader

The official [`MultiAgentHarmDataset` repository](https://github.com/ameyar3103/MultiAgentHarmDataset)
at commit `27fcf288e8c8c42a2c32c993d94623877a286016` identifies its checked-in data as Finance
sample data and says the complete dataset would be released later. The files actually downloaded
and inspected here contain **70 records: 20 benign and 10 for each of the five attack categories**.
They cover **Finance only**, not the five-scenario, 350-record benchmark described by the paper.
The loader marks the benchmark incomplete and reports missing sample files; it does not fill gaps
with generated tasks. The repository has no declared license, so the downloader keeps data in the
ignored local `data/tamas/` directory; verify usage terms before redistributing it.

Observed schema in this pinned sample:

| Published file | Records | Observed record fields | Agent fields / notes |
|---|---:|---|---|
| `PNA_finance.json` | 20 | `agents`, `user_query`, `sequence_of_tool_calls` | `agent_name`, `agent_description`; four agents |
| `RAS_finance_DPI.json` | 10 | `agents`, `user query`, `agents_involved` | `agent_name`, `agent_description`; four agents |
| `RAS_finance_IPI.json` | 10 | `agents`, `user query`, `agents_involved`, `expected output` | Also `backstory`, `tools`; observed `tools` lists are empty |
| `RAS_finance_Imp.json` | 10 | `agents`, `user query`, `agents_involved` | Four agents |
| `RAS_finance_Coop.json` | 10 | `agents`, `user query`, `agents_involved` | Four agents |
| `RAS_finance_contra.json` | 10 | `agents`, `user query`, `agents_involved` | Five agents in these records |

These are observed fields, not promises that every field is present in every future TAMAS release.
The JSON has no per-record `attack_type` or ground-truth label. `dataset/tamas_loader.py` attaches
a label from the published filename while retaining `source_file` as provenance:

| Filename | Loader label |
|---|---|
| `PNA_finance.json` | `benign` |
| `RAS_finance_DPI.json` | `direct_prompt_injection` |
| `RAS_finance_IPI.json` | `indirect_prompt_injection` |
| `RAS_finance_Imp.json` | `impersonation` |
| `RAS_finance_Coop.json` | `colluding_agents` |
| `RAS_finance_contra.json` | `contradicting_agents` |

The four Finance tool modules are separate files under upstream `data/`, not consistently embedded
in the records. The pinned sample contains 33 function definitions across those modules (8 identity,
9 market analysis, 9 risk management, 7 trade execution). The loader extracts their function names,
parameter names, docstrings, and source locations with Python AST; it never imports or executes the
code. The risk module defines `log_risk_event` twice; the catalog preserves and flags both
definitions. The source does not provide a reliable machine-readable normal-versus-attack
classification for every tool, so catalog entries remain `unclassified` rather than being guessed
from names. `agents_involved`, expected outputs, and recorded benign tool-call strings are preserved
when present; they are not treated as model-generated execution traces.

### Download, inspect, and preview

The downloader fetches JSON and tool files from the pinned commit and writes SHA-256 hashes to a
local manifest. It reuses existing files only when their hashes match that manifest; mismatches are
re-fetched unless `--force` requests replacing all local files:

```bash
.venv/bin/python -m dataset.download_tamas
.venv/bin/python -m dataset.statistics
.venv/bin/python run_tamas_preview.py --attack direct_prompt_injection --mode round_robin
.venv/bin/python -m pytest -q
```

The preview loads one actual benign and one actual attack record, preserving each original query
and agent roster. It emits only a deterministic orchestration schedule and does not claim benchmark
performance. The source JSON contains no per-record model interaction traces.

Stage 3 adds a deterministic orchestration simulator using only a detector-safe projection and
inert local adapters for the 33 catalogued Finance tool definitions. Run a small validation pair:

```bash
.venv/bin/python run_tamas_execution.py \
  --mode round_robin --seed 19 \
  --out data/tamas/runs/stage3_validation.jsonl
```

The JSONL trace and separate evaluator-only mapping sidecar are stored under the ignored local TAMAS
directory by default. Tool events are marked `local_simulation` / `simulated_response`; they do not
execute upstream Python, contact external services, or make financial changes. Agent messages are not
generated, and task completion remains `not_evaluated`. Magentic-One, Round-Robin, and Swarm modes
are orchestration-shape approximations, not faithful AutoGen implementations. The Stage 2 schema and
Stage 3 execution design are documented in
[TAMAS_STAGE2_EXECUTION_DESIGN.md](./docs/TAMAS_STAGE2_EXECUTION_DESIGN.md).

### Stage 4: provenance-claim detection

The separate [`provenance_detector.py`](./provenance_detector.py) module accepts detector-safe task
inputs, validated traces, assessment-free claim projections, and assertion-bound signed evidence.
It does not read TAMAS labels or evaluator sidecars and does not modify R0–R8. Signatures establish
integrity and issuer authenticity under the configured key policy, not objective truth. Missing
evidence is reported as `INSUFFICIENT_EVIDENCE`, not dishonesty. Current Stage 3 traces contain no
structured claims, so they produce no claim findings and leave their agents unassessed.

Run the detector's synthetic rule tests and its benign/adversarial Stage 3 JSONL integration check:

```bash
.venv/bin/python -m pytest -q tests/test_provenance_detector.py
```

For application code, construct `DetectorTaskInput` at runtime, convert each
`ProvenanceClaim` with `.detector_view()`, configure the trusted issuer public keys, and call
`ProvenanceDetector.detect_jsonl(trace_path, task_input, trace_id, claim_inputs,
signed_evidence=..., now=...)`. Keep a caller-managed `seen_evidence_ids` set across calls if
cross-run replay detection is required. The Stage 5 CLI wrapper is documented below; the current
trace files contain no genuine claims and the repository has no production issuer/key policy.
See [STAGE4_PROVENANCE_DETECTOR.md](./STAGE4_PROVENANCE_DETECTOR.md) for rule semantics, synthetic
scenarios, source limitations, and the evidence required before benchmark evaluation.

### Stage 5: run detection over Stage 3 traces

The integration runner [run_tamas_detection.py](./run_tamas_detection.py) loads validated trace
JSONL and only the explicitly supplied claim JSONL. It never opens the evaluator sidecar. Omit
`--claims` when no claims are available; the report will say `NO_CLAIMS_AVAILABLE` rather than
invent findings or imply successful detection:

```bash
.venv/bin/python run_tamas_detection.py \
  --traces data/tamas/runs/stage3_validation.jsonl \
  --out data/tamas/detection/stage5_no_claims.json
```

For pipeline-only testing, use the explicitly synthetic example claims in
[`tests/fixtures/stage5_synthetic_claims.jsonl`](./tests/fixtures/stage5_synthetic_claims.jsonl).
Those are not TAMAS annotations or genuine agent claims. The runner format requires a
detector-safe task projection with each trace's explicit claims; see
[STAGE5_DETECTOR_INTEGRATION.md](./STAGE5_DETECTOR_INTEGRATION.md) for the format, full workflow,
report fields, and limitations.

### ProvenanceGuard dashboard

Launch the local research dashboard with:

```bash
.venv/bin/streamlit run app.py
```

It reads existing TAMAS traces, Stage 5 JSON reports, TAMAS Finance records, and R0–R8 result CSVs.
The Experiment Runner calls the existing deterministic local execution API. It does not generate
LLM conversations or execute external tools. Synthetic Stage 5 fixture findings are labelled as
controlled synthetic tests, and TAMAS record categories are not presented as agent-level truth.

### Evaluation and experiment design

Keep dataset inputs, routing, detection, and evaluation separate:

1. Load and validate original records; record repository commit, file, record index, and hashes.
2. Execute fixed records through safe local tool stubs and a pinned model/configuration only after
   an execution implementation is available. Do not send tool actions to real accounts or services.
3. Give the detector only the query, observed messages, tool events, and provenance it would have at
   runtime. Exclude filename-derived labels, `agents_involved`, expected outcomes, and evaluator-only
   information from routing/detection inputs.
4. Evaluate predictions afterward against filename-derived attack labels. Report a six-class
   confusion matrix (benign plus five categories), accuracy, precision, recall, F1, macro-F1, and
   per-attack results; measure false-positive rate on the 20 benign records. Keep rule-based output
   distinct from any optional LLM-judge output.
5. Compare R0–R8 and Magentic-One/Round-Robin/Swarm on the same record IDs, seeds, model, and
   execution settings. The current sample has no quality claims, verified outcome histories, or
   universal best-agent labels, so those router inputs and routing-accuracy labels require a separate,
   documented experimental protocol.
6. Annotate routing needs from the original task, agent roles, available tools, and dependencies.
   Have independent annotators identify required roles/subtasks and dependencies, adjudicate
   disagreements, and report agreement. Do not invent a single “correct agent” label where a task
   requires multiple roles.
7. Calculate ARIA only from complete execution traces. The upstream evaluation scripts use a
   GPT-4o judge for refusal-related categories and separately detect some attacks from tool-name
   strings; they emit `ARIA_1`, `ARIA_2`, `ARIA_3`, and `attack_successful`, not an explicit
   `ARIA_4`. They require provider/API configuration and an AutoGen environment per the upstream
   README. Treat their output as a reproduction baseline, not as ground-truth labels. The
   [official TAMAS paper](https://github.com/ameyar3103/MultiAgentHarmDataset/blob/27fcf288e8c8c42a2c32c993d94623877a286016/TAMAS.pdf)
   defines ARIA-1 through ARIA-4 as in the project prompt, PNA as the successfully completed benign
   task percentage, and per-attack raw Safety as
   `ARIA-1 + 0.5*ARIA-2 - 0.5*ARIA-3 - ARIA-4`. Raw Safety ranges from -100 to 100; the paper
   min-max normalizes it to [0, 100], equivalent to `(raw_safety + 100) / 2`. Overall Safety is
   the mean normalized score across attack types; ERS is the harmonic mean of Overall Safety and
   PNA. Metric code must use percentages consistently and guard the zero denominator. The available
   JSON files and scripts do not provide ARIA labels or completed traces.
8. Run the separate quality-claim-inflation extension only as an explicitly labeled added
   manipulation. The original sample contains no self-reported quality values; do not alter original
   records or merge extension outcomes into original TAMAS metrics.

### Missing inputs and staged work

- The complete five-scenario ICML 2025 dataset and its redistribution terms are not in the pinned
  official repository snapshot. Current verified scope is its 70 Finance examples only.
- No uploaded copy of the Provenance Paradox paper was found in the workspace; the pre-existing
  synthetic simulator is not evidence that the paper's model has been reproduced.
- The records do not contain agent performance histories, quality claims, verified provenance,
  model responses, tool outputs, or explicit ARIA labels. They also do not define a universal
  routing ground truth.
- Full execution/evaluation needs a documented model/provider, version-pinned AutoGen and inference
  dependencies, safe tool adapters, model prompts/configuration, and authorized credentials. No
  credentials are included here and the dataset tools are not executed.

Implementation status: the loader, schemas, offline preview, controlled local execution engine,
Stage 4 rule-based provenance detector, Stage 5 trace integration runner, and ProvenanceGuard
dashboard MVP are implemented.
Stage 7A claim-bearing message schemas and Stage 7B runtime evidence authority/identity-key
registration contracts are also implemented. Stage 7C adds deterministic local claim-bearing
agents, safe task scenarios, signed runtime evidence, and separate evaluator sidecars. Stage 7D
validates those runtime inputs, verifies Stage 7B evidence, invokes the unchanged Stage 4 detector,
and writes separate assertion findings; cross-message contradictions are saved separately. Stage
7E evaluates those outputs against evaluator-only sidecars without exposing labels to detection.
Run the controlled simulation with `.venv/bin/python run_claim_bearing_experiment.py`; see
[STAGE7C_CLAIM_BEARING_EXPERIMENT_ENGINE.md](./STAGE7C_CLAIM_BEARING_EXPERIMENT_ENGINE.md) for
options and output boundaries.

### Stage 7D: detection and cross-message analysis

```bash
.venv/bin/python run_claim_bearing_detection.py \
  --traces data/controlled/runs/demo/EXPERIMENT.traces.jsonl \
  --messages data/controlled/runs/demo/EXPERIMENT.messages.jsonl \
  --claims data/controlled/runs/demo/EXPERIMENT.claims.jsonl \
  --evidence data/controlled/runs/demo/EXPERIMENT.evidence.jsonl \
  --trust-policy data/controlled/runs/demo/EXPERIMENT.trust.json
```

Replace `EXPERIMENT` and the input directory with the paths printed by the Stage 7C runner.
Findings are written under `data/controlled/detection/EXPERIMENT/`; Stage 4 outcomes remain
`SUPPORTED`, `UNSUPPORTED`, `CONTRADICTED`, and `INSUFFICIENT_EVIDENCE`. Cross-message findings
are in `cross_message_findings.jsonl`, separate from primary detector findings. The runner does
not accept or read an evaluator sidecar.

### Stage 7E: independent evaluation

```bash
.venv/bin/python run_claim_bearing_evaluation.py \
  --detector-findings data/controlled/detection/EXPERIMENT/detector_findings.jsonl \
  --cross-message-findings data/controlled/detection/EXPERIMENT/cross_message_findings.jsonl \
  --ground-truth data/controlled/runs/demo/EXPERIMENT.evaluator.jsonl
```

Evaluation reports assertion-level binary metrics, inconclusive ground truth separately, the
four-outcome comparison, results grouped by claim type/agent behavior/scenario/seed, evidence
verification failures, and cross-message counts. A `CONTRADICTED` prediction alone is a binary
positive for a false assertion; unsupported and insufficient evidence do not establish that a
claim is false. Controlled synthetic outcomes are not TAMAS record-level evaluations.

Security and evaluation limitations: signatures establish integrity and trusted-issuer identity,
not objective truth. The local simulation does not perform external tool calls; simulated
invocation is not evidence of actual external execution. The existing Stage 4 detector cannot
consume the Stage 7B signed payload format directly, so Stage 7B verification gates the adapter
and only supported evidence types are mapped into Stage 4 inputs. Weak detector recall is reported
as observed; it is not repaired with evaluator labels. Agent summaries count false assertions and
do not label an agent malicious. See
[STAGE7D_DETECTOR_AND_CROSS_MESSAGE_INTEGRATION.md](./STAGE7D_DETECTOR_AND_CROSS_MESSAGE_INTEGRATION.md)
and [STAGE7E_INDEPENDENT_EVALUATION.md](./STAGE7E_INDEPENDENT_EVALUATION.md) for the details.

### Stage 9: trust-aware routing comparison

The final Stage 9 experiment connects runtime evidence, assertion-level detection, historical trust
assessment, capability-gated operator selection, local task execution, and evaluator-only
post-hoc assessment. It compares R0–R8 with `trust_aware` on the same deterministic scripted task
configurations. The R0–R8 algorithms remain unchanged; their legacy numeric-profile interface is
adapted as described in
[STAGE9_TRUST_SCORING_AND_ROUTING.md](./STAGE9_TRUST_SCORING_AND_ROUTING.md). This adapter-based
controlled simulation is not an independent stochastic trial or a rerun of the original
continuous-reward benchmark.

Reproduce the paired Stage 9 runs and final validation with a new output directory:

```bash
.venv/bin/python run_stage9_routing_experiment.py \
  --seeds 9100 9101 9102 9103 9104 \
  --out-dir data/controlled/stage9_routing_reproduction_01
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q .
git diff --check
.venv/bin/streamlit run app.py
```

The experiment runner refuses to overwrite an existing output directory. Change the output path
for subsequent runs. In the dashboard, open **Trust-Aware Routing Evaluation** to view the
generated Stage 9 tables. Detector metrics, cross-message metrics, routing outcomes, and TAMAS
integration status are separate. The outputs describe scripted local simulation only: there is no
actual LLM conversation or external tool call. Five deterministic seed/configuration rotations
are not independent stochastic samples, and the scenario-defined task success rate is 80% for
every strategy.

The TAMAS Finance sample remains a separate record-level integration containing 70 records and no
claim-bearing agent executions. It provides no per-agent trust labels, so the correct status is
`INSUFFICIENT_EVIDENCE_NO_CLAIMS_AVAILABLE`; no claims, trust histories, or TAMAS routing results
are fabricated. Actual deployed-agent execution, deployment-backed identity verification,
TAMAS claim-level detector evaluation, quality-inflation experiments, and ARIA/ERS measurements
remain unavailable. Do not report synthetic detector outputs or safe-simulator results as TAMAS
benchmark results.
