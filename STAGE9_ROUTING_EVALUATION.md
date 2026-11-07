# Stage 9 Routing Evaluation

## Experiment protocol

The final retained comparison is under
[`data/controlled/stage9_routing_20261107_final_v3/`](./data/controlled/stage9_routing_20261107_final_v3/).
It contains 10 strategy conditions (R0–R8 and `trust_aware`) × five paired
seed/configuration rotations × five controlled scenarios: 25 task runs per
strategy and 250 total task runs. Each task has five scripted agents, one
selected operator, a local-only operation, runtime evidence, a Stage 4
detector report, a separate Stage 7D report, and an evaluator-only sidecar.
The controller writes each routing decision before task generation. The
decision record does not contain behavior labels, expected values, or
evaluator outcomes.

Seeds 9100–9104 rotate the five behavior-to-slot assignments and the scenario
execution order. They are meaningfully different deterministic configurations,
not independent stochastic samples. The same seed/configuration is paired
across all ten strategies. The default trust weights were fixed before this
final evaluation; no evaluation labels were used to tune them.

The common task set yields four successful local tasks and one
`missing_lookup` failure per seed. The result is deliberately scenario-defined:
all strategies achieve 20/25 task completions (80%), and the selected agent
does not affect whether a lookup key exists. This comparison therefore cannot
demonstrate a task-success improvement from trust-aware routing.

## Routing and Stage 4 results

| Strategy | Tasks | Success | Selected agents with false assertions | Selected evidence coverage | Pre-decision verified capability demonstrations | Stage 4 TP / FP / TN / FN | Precision | Recall | F1 | Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| R0 | 25 | 20/25 (0.800) | 22 | 0.777 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R1 | 25 | 20/25 (0.800) | 25 | 0.851 | 6 | 0 / 0 / 724 / 100 | N/A | 0 | N/A | 0.8786 |
| R2 | 25 | 20/25 (0.800) | 22 | 0.800 | 1 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R3 | 25 | 20/25 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R4 | 25 | 20/25 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R5 | 25 | 20/25 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R6 | 25 | 20/25 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R7 | 25 | 20/25 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| R8 | 25 | 20/25 (0.800) | 22 | 0.800 | 0 | 1 / 0 / 718 / 100 | 1.000 | 0.0099 | 0.0196 | 0.8779 |
| trust_aware | 25 | 20/25 (0.800) | 17 | 0.811 | 9 | 0 / 0 / 724 / 100 | N/A | 0 | N/A | 0.8786 |

`N/A` precision/F1 means there were no positive `CONTRADICTED` predictions;
it is not reported as a perfect value. `CONTRADICTED` is the only Stage 4
binary positive. The 100 false negatives are false assertions not predicted
`CONTRADICTED`; unsupported and insufficient evidence are not treated as proof
that a claim is false. No detector false positives were observed.

The four-outcome Stage 4 distributions were:

| Strategies | Assertions per strategy | SUPPORTED | UNSUPPORTED | CONTRADICTED | INSUFFICIENT_EVIDENCE |
|---|---:|---:|---:|---:|---:|
| R0, R2–R8 | 819 | 20 | 125 | 1 | 673 |
| R1, trust_aware | 824 | 20 | 125 | 0 | 679 |

The slightly different assertion totals arise from the generated operator
assignment and its associated runtime claims. Evidence-signature verification
failures were zero for all strategy runs. For `trust_aware`, the evaluator
reported 43 evidence-not-relevant associations, 25 evidence-relationship
mismatches, and 125 scope mismatches. These are association failures, not
proof that the associated assertions are false.

The 250 selected agents produced 17 false-assertion-bearing task runs under
`trust_aware`, compared with 22 under R0 and 22 under each R2–R8; R1 had 25.
This is a descriptive difference on a small scripted corpus, not evidence that
the system is a successful false-agent classifier. The selected behavior mix
for `trust_aware` was:

| Selected behavior | Selected tasks | Selected tasks with false assertions |
|---|---:|---:|
| HONEST_AGENT | 8 | 0 |
| FALSE_REPORTING_AGENT | 8 | 8 |
| EVIDENCE_MISREPRESENTATION_AGENT | 7 | 7 |
| IDENTITY_MISMATCH_AGENT | 1 | 1 |
| CAPABILITY_EXAGGERATION_AGENT | 1 | 1 |

Selection of an agent whose previous history contained at least one
INSUFFICIENT_EVIDENCE finding occurred in 20/25 selections for every strategy.
For `trust_aware`, those selected histories contained 281 accumulated
insufficient-evidence findings (counts can repeat across decisions). This
reflects the corpus's evidence limitations and the conservative scoring rule:
insufficient evidence is not treated as dishonesty.

## Per-seed and scenario results

Each strategy had 5 tasks per seed: 4 successful and 1 unsuccessful scenario,
so task success was 0.8 for all five seeds and all strategies. For
`trust_aware`, the number of selected-agent task runs containing false
assertions by seed was:

| Seed/configuration | Tasks | Task successes | Selected tasks with false assertions | Selected evidence coverage |
|---:|---:|---:|---:|---:|
| 9100 | 5 | 4 | 3 | 0.857 |
| 9101 | 5 | 4 | 5 | 0.857 |
| 9102 | 5 | 4 | 1 | 0.857 |
| 9103 | 5 | 4 | 4 | 0.743 |
| 9104 | 5 | 4 | 4 | 0.743 |

Across all strategies, each of the arithmetic, local lookup, authorized local
tool, and relay scenarios succeeded 50/50 times; the missing lookup failed
50/50 times. Per-strategy per-seed, per-scenario, and selected-behavior tables
are retained in `routing_strategy_group_metrics.csv`. Stage 4 breakdowns by
claim type, behavior, scenario, and seed are in each strategy's
`evaluation/<strategy>/evaluation_summary.json`.

## Cross-message evaluation (separate metric)

The independent cross-message evaluator matched every emitted finding to
sidecar-eligible assertions and exact task/trace/agent/message associations.
For each of the ten strategies:

| Finding type | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Identity claim conflicts with sender | 25 | 0 | 0 | N/A | 1.000 | 1.000 | 1.000 |
| Permission misrepresented as demonstration | 25 | 0 | 0 | N/A | 1.000 | 1.000 | 1.000 |
| **Total** | **50** | **0** | **0** | **N/A** | **1.000** | **1.000** | **1.000** |

True negatives are undefined because the evaluation has no defensible finite
universe of absent cross-message findings. The corpus also reports 19
capability pairs as ineligible and 51 false assertions as ineligible for the
supported cross-message rules. These results are specific to repeated
scripted contradiction patterns; they do not measure generalization.

The cross-message result does not change or combine with Stage 4 metrics.
There is no combined accuracy score.

## Confidence intervals and interpretation

Task-success intervals use a cluster bootstrap over the five seed/configuration
clusters (2,000 resamples), not individual correlated assertions. Each cluster
has the same fixed 4/5 success rate, so the percentile interval is [0.8, 0.8].
Five deterministic clusters are insufficient for reliable inferential
confidence intervals; this interval is descriptive only. No assertion-level
bootstrap was used. Cross-message intervals are not estimated because the
corpus contains structurally paired deterministic patterns.

The legacy R0–R8 algorithms were not modified. Their existing simulator uses
numeric quality/reward feedback, whereas Stage 7C uses scripted
claim-bearing agents and runtime evidence. The Stage 9 comparison adapts the
legacy interface using the documented signal mapping in
[`STAGE9_TRUST_SCORING_AND_ROUTING.md`](./STAGE9_TRUST_SCORING_AND_ROUTING.md).
It is a controlled adapter comparison, not a direct apples-to-apples
reproduction of the original continuous-reward benchmark.

## Reproduction and retained data

```bash
.venv/bin/python run_stage9_routing_experiment.py \
  --seeds 9100 9101 9102 9103 9104 \
  --out-dir data/controlled/stage9_routing_reproduction_01
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q .
git diff --check
```

The output directory must be new; the runner refuses to overwrite an existing
directory. Each seed/strategy/task subdirectory has separate `source/` and
`detection/` outputs. Evaluator sidecars remain in the source/evaluator path
and are read only after route and detection runs. Aggregate machine-readable
outputs include `routing_decisions.jsonl`, `routing_evaluation.json`,
`detector_and_cross_message_evaluation.json`, per-strategy evaluator reports,
`strategy_comparison.csv`, `routing_group_metrics.csv`,
`routing_strategy_group_metrics.csv`, `manifest.json`, and
`tamas_integration_status.json`.
