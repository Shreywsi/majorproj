# Stage 7D + 7E Implementation Audit and Experimental Validation

## Scope and evidence standard

This audit covers Stage 7A–7E implementation, the documented detector/evaluator
boundary, the requested Stage 7D correctness conditions, and deterministic
synthetic experiments. Statements below are marked as verified from source,
tests, or generated outputs; they are not claims about external agents or
external tool execution.

The worktree was already dirty before this audit, with unrelated modified and
untracked files. Those were preserved. No Stage 8 or Stage 9 work was started.
The Streamlit dashboard, TAMAS dataset, Stage 3 engine, Stage 4 detector, and
R0–R8 routers were not modified.

## Files inspected

- Stage 7D/E entry points and logic: `run_claim_bearing_detection.py`,
  `dataset/cross_message_analysis.py`, `dataset/independent_evaluator.py`,
  `run_claim_bearing_evaluation.py`.
- Stage 7A–7C implementation: `dataset/claim_message_schemas.py`,
  `dataset/runtime_evidence.py`, `dataset/claim_bearing_agents.py`,
  `run_claim_bearing_experiment.py`.
- Existing outcome logic: `provenance_detector.py`.
- Documentation: `STAGE7A_CLAIM_MESSAGE_SCHEMAS.md`,
  `STAGE7B_RUNTIME_EVIDENCE_AUTHORITY.md`,
  `STAGE7C_CLAIM_BEARING_EXPERIMENT_ENGINE.md`,
  `STAGE7D_DETECTOR_AND_CROSS_MESSAGE_INTEGRATION.md`,
  `STAGE7E_INDEPENDENT_EVALUATION.md`.
- Tests: `tests/test_stage7_claim_messages.py`,
  `tests/test_stage7_runtime_evidence.py`,
  `tests/test_stage7c_claim_bearing_experiment.py`,
  `tests/test_stage7d_7e_integration.py`.
- Existing generated Stage 7 run/detection/evaluation files were inventoried;
  three new source runs and their first-pass and final detection/evaluation
  outputs were preserved under the audit-specific directories listed below.

## Files changed

- `dataset/cross_message_analysis.py` — identity corroboration now observes the
  same timestamp compatibility window as other cross-message comparisons.
- `dataset/independent_evaluator.py` — cross-message assertion and message
  references now require matching known experiment/trace/task/agent
  associations. Evaluator summaries separately count per-reference evidence
  association failures.
- `run_claim_bearing_detection.py` — detection summaries now report
  assertion-to-evidence association status counts and failures separately from
  signed-record verification.
- `tests/test_stage7d_7e_integration.py` — regression tests for time-window
  enforcement, mismatched/unknown cross-message associations, and separate
  evidence-association counts; missing references are checked in the summary.
- `STAGE7D_DETECTOR_AND_CROSS_MESSAGE_INTEGRATION.md` and
  `STAGE7E_INDEPENDENT_EVALUATION.md` — documentation updated to describe the
  separate association-failure counts.
- `STAGE7_VALIDATION_REPORT.md` — this report.

The implementation files were untracked in the initial worktree; the existing
unrelated modifications to `README.md`, `crypto_ledger.py`, and
`requirements.txt` were not changed by this audit.

## Correctness and isolation findings

### Evaluator isolation — verified at the implementation boundary

- The detection CLI accepts traces, messages, claims, evidence, and a public
  trust policy; it has no evaluator-sidecar argument and does not import the
  evaluator module.
- Detector input parsing requires a fixed claim schema. It does not accept
  `expected_asserted_value`, `matches_expected`, `ground_truth_label`, or
  behavior-label fields. Findings are built from runtime assertions/evidence
  and trace events.
- Stage 7C writes evaluator-only outcomes and behavior categories through a
  separate sidecar writer. The detector-facing trust policy excludes the
  experiment-evaluator issuer.
- An integration test serializes detector results and verifies that expected
  values, behavior labels, and ground-truth fields are absent.

This verifies the separation implemented by the CLI and data flow, not an
operating-system sandbox: the Python process is not filesystem-jailed. A caller
could still manually copy sidecar data into a detector-facing input file;
strict schemas and trust validation reject many such substitutions, but the
process itself has normal filesystem permissions.

### Evidence outcomes and four detector outcomes — verified

- `RuntimeEvidenceVerifier` has distinct statuses for invalid signatures,
  untrusted issuers, unauthorized categories, expiry, replay, identity/scope
  mismatch, and missing evidence. Tests exercise those conditions; detection
  integration also exercises invalid signature, expiry, replayed nonce, and
  absent referenced evidence.
- Missing references remain visible. In detection integration, a missing
  reference produces `INSUFFICIENT_EVIDENCE` and a `missing` association status.
- Failed signature/trust/freshness checks adapt to unsupported evidence rather
  than to a contradictory attestation. A separate complete trace may still
  independently contradict a claim; verification failure alone is not treated
  as proof that the claim is false.
- Stage 4 distinguishes a self-claim or failed attestation (`UNSUPPORTED`),
  an explicit conflicting trusted/trace fact (`CONTRADICTED`), and absence or
  inadequate observation (`INSUFFICIENT_EVIDENCE`). The synthetic false
  simulated-tool assertions are not labeled `CONTRADICTED`.
- Binary evaluation treats only `CONTRADICTED` as a positive false-claim
  prediction. False assertions assigned any other outcome are false negatives.
  `UNSUPPORTED` is not conflated with `CONTRADICTED`.

The three unmodified generated experiments contain only valid signed evidence,
so their verification-failure counts are zero. The failure statuses above were
verified through tests that exercise altered/expired/untrusted/replayed or
missing evidence rather than by altering the preserved experiment artifacts.

### Cross-message and evaluator validation — verified, with fixes

- Cross-message comparisons are grouped by runtime agent, task, trace, and
  exact scope, and require matching experiment and subject values. Tool-use
  findings are suppressed by a matching same-agent invocation. The detector
  runner validates message sequence, event actor/type, and task/trace
  association before invoking cross-message analysis.
- Identity findings require a differing identity assertion and corroboration
  from another message of that same runtime-associated agent. The audit fixed
  the missing time-window check on that corroborating message.
- The evaluator rejects duplicate ground-truth/prediction assertion IDs,
  unknown prediction IDs, missing predictions, mismatched prediction
  associations, and now mismatched or unknown cross-message assertion/message
  associations. Regression tests cover the added cases.
- Generated cross-message findings were kept separate from assertion metrics
  and did not produce agent-level malicious labels.

### Metrics and multi-seed handling — verified

- Inconclusive ground truth is included in outcome-comparison reporting but
  excluded from TP/FP/TN/FN denominators. A focused test verifies this behavior.
- Agent summaries contain only false-assertion counts/proportions, not
  agent-level malicious classifications.
- Aggregate evaluation retains per-seed metric groups; it does not replace
  them with a single pooled result.
- Evidence association failures (scope mismatch, irrelevant evidence,
  relationship mismatch, and missing references) are now summarized separately
  from cryptographic/trust verification failures.

## Experiments

Three deterministic runs used the default full scenario set and the five
Stage 7C scripted behaviors. Each run contained 5 tasks, 25 agent
participations, 130 messages, 165 assertions, 105 signed evidence records, and
5 simulated tool invocations. Each behavior occurred five times per seed:
`HONEST_AGENT`, `FALSE_REPORTING_AGENT`, `IDENTITY_MISMATCH_AGENT`,
`CAPABILITY_EXAGGERATION_AGENT`, and `EVIDENCE_MISREPRESENTATION_AGENT`.

Source traces, messages, claims, evidence, public trust files, and evaluator
sidecars are under:

- `data/controlled/runs/stage7d_7e_audit_20261107/seed_701/`
- `data/controlled/runs/stage7d_7e_audit_20261107/seed_702/`
- `data/controlled/runs/stage7d_7e_audit_20261107/seed_703/`

Final detector outputs are under
`data/controlled/detection/stage7d_7e_audit_20261107_final/`, and per-seed plus
aggregate evaluator outputs are under
`data/controlled/evaluation/stage7d_7e_audit_20261107_final/`. Initial
detection/evaluation outputs from before the association-count addition remain
in the corresponding `.../stage7d_7e_audit_20261107/` directories; no existing
run artifacts were overwritten.

### Per-seed binary metrics

Precision, recall, F1, and accuracy are rounded to six decimal places below.

| Seed | TP | FP | TN | FN | Precision | Recall | F1 | Accuracy |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 701 | 1 | 0 | 144 | 20 | 1.000000 | 0.047619 | 0.090909 | 0.878788 |
| 702 | 1 | 0 | 144 | 20 | 1.000000 | 0.047619 | 0.090909 | 0.878788 |
| 703 | 1 | 0 | 144 | 20 | 1.000000 | 0.047619 | 0.090909 | 0.878788 |

Each seed had 21 false and 144 truthful assertions, with no inconclusive
assertions; its binary denominator was 165.

### Aggregate binary metrics

| TP | FP | TN | FN | Precision | Recall | F1 | Accuracy |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 0 | 432 | 60 | 1.000000 | 0.047619 | 0.090909 | 0.878788 |

The aggregate contains 495 assertions: 63 false, 432 truthful, and zero
inconclusive; the binary denominator is 495. The aggregate output retains
separate seed groups for 701, 702, and 703.

### Detector outcome distribution

| Seed(s) | SUPPORTED | UNSUPPORTED | CONTRADICTED | INSUFFICIENT_EVIDENCE |
|---|---:|---:|---:|---:|
| Each of 701, 702, 703 | 4 | 25 | 1 | 135 |
| Aggregate | 12 | 75 | 3 | 405 |

### Evidence verification and association counts

| Scope | Cryptographic/trust verification failures | Association failures |
|---|---:|---|
| Each seed | 0 (105/105 records `valid`) | `scope_mismatch`: 25; `evidence_not_relevant`: 10; `evidence_relationship_mismatch`: 5 |
| Aggregate | 0 (315/315 records `valid`) | `scope_mismatch`: 75; `evidence_not_relevant`: 30; `evidence_relationship_mismatch`: 15 |

No missing references occurred in the generated runs. Missing evidence is
covered by a focused test and reported separately as an association status.
Invalid signature, expiry, untrusted issuer, replay, and missing-reference
statuses are distinct in verification/integration tests; the synthetic
per-seed counts above are actual run outputs, not fault-injection estimates.

### Cross-message results

Each seed produced 10 findings: five
`identity_claim_conflicts_with_sender` and five
`permission_misrepresented_as_demonstration`. Across seeds this is 30 findings
and 45 assertion references. No tool-use or completion conflict findings were
reported in these runs. This records findings in controlled messages/traces;
it does not establish real-world intent or agent malice.

## Validation results

- Focused Stage 7A–7E tests after the final code changes:
  `69 passed`.
- Full test suite after the final code changes:
  `197 passed`.
- Python compileall over `dataset`, `tests`, and top-level Python modules:
  passed.
- `git diff --check`: passed.
- No test failures remained.

## Commands executed

Focused and full validation:

```bash
.venv/bin/python -m pytest -q tests/test_stage7_claim_messages.py tests/test_stage7_runtime_evidence.py tests/test_stage7c_claim_bearing_experiment.py tests/test_stage7d_7e_integration.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q dataset tests *.py
git diff --check
```

Stage 7C generation (one command per seed):

```bash
.venv/bin/python run_claim_bearing_experiment.py --seed 701 --output-dir data/controlled/runs/stage7d_7e_audit_20261107/seed_701
.venv/bin/python run_claim_bearing_experiment.py --seed 702 --output-dir data/controlled/runs/stage7d_7e_audit_20261107/seed_702
.venv/bin/python run_claim_bearing_experiment.py --seed 703 --output-dir data/controlled/runs/stage7d_7e_audit_20261107/seed_703
```

Stage 7D detection was run once per seed for the initial outputs and once per
seed again after the association-count change. The following are the exact
final commands; the initial commands used the same inputs and substituted
`stage7d_7e_audit_20261107` for
`stage7d_7e_audit_20261107_final` in `--out-dir`.

```bash
.venv/bin/python run_claim_bearing_detection.py --traces data/controlled/runs/stage7d_7e_audit_20261107/seed_701/experiment-82daf8290f4216fd.traces.jsonl --messages data/controlled/runs/stage7d_7e_audit_20261107/seed_701/experiment-82daf8290f4216fd.messages.jsonl --claims data/controlled/runs/stage7d_7e_audit_20261107/seed_701/experiment-82daf8290f4216fd.claims.jsonl --evidence data/controlled/runs/stage7d_7e_audit_20261107/seed_701/experiment-82daf8290f4216fd.evidence.jsonl --trust-policy data/controlled/runs/stage7d_7e_audit_20261107/seed_701/experiment-82daf8290f4216fd.trust.json --out-dir data/controlled/detection/stage7d_7e_audit_20261107_final/seed_701
.venv/bin/python run_claim_bearing_detection.py --traces data/controlled/runs/stage7d_7e_audit_20261107/seed_702/experiment-3acd57e3ed8a5eba.traces.jsonl --messages data/controlled/runs/stage7d_7e_audit_20261107/seed_702/experiment-3acd57e3ed8a5eba.messages.jsonl --claims data/controlled/runs/stage7d_7e_audit_20261107/seed_702/experiment-3acd57e3ed8a5eba.claims.jsonl --evidence data/controlled/runs/stage7d_7e_audit_20261107/seed_702/experiment-3acd57e3ed8a5eba.evidence.jsonl --trust-policy data/controlled/runs/stage7d_7e_audit_20261107/seed_702/experiment-3acd57e3ed8a5eba.trust.json --out-dir data/controlled/detection/stage7d_7e_audit_20261107_final/seed_702
.venv/bin/python run_claim_bearing_detection.py --traces data/controlled/runs/stage7d_7e_audit_20261107/seed_703/experiment-2e8c9f4da6818dca.traces.jsonl --messages data/controlled/runs/stage7d_7e_audit_20261107/seed_703/experiment-2e8c9f4da6818dca.messages.jsonl --claims data/controlled/runs/stage7d_7e_audit_20261107/seed_703/experiment-2e8c9f4da6818dca.claims.jsonl --evidence data/controlled/runs/stage7d_7e_audit_20261107/seed_703/experiment-2e8c9f4da6818dca.evidence.jsonl --trust-policy data/controlled/runs/stage7d_7e_audit_20261107/seed_703/experiment-2e8c9f4da6818dca.trust.json --out-dir data/controlled/detection/stage7d_7e_audit_20261107_final/seed_703
```

Stage 7E was run per seed and once with all three seed files for aggregation:

```bash
.venv/bin/python run_claim_bearing_evaluation.py --detector-findings data/controlled/detection/stage7d_7e_audit_20261107_final/seed_701/experiment-82daf8290f4216fd/detector_findings.jsonl --cross-message-findings data/controlled/detection/stage7d_7e_audit_20261107_final/seed_701/experiment-82daf8290f4216fd/cross_message_findings.jsonl --ground-truth data/controlled/runs/stage7d_7e_audit_20261107/seed_701/experiment-82daf8290f4216fd.evaluator.jsonl --out-dir data/controlled/evaluation/stage7d_7e_audit_20261107_final/seed_701
.venv/bin/python run_claim_bearing_evaluation.py --detector-findings data/controlled/detection/stage7d_7e_audit_20261107_final/seed_702/experiment-3acd57e3ed8a5eba/detector_findings.jsonl --cross-message-findings data/controlled/detection/stage7d_7e_audit_20261107_final/seed_702/experiment-3acd57e3ed8a5eba/cross_message_findings.jsonl --ground-truth data/controlled/runs/stage7d_7e_audit_20261107/seed_702/experiment-3acd57e3ed8a5eba.evaluator.jsonl --out-dir data/controlled/evaluation/stage7d_7e_audit_20261107_final/seed_702
.venv/bin/python run_claim_bearing_evaluation.py --detector-findings data/controlled/detection/stage7d_7e_audit_20261107_final/seed_703/experiment-2e8c9f4da6818dca/detector_findings.jsonl --cross-message-findings data/controlled/detection/stage7d_7e_audit_20261107_final/seed_703/experiment-2e8c9f4da6818dca/cross_message_findings.jsonl --ground-truth data/controlled/runs/stage7d_7e_audit_20261107/seed_703/experiment-2e8c9f4da6818dca.evaluator.jsonl --out-dir data/controlled/evaluation/stage7d_7e_audit_20261107_final/seed_703
.venv/bin/python run_claim_bearing_evaluation.py --detector-findings data/controlled/detection/stage7d_7e_audit_20261107_final/seed_701/experiment-82daf8290f4216fd/detector_findings.jsonl data/controlled/detection/stage7d_7e_audit_20261107_final/seed_702/experiment-3acd57e3ed8a5eba/detector_findings.jsonl data/controlled/detection/stage7d_7e_audit_20261107_final/seed_703/experiment-2e8c9f4da6818dca/detector_findings.jsonl --cross-message-findings data/controlled/detection/stage7d_7e_audit_20261107_final/seed_701/experiment-82daf8290f4216fd/cross_message_findings.jsonl data/controlled/detection/stage7d_7e_audit_20261107_final/seed_702/experiment-3acd57e3ed8a5eba/cross_message_findings.jsonl data/controlled/detection/stage7d_7e_audit_20261107_final/seed_703/experiment-2e8c9f4da6818dca/cross_message_findings.jsonl --ground-truth data/controlled/runs/stage7d_7e_audit_20261107/seed_701/experiment-82daf8290f4216fd.evaluator.jsonl data/controlled/runs/stage7d_7e_audit_20261107/seed_702/experiment-3acd57e3ed8a5eba.evaluator.jsonl data/controlled/runs/stage7d_7e_audit_20261107/seed_703/experiment-2e8c9f4da6818dca.evaluator.jsonl --out-dir data/controlled/evaluation/stage7d_7e_audit_20261107_final/aggregate
```

## Limitations and unresolved defects

- These are deterministic local simulations; no external tool was executed.
- The detector returned very low recall (0.047619) and 60 false negatives in
  the aggregate. This is an observed limitation, not corrected by changing
  thresholds or labels.
- No generated assertion had inconclusive ground truth, so exclusion is
  verified by evaluator tests rather than these seed metrics.
- The per-seed evidence association failures are deliberate/observable claim
  mismatches and are reported separately; they do not represent invalid
  cryptographic signatures.
- Runtime evidence issuer keys and replay state remain in-memory, as documented
  by Stage 7B. This audit did not add persistent replay storage, a production
  clock policy, or operating-system isolation.
- No other correctness defect remained in the requested Stage 7D/E checks after
  the association, timestamp-window, and reporting fixes.
