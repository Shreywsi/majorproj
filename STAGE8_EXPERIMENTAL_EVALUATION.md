# Stage 8 Experimental Evaluation

## Scope and evidence boundary

This report evaluates persisted Stage 7 controlled runs and five new Stage 8
controlled behavior-to-agent-slot rotations. It does not tune detector rules,
change evaluation labels, or combine detector and cross-message metrics. The
Stage 4 provenance detector received runtime traces, messages, claims, evidence,
and trust policy only. Evaluator sidecars were read only by Stage 7E/Stage 8
analysis.

The Stage 7 baseline uses seeds 701–703 (495 assertions). Stage 8 uses seeds
810–814 (820 assertions total) across the five existing scenarios and all five
scripted behaviors. These are controlled synthetic simulations, not a sample
of independent stochastic trials.

## Stage 7 detector false-negative analysis

The baseline aggregate is TP=3, FP=0, TN=432, FN=60. Every one of the 60 false
assertions lacking a `CONTRADICTED` detector outcome is included in
[`stage7_false_negative_details.csv`](./data/controlled/evaluation/stage8_broader_20261107/stage7_false_negative_details.csv).
The CSV preserves assertion ID, experiment/task/trace/agent, claim type,
scenario, behavior, detector outcome/rule/reason, evidence references,
verification and association status, relevant trace-event references,
cross-message overlap, and failure reason. Nested evidence/reference fields are
JSON-encoded. Group totals are in
[`stage7_false_negative_groups.csv`](./data/controlled/evaluation/stage8_broader_20261107/stage7_false_negative_groups.csv).

| Grouping | Counts |
|---|---|
| Claim type | CAPABILITY 15; EVIDENCE_SOURCE 15; IDENTITY 15; TOOL_USAGE 15 |
| Agent behavior | CAPABILITY_EXAGGERATION_AGENT 15; EVIDENCE_MISREPRESENTATION_AGENT 15; FALSE_REPORTING_AGENT 15; IDENTITY_MISMATCH_AGENT 15 |
| Scenario | arithmetic 12; authorized_tool 12; local_lookup 12; missing_lookup 12; relay 12 |
| Failure reason | evidence association mismatch 30; irrelevant evidence 15; simulated execution is not actual evidence 15 |

Across these false negatives, referenced evidence that was present had a valid
signature (45 verification-status records were `valid`). Association outcomes
were 15 `scope_mismatch`, 15 `evidence_relationship_mismatch`, and 15
`evidence_not_relevant`; the other 15 had no evidence references and therefore
no verification record. There were 15 false negatives with a relevant trace-event
reference, all associated with simulated execution rather than suitable actual
execution evidence; 45 had no relevant trace-event reference. A cross-message
finding included 30 of the 60 false-negative assertion IDs. The other 30 were
not identified by the supported cross-message patterns.

## Stage 8 Stage 4 detector results

The binary denominator is all 820 assertions; no assertion had inconclusive
ground truth. Aggregate results:

| TP | FP | TN | FN | Precision | Recall | F1 | Accuracy |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 719 | 100 | 1.000000 | 0.009901 | 0.019608 | 0.878049 |

Four-outcome distribution: `CONTRADICTED` 1, `INSUFFICIENT_EVIDENCE` 674,
`SUPPORTED` 20, `UNSUPPORTED` 125. Aggregate evidence verification failures
were zero; association-status counts were 125 scope mismatches, 45 irrelevant
evidence records, and 25 relationship mismatches.

### By claim type

| Claim type | TP | FP | TN | FN | Precision | Recall | F1 | Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CAPABILITY | 0 | 0 | 270 | 25 | N/A | 0 | N/A | 0.915254 |
| EVIDENCE_SOURCE | 0 | 0 | 100 | 25 | N/A | 0 | N/A | 0.800000 |
| IDENTITY | 0 | 0 | 100 | 25 | N/A | 0 | N/A | 0.800000 |
| ROLE | 0 | 0 | 125 | 0 | N/A | N/A | N/A | 1.000000 |
| TASK_COMPLETION | 1 | 0 | 24 | 0 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| TOOL_USAGE | 0 | 0 | 100 | 25 | N/A | 0 | N/A | 0.800000 |

### By agent behavior

| Behavior | TP | FP | TN | FN | Precision | Recall | F1 | Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CAPABILITY_EXAGGERATION_AGENT | 0 | 0 | 155 | 25 | N/A | 0 | N/A | 0.861111 |
| EVIDENCE_MISREPRESENTATION_AGENT | 0 | 0 | 135 | 25 | N/A | 0 | N/A | 0.843750 |
| FALSE_REPORTING_AGENT | 1 | 0 | 134 | 25 | 1.000000 | 0.038462 | 0.074074 | 0.843750 |
| HONEST_AGENT | 0 | 0 | 160 | 0 | N/A | N/A | N/A | 1.000000 |
| IDENTITY_MISMATCH_AGENT | 0 | 0 | 135 | 25 | N/A | 0 | N/A | 0.843750 |

### By scenario

| Scenario | TP | FP | TN | FN | Precision | Recall | F1 | Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| arithmetic | 0 | 0 | 144 | 20 | N/A | 0 | N/A | 0.878049 |
| authorized_tool | 0 | 0 | 144 | 20 | N/A | 0 | N/A | 0.878049 |
| local_lookup | 0 | 0 | 144 | 20 | N/A | 0 | N/A | 0.878049 |
| missing_lookup | 1 | 0 | 143 | 20 | 1.000000 | 0.047619 | 0.090909 | 0.878049 |
| relay | 0 | 0 | 144 | 20 | N/A | 0 | N/A | 0.878049 |

### By seed

| Seed | Assertions | TP | FP | TN | FN | Precision | Recall | F1 | Accuracy |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 810 | 165 | 1 | 0 | 144 | 20 | 1.000000 | 0.047619 | 0.090909 | 0.878788 |
| 811 | 160 | 0 | 0 | 140 | 20 | N/A | 0 | N/A | 0.875000 |
| 812 | 165 | 0 | 0 | 145 | 20 | N/A | 0 | N/A | 0.878788 |
| 813 | 165 | 0 | 0 | 145 | 20 | N/A | 0 | N/A | 0.878788 |
| 814 | 165 | 0 | 0 | 145 | 20 | N/A | 0 | N/A | 0.878788 |

The 160-assertion run is preserved without padding or normalization. Inspection
of its sidecar shows 32 assertions in each scenario, versus 33 in most other
rotations; behavior-to-role reassignment changes the scripted assertion set.
All emitted assertions were still matched to predictions.

Machine-readable exhaustive group and per-seed metrics are in
[`evaluation_summary.json`](./data/controlled/evaluation/stage8_broader_20261107/aggregate/evaluation_summary.json)
and [`assertion_metrics.csv`](./data/controlled/evaluation/stage8_broader_20261107/aggregate/assertion_metrics.csv).

The expanded Stage 8 corpus has 100 detector false negatives and no false
positives. They are distributed evenly across the four false-assertion claim
types (25 each), the four non-honest scripted behaviors (25 each), and the
five scenarios (20 each). Failure grouping is 50 evidence-association
mismatches, 25 irrelevant-evidence cases, and 25 simulated-execution-only
cases. Per-assertion context is in
[`stage8_false_negative_details.csv`](./data/controlled/evaluation/stage8_broader_20261107/stage8_false_negative_details.csv);
the grouped summary is in
[`stage8_false_negative_groups.csv`](./data/controlled/evaluation/stage8_broader_20261107/stage8_false_negative_groups.csv).

## Independent cross-message evaluation

Cross-message findings are evaluated separately from Stage 4 outcomes. A true
positive is an emitted finding that exactly matches a sidecar-eligible
contradiction kind and assertion IDs, and whose experiment, trace, task, agent,
and message references validate against source messages and traces. A false
positive is a structurally valid emitted finding without such an eligible
ground-truth pattern. A false negative is an eligible pattern with no exact
finding. TN is undefined because these artifacts do not define a finite
universe of absent cross-message findings.

| Corpus | Eligible findings | TP | FP | FN | Precision | Recall | F1 | TN |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Stage 7 seeds 701–703 | 30 | 30 | 0 | 0 | 1.000000 | 1.000000 | 1.000000 | Undefined |
| Stage 8 rotations 810–814 | 50 | 50 | 0 | 0 | 1.000000 | 1.000000 | 1.000000 | Undefined |

Both corpora contain only two independently assessable types:
`identity_claim_conflicts_with_sender` (15 Stage 7 / 25 Stage 8) and
`permission_misrepresented_as_demonstration` (15 / 25). Each type had zero FP
and FN in this scripted corpus. Stage 8 cross-message findings reference 75
assertions, of which 50 false assertion IDs overlap the Stage 4 false
negatives. The 25 configured-permission assertions are not themselves false.
The results establish correct matching for these annotated templates only, not
generalization to unseen contradiction types. Detailed results are in
[`stage7_cross_message_evaluation.json`](./data/controlled/evaluation/stage8_broader_20261107/stage7_cross_message_evaluation.json),
[`cross_message_evaluation.json`](./data/controlled/evaluation/stage8_broader_20261107/cross_message_evaluation.json),
and their by-type CSVs.

No combined score is reported: the two components use different evaluation
units and Stage 7 cross-message findings overlap detector false negatives.

## Evidence robustness checks

Four copied Stage 7 source runs were placed in isolated Stage 8 robustness
directories; each retains the runtime inputs, evaluator sidecar, fault
description, detector output, and evaluator output.

| Variant | Verification statuses / failures | Association failures | Detector outcome counts (C / IE / S / U) |
|---|---|---|---|
| One evidence record removed | 104 valid verifications; 1 missing association | missing 1; scope mismatch 24; irrelevant 10; relationship mismatch 5 | 1 / 136 / 4 / 24 |
| One signature tampered | invalid signature 1; valid 104 | scope mismatch 25; irrelevant 10; relationship mismatch 5 | 1 / 135 / 4 / 25 |
| One trusted issuer removed | untrusted issuer 50; valid 55 | untrusted issuer 25; scope mismatch 25; irrelevant 10; relationship mismatch 5 | 1 / 110 / 4 / 50 |
| Clock set to maximum evidence expiry | expired 105 | expired 120; scope mismatch 25 | 1 / 15 / 4 / 145 |

`C / IE / S / U` means CONTRADICTED / INSUFFICIENT_EVIDENCE / SUPPORTED /
UNSUPPORTED. Failure states remain distinct and do not by themselves establish
that a claim is false. Replay protection was directly validated by the existing
Stage 7B repeated-nonce verifier test, but no persisted replay mutation was
created: the preserved artifacts do not include a trusted issuer private key
with which to produce a second valid signed record. See
[`robustness_summary.json`](./data/controlled/evaluation/stage8_broader_20261107/robustness_summary.json)
for exact machine-readable counts.

## Reproducibility and statistical limits

Stage 7 seeds 701–703 preserve scenario order
`arithmetic, local_lookup, authorized_tool, relay, missing_lookup` and the same
behavior order by agent slot. After normalizing seed-derived entity IDs,
message and claim/assertion templates match across seeds; literal task/agent
IDs embedded in message content and scopes differ. The event type/source/
status/tool/sequence trace structure is unchanged, although trace config hashes
differ. Evidence records also differ in deterministic seed-derived
cryptographic values and signatures. Scenario and agent participation order
are fixed. Thus seeds change identifiers and cryptographic material, not
stochastic behavior conditions. The identical Stage 7 metric distributions
are not three independent stochastic trials.

Stage 8 intentionally cyclically rotated behavior assignments across the five
agent slots for seeds 810–814; scenario order stayed fixed. These are controlled
paired variations, not stochastic replications. Rotation changes the
script-generated assertions, including the 160-assertion rotation described
above. No detector rules or thresholds were tuned. No confidence intervals
were estimated: five scripted rotations and three fixed-template seeds do not
support cluster-level population inference. Assertion-level bootstrap
intervals would incorrectly treat within-task observations as independent.
The full comparison is in
[`reproducibility_analysis.json`](./data/controlled/evaluation/stage8_broader_20261107/reproducibility_analysis.json)
and [`reproducibility_comparison.csv`](./data/controlled/evaluation/stage8_broader_20261107/reproducibility_comparison.csv).

## Comparison and interpretation

* **Stage 4 provenance detection:** evaluates individual assertions against
  trace/evidence and returns four outcomes. On the expanded controlled corpus
  it produced one TP and 100 FN, with no FP; its very low recall is the main
  limitation.
* **Stage 7D cross-message analysis:** checks restricted multi-message
  identity and permission-versus-demonstration patterns. It found 30/30
  eligible contradictions in Stage 7 and 50/50 in the rotated corpus, and
  independently referenced 50 false assertions missed by Stage 4 in Stage 8.
  The corpus is small and scripted, so these perfect observed metrics are not
  evidence of broad generalization.
* **Combined contribution:** no combined metric is calculated. Cross-message
  finding counts and assertion-level detector metrics are not interchangeable
  and could double-count the same assertion.

These are assertion-level manipulation-detection results, not evidence that
the system is a successful false-agent classifier.

## Files and validation

Files inspected included:

* `provenance_detector.py`, `dataset/cross_message_analysis.py`,
  `dataset/independent_evaluator.py`,
  `run_claim_bearing_experiment.py`, `run_claim_bearing_detection.py`, and
  `run_claim_bearing_evaluation.py`.
* Stage 7A–7E documentation and tests, including
  `tests/test_stage7d_7e_integration.py`.
* Persisted Stage 7 and Stage 8 source JSONL, detector findings, cross-message
  findings, evaluator summaries, and per-seed metrics.
* TAMAS loader, Stage 3 execution traces, Stage 5 detection outputs, and the
  existing `results/raw.csv` / `results/summary.csv` R0–R8 outputs.
* `app.py` and `tests/test_dashboard.py`.

Implementation changes in this phase: `dataset/stage8_analysis.py` adds
ground-truth-bounded false-negative and cross-message analysis;
`app.py` adds a separate Stage 8 view; `tests/test_stage8_analysis.py` and
`tests/test_dashboard.py` cover the new analysis and dashboard surfaces.
No Stage 4 detector rules, Stage 3 engine, TAMAS labels, or R0–R8 routers were
changed.

Additional deliverables include `STAGE8_EXPERIMENTAL_EVALUATION.md`,
`STAGE8_TAMAS_INTEGRATION_REPORT.md`, JSON/CSV outputs under
`data/controlled/evaluation/stage8_broader_20261107/`, new source and
detection/evaluation runs under `data/controlled/runs/`,
`data/controlled/detection/`, and `data/controlled/evaluation/`, plus the
separate TAMAS reports under `data/tamas/stage8_integration_20261107/`.

Commands executed:

* `.venv/bin/python run_claim_bearing_experiment.py --help`
* `.venv/bin/python run_claim_bearing_detection.py --help`
* `.venv/bin/python run_claim_bearing_evaluation.py --help`
* `.venv/bin/python run_claim_bearing_detection.py --traces data/controlled/runs/stage8_robustness_20261107/missing_evidence/experiment-82daf8290f4216fd.traces.jsonl --messages data/controlled/runs/stage8_robustness_20261107/missing_evidence/experiment-82daf8290f4216fd.messages.jsonl --claims data/controlled/runs/stage8_robustness_20261107/missing_evidence/experiment-82daf8290f4216fd.claims.jsonl --evidence data/controlled/runs/stage8_robustness_20261107/missing_evidence/experiment-82daf8290f4216fd.evidence.jsonl --trust-policy data/controlled/runs/stage8_robustness_20261107/missing_evidence/experiment-82daf8290f4216fd.trust.json --out-dir data/controlled/detection/stage8_robustness_20261107/missing_evidence`
* The same detection command was executed for `invalid_signature`,
  `untrusted_issuer`, and `expired` using their corresponding variant paths;
  the expired run additionally used `--now 1700090414`.
* `.venv/bin/python run_claim_bearing_evaluation.py --detector-findings data/controlled/detection/stage8_robustness_20261107/missing_evidence/experiment-82daf8290f4216fd/detector_findings.jsonl --cross-message-findings data/controlled/detection/stage8_robustness_20261107/missing_evidence/experiment-82daf8290f4216fd/cross_message_findings.jsonl --ground-truth data/controlled/runs/stage8_robustness_20261107/missing_evidence/experiment-82daf8290f4216fd.evaluator.jsonl --out-dir data/controlled/evaluation/stage8_robustness_20261107/missing_evidence`
* The same evaluation command was executed for the other three robustness
  variants with their respective input/output paths.
* The five broader source runs are preserved under
  `data/controlled/runs/stage8_broader_20261107/rotation_0..4/` with seeds
  810–814, all five scenarios, and the behavior-slot orders recorded in
  `reproducibility_comparison.csv`. Those run commands were executed earlier
  in this task; their exact shell lines were not retained in the summarized
  command history, so the saved JSONL/sidecar artifacts are the source of truth
  for their actual inputs and configuration.
* Inline Python invocations read preserved JSONL, run analysis helpers, and
  write the Stage 8 JSON/CSV analysis outputs.
* `.venv/bin/python -m pytest -q tests/test_stage8_analysis.py tests/test_stage7d_7e_integration.py`
* `.venv/bin/python -m pytest -q tests/test_dashboard.py`
* `.venv/bin/python -m pytest -q`
* `.venv/bin/python -m compileall -q dataset tests *.py`
* `git diff --check`

Validation passed: 36 Stage 7/Stage 8 analysis tests, 15 dashboard tests, and
the full suite (206 passed). Python compileall and `git diff --check` passed.
