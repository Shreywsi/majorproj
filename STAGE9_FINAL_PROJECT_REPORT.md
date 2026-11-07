# Stage 9 Final Project Report

## Executive summary

Stage 9 completes the controlled local-simulation pipeline:

**runtime evidence → Stage 4 provenance detection → decision-time trust history
→ capability-gated agent selection → local task execution → independent
post-hoc evaluation.**

The new `trust_aware` strategy is integrated into the existing Stage 7C
claim-bearing experiment engine and compared with unchanged R0–R8 routing
algorithms. Routing, assertion detection, cross-message analysis, task
completion, and TAMAS record-level integration are reported as separate
evaluation dimensions. No overall accuracy score combines them.

The experiment does not show a task-completion improvement: every strategy
completed 20 of 25 tasks (80%) because success is determined by the controlled
scenario, including a fixed missing-key failure. `trust_aware` selected agents
whose task artifacts contained false assertions in 17/25 tasks, compared with
22/25 for R0 and R2–R8 and 25/25 for R1. This is a descriptive result on a
small scripted corpus, not evidence that the system is a successful
false-agent classifier. Stage 4 detection recall remains very low, and
cross-message findings remain a separate metric.

## Architecture and implementation

| Component | Implementation |
|---|---|
| Trust scoring | [`dataset/agent_trust_scoring.py`](./dataset/agent_trust_scoring.py) |
| Capability-gated routing | [`dataset/trust_aware_router.py`](./dataset/trust_aware_router.py) |
| Stage 7C selected-operator integration | [`dataset/claim_bearing_agents.py`](./dataset/claim_bearing_agents.py) |
| Paired experiment runner and artifact persistence | [`run_stage9_routing_experiment.py`](./run_stage9_routing_experiment.py) |
| Independent routing evaluation | [`dataset/stage9_routing_evaluator.py`](./dataset/stage9_routing_evaluator.py) |
| Dashboard extension | [`app.py`](./app.py) |
| Regression and integration tests | [`tests/test_stage9_trust_routing.py`](./tests/test_stage9_trust_routing.py), [`tests/test_dashboard.py`](./tests/test_dashboard.py) |

The router consumes only runtime task requirements, candidate identity and
capability declarations, prior detector outcomes, evidence-verification
statuses, identity checks, cross-message findings, and prior capability
observations. Its types contain no behavior-label, expected-output, or
evaluator-sidecar fields. The experiment controller updates online history
only after a task has completed; that history can affect later decisions, not
the decision that generated it. Sidecars are joined after routing by the
independent evaluator. Tests check the router's input schema, rejected
evaluator-only fields, and deterministic replay. This verifies the implemented
information-flow boundary, not a process or operating-system sandbox against
malicious in-process Python code.

### Trust formula and selection

Defaults are fixed and were not tuned against this evaluation:

* Assertion reliability: 0.40, Beta(1,1)-smoothed over SUPPORTED and
  CONTRADICTED only.
* Evidence integrity: 0.20, smoothed valid evidence records over recorded
  verification statuses.
* Identity binding: 0.15, smoothed verified identity checks.
* Cross-message consistency: 0.15, smoothed checks without findings.
* Evidence coverage: 0.10, evaluated assertions with a valid evidence
  association.

Unobserved components use neutral 0.5 priors; coverage is reported as
unavailable when there are no evaluated assertions. The weighted raw score is
shrunk toward 0.5 by `n / (n + 3)`, where `n` is the number of prior evaluated
assertions. Fewer than three observations is marked cold start. UNSUPPORTED
and INSUFFICIENT_EVIDENCE remain separate from CONTRADICTED and do not count as
false assertions. A valid signature authenticates a record, not objective
truth. The full component definitions and caveats are in
[`STAGE9_TRUST_SCORING_AND_ROUTING.md`](./STAGE9_TRUST_SCORING_AND_ROUTING.md).

Routing first filters for role, declared capability, and identity eligibility;
then prefers verified capability demonstrations over configured-permission
cold-start fallback. Within the selected capability tier it ranks by smoothed
trust score and resolves ties by stable agent ID. A local simulated
demonstration is never called external execution. There is no permanent
blacklist or agent-level malicious label.

### Baseline adapter

R0–R8 algorithms in [`router.py`](./router.py) were not modified. The
claim-bearing runner adapts the legacy numeric profile interface: R0 uses its
unchanged random policy; R1 receives a smoothed self-reported `RECORDED` rate;
R2–R8 receive an authenticated summary derived from prior detector outcomes
(SUPPORTED=1, CONTRADICTED=0, UNSUPPORTED/INSUFFICIENT_EVIDENCE=0.5) and use
their unchanged policy logic. The signature authenticates the controller's
detector-derived input, not independent truth. This is an adapter-based
comparison, not a reproduction of the legacy continuous-reward benchmark
using identical task semantics.

## Experimental conditions and results

The canonical final data is in
[`data/controlled/stage9_routing_20261107_final_v3/`](./data/controlled/stage9_routing_20261107_final_v3/).
It compares 10 strategies (R0–R8 plus `trust_aware`) over five scenarios,
five agents, and five paired deterministic seed/configuration rotations:
25 tasks per strategy, 250 task runs total. All five scripted behaviors are
present: HONEST_AGENT, FALSE_REPORTING_AGENT, IDENTITY_MISMATCH_AGENT,
CAPABILITY_EXAGGERATION_AGENT, and EVIDENCE_MISREPRESENTATION_AGENT.

Seeds 9100–9104 rotate behavior-to-slot assignment and scenario order. They
change actual experimental conditions but are deterministic configurations,
not independent stochastic samples. Each task records 2 handoffs; each
strategy has 50 handoffs and 25 local simulated tool invocations. No reliable
routing-latency measurement was made.

### Strategy comparison

| Strategy | Tasks | Success | False-assertion-bearing selected tasks | Evidence coverage | Prior verified capability demonstrations | Stage 4 TP / FP / TN / FN | Precision | Recall | F1 | Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| R0 | 25 | 20 (0.800) | 22 | 0.777 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R1 | 25 | 20 (0.800) | 25 | 0.851 | 6 | 0 / 0 / 724 / 100 | N/A | 0 | N/A | 0.8786 |
| R2 | 25 | 20 (0.800) | 22 | 0.800 | 1 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R3 | 25 | 20 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R4 | 25 | 20 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R5 | 25 | 20 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R6 | 25 | 20 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R7 | 25 | 20 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R8 | 25 | 20 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| trust_aware | 25 | 20 (0.800) | 17 | 0.811 | 9 | 0 / 0 / 724 / 100 | N/A | 0 | N/A | 0.8786 |

For strategies with no positive `CONTRADICTED` predictions, precision and F1
are undefined (shown as N/A), not perfect. The Stage 4 outcomes for R0 and
R2–R8 were SUPPORTED 20, UNSUPPORTED 125, CONTRADICTED 1, and
INSUFFICIENT_EVIDENCE 673 per strategy. R1 and `trust_aware` had 20, 125, 0,
and 679 respectively. The 100 false negatives per strategy are false
assertions not predicted CONTRADICTED. No Stage 4 false positives or signature
verification failures were observed. Evidence-association failures for
`trust_aware` were 43 not-relevant, 25 relationship mismatches, and 125 scope
mismatches; these are not proof that associated assertions are false.

The trust-aware selection mix was 8 HONEST_AGENT, 8 FALSE_REPORTING_AGENT,
7 EVIDENCE_MISREPRESENTATION_AGENT, 1 IDENTITY_MISMATCH_AGENT, and 1
CAPABILITY_EXAGGERATION_AGENT. The selection counts do not amount to
agent-level classifications. Twenty of 25 selected histories per strategy
had prior INSUFFICIENT_EVIDENCE findings; the router did not treat those
findings as proof of dishonesty.

All strategies succeeded on arithmetic, local lookup, authorized local tool,
and relay tasks (50/50 in the paired aggregate), and failed the
`missing_lookup` scenario (50/50). Thus this experiment's 80% success rate is
a fixed property of the task set and not a routing benefit.

### Stage 4, cross-message, and prior Stage 8 detector results

Stage 4 detection and cross-message findings are separate. The Stage 9
trust-aware Stage 4 result is TP=0, FP=0, TN=724, FN=100, with recall 0;
R0 and R2–R8 each have TP=1, FP=0, TN=718, FN=100, with recall 0.009901.
No combined accuracy is computed.

The sidecar-based cross-message evaluation found 50/50 eligible patterns per
strategy: 25 identity-claim/sender conflicts and 25
permission-misrepresented-as-demonstration findings; each type had TP=25,
FP=0, FN=0, precision=1, recall=1, F1=1. TN is undefined because no finite
universe of absent findings was defined. These are repeated scripted
patterns, not generalization evidence.

The prior Stage 8 expanded corpus remains unchanged at 820 assertions:
TP=1, FP=0, TN=719, FN=100, precision=1.000000, recall=0.009901, F1=0.019608,
accuracy=0.878049. Its separately evaluated cross-message result was 50/50.
Stage 8 artifacts were preserved; those results are not merged with Stage 9
routing outcomes.

### Reproducibility, uncertainty, and limitations

The five seed/configuration clusters produce the same 4/5 task-success rate.
A cluster bootstrap percentile interval is [0.8, 0.8], but five deterministic
clusters are too few for reliable inferential claims. Assertion-level
resampling was avoided because assertions are correlated within tasks.
Cross-message intervals were not estimated for deterministic paired patterns.

Other important limits:

* Agents are scripted, not LLMs or deployed autonomous agents.
* Tool calls are local simulations, not external execution.
* Tasks do not make success depend on agent choice, so task-success comparison
  cannot demonstrate routing utility.
* The baseline signal adapter does not recreate legacy continuous-reward
  semantics.
* Stage 4 recall remains very low and evidence association limitations are
  prominent.
* Five configurations, five scenarios, and repeated scripted behaviors do
  not support broad population claims or reliable confidence intervals.
* No router timing measurement or real-world identity assurance is available.

## TAMAS Finance integration

TAMAS is separate from the synthetic routing results. Its local sample has 70
records (20 benign, 50 adversarial), four existing local simulation traces,
14 simulated tool invocations, zero claims, and zero detector findings.
Available record-level categories are 10 each for colluding agents,
contradicting agents, direct prompt injection, impersonation, and indirect
prompt injection, plus 20 benign records. There are no genuine claim-bearing
agent executions or per-agent ground-truth labels. The status is
`INSUFFICIENT_EVIDENCE_NO_CLAIMS_AVAILABLE`; per-agent accuracy and
trust-aware TAMAS routing are unavailable. `agents_involved` and task labels
were not used as router trust inputs. Full details are in
[`STAGE9_TAMAS_INTEGRATION_STATUS.md`](./STAGE9_TAMAS_INTEGRATION_STATUS.md).

## Dashboard and validation

The existing Streamlit dashboard was extended additively with the
**Trust-Aware Routing Evaluation** page. It loads the canonical Stage 9
machine-readable files and displays trust scores/components, evidence
coverage, demonstrated capability and decision explanations, R0–R8 comparison,
task metrics, selection behavior counts, per-seed/per-scenario views, separate
detector and cross-message metrics, and TAMAS status. The focused dashboard
test rendered this page with the generated Stage 9 directory present.

Validation executed:

```text
.venv/bin/python -m pytest -q tests/test_stage9_trust_routing.py tests/test_dashboard.py
32 passed in 9.62s

.venv/bin/python -m pytest -q
223 passed in 57.39s

.venv/bin/python -m compileall -q .
passed (exit code 0)

git diff --check
passed (exit code 0)
```

The full test suite includes existing Stage 7/8 and dashboard tests as well as
the Stage 9 trust, router, reproducibility, leakage-boundary, and evaluation
coverage.

## Files and reproduction

Stage 9 source and test additions/changes:

* Added `dataset/agent_trust_scoring.py`,
  `dataset/trust_aware_router.py`, `dataset/stage9_routing_evaluator.py`,
  `run_stage9_routing_experiment.py`, and
  `tests/test_stage9_trust_routing.py`.
* Extended `dataset/claim_bearing_agents.py`, `app.py`,
  `tests/test_dashboard.py`, and `README.md`.
* Added this report and the implementation plan, scoring/routing,
  evaluation, and TAMAS reports.
* Preserved machine-readable output, source artifacts, evidence, detector and
  cross-message findings, evaluator sidecars, routing decisions, and CSVs in
  the canonical Stage 9 output directory. Earlier Stage 9 output directories
  and Stage 7/8 artifacts were not overwritten.
* No commit was created. R0–R8 algorithms and Stage 7/8 outputs were not
  modified by the Stage 9 routing experiment.

To reproduce with a new output directory:

```bash
.venv/bin/python run_stage9_routing_experiment.py \
  --seeds 9100 9101 9102 9103 9104 \
  --out-dir data/controlled/stage9_routing_reproduction_01
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q .
git diff --check
.venv/bin/streamlit run app.py
```

The runner refuses to overwrite an existing output directory; use a different
fresh path for each run. After acceptance of this implementation, remaining
work is project reporting, presentation, demonstration, and viva preparation.

## Research directions

Potential follow-on research—not additional implementation stages—includes
evaluating on larger independently annotated claim-bearing tasks, task sets
where verified capability affects task success, repeated stochastic
configurations with enough independent runs for uncertainty estimates, and
authorized real-agent/tool execution with explicit deployment identity and
provenance guarantees. Such evaluation would require a separately defined
protocol and independent labels; the current results do not establish those
claims.
