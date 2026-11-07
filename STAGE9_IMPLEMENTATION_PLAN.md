# Stage 9 Implementation Plan

## Audit findings before implementation

The worktree is already dirty. In particular, `README.md`, `crypto_ledger.py`,
and `requirements.txt` have existing modifications, and Stage 7/8 files and
artifacts are untracked in this workspace. They will be preserved; no reset,
cleanup, overwrite, or commit is planned. New Stage 9 artifacts will use
separate directories.

### Interfaces available

* **Identity and signed runtime evidence:** `dataset/runtime_evidence.py`
  provides `RuntimeEvidenceVerifier`, `TrustPolicy`, `EvidenceCategory`,
  issuer status values, and task/trace/agent-bound verification. The
  Stage 7D runner verifies evidence and associates it with assertions.
* **Assertion findings:** `run_claim_bearing_detection.py` consumes only
  runtime traces, structured messages, claims, evidence and public trust
  policy; it emits one Stage 4 finding per assertion with the four established
  outcomes plus verification/association details.
* **Cross-message findings:** `dataset/cross_message_analysis.py` reports
  association-bounded findings, and Stage 8 has an independent sidecar-based
  evaluator. These are distinct from Stage 4 outcomes.
* **Historical trust:** the Stage 7C engine does not preserve agent identity
  across tasks: IDs and keys are generated from each task ID, and the operator
  is fixed to a task role/slot. A Stage 9-only stable-identity option and
  selector hook are required to accumulate task history without changing
  existing Stage 7 artifacts or default behavior.
* **Capabilities and requirements:** each Stage 7C scenario declares a
  required role/capability, and runtime reports distinguish
  `CONFIGURED_PERMISSION` from `DEMONSTRATED_CAPABILITY`. All five scripted
  agents receive configured permission; only a task's assigned operator
  produces a tool event. All such events are local simulations.
* **Existing R0–R8:** `router.Router` exposes `observe_profiles`,
  `score_all`, `select`, and `update`; it accepts per-agent numeric profiles
  and typed task indices, and learns from outcomes. Its implementation and
  original `results/raw.csv` and `results/summary.csv` are separate from
  Stage 7 claim-bearing runs. The existing algorithms will not be edited.
* **Task execution and evaluation:** Stage 7C has five deterministic local
  scenarios and evaluator-only sidecars. Sidecars include expected assertion
  values, task result and behavior category. They may only be read by the
  independent evaluator after routing/detection outputs are finalized.
* **Dashboard:** root `app.py` already has an additive Stage 8 page and tests.
  Stage 9 will add a separate page reading persisted Stage 9 outputs; no
  existing pages will be replaced.

### Stage 8 evidence to preserve

The inspected aggregate file reports TP=1, FP=0, TN=719, FN=100 across 820
assertions (precision 1.0, recall 0.009901, F1 0.019608, accuracy 0.878049).
The separate cross-message evaluation reports 50 TP, 0 FP, 0 FN with no
defined TN universe. TAMAS has 70 record-level rows but no genuine
claim-bearing agent execution or per-agent truth. These numbers and
limitations remain unchanged and separate from routing metrics.

## Additive implementation

1. Add `dataset/agent_trust_scoring.py` with explicit typed inputs, configurable
   documented weights, outcome-specific counts, evidence coverage and
   verification statuses, identity status, cross-message flags, and
   capability evidence. Use a smoothed assertion reliability component based
   only on SUPPORTED versus CONTRADICTED; UNSUPPORTED and INSUFFICIENT_EVIDENCE
   remain distinct and are not treated as false claims. Blend sparse estimates
   toward a neutral cold-start prior. Do not emit malicious-agent labels.
2. Add `dataset/trust_aware_router.py` to filter by task/capability
   compatibility before scoring, use conservative permission-only fallback
   when no demonstrated capability is known, prefer verified demonstrations
   where applicable, tie-break deterministically, and log all candidates,
   exclusions, score components, coverage, selection, and rationale. Keep
   simulated demonstrations explicitly separate from actual external
   execution. Use only a strict runtime context with no evaluator fields.
3. Extend the Stage 7C generator additively with an optional stable roster,
   selected operator slot, and routing-decision callback/config. Default
   Stage 7C generation must retain its current fixed operator and task-local
   identity behavior. Role assignment changes only in Stage 9-enabled runs.
4. Add a Stage 9 experiment runner that performs each task in order: select
   from public runtime profile/history; generate source artifacts; run Stage 4
   and cross-message detection; update router history from those detector
   outputs; continue to the next task. The independent evaluator runs only
   after every routing decision. Preserve all artifacts in new Stage 9 run,
   detection, routing, and evaluation directories.
5. Compare the untouched legacy `Router` versions R0–R8 through a narrow
   Stage 9 adapter on the same scenario sequence, stable roster, and behavior
   slot assignments. Adapter signal mapping will be explicit: R0 remains
   random; R1 receives a numeric summary of prior self-reported execution
   claims; R2–R8 receive an attested numeric transformation of the prior
   **detector outcome distribution** (`SUPPORTED=1`, `CONTRADICTED=0`,
   `UNSUPPORTED`/`INSUFFICIENT_EVIDENCE=0.5`). The attestation authenticates
   that detector-derived summary, not objective truth. The trust-aware policy
   receives the same runtime evidence/findings and additionally decomposes
   them into its documented components. Results will be described as an
   adapter-based controlled comparison, not a replacement or re-run of the
   original R0–R8 benchmark.
6. Use multiple behavior-slot rotations and seeds. They are paired controlled
   variations, not stochastic trials. No weights or rules will be tuned on
   final evaluation data; default, documented weights will be frozen before
   running the comparison.
7. Add independent routing/task evaluation from persisted routing records and
   evaluator sidecars, aggregate CSV/JSON outputs, and tests for outcome
   separation, cold start, evidence/capability boundaries, stable history,
   leakage, reproducibility, all ten strategies, and dashboard integration.
8. Add separate Stage 9 docs, update README with exact reproduction commands,
   and report TAMAS as unavailable for claim-based routing evaluation without
   inventing histories or claims.

## Validation and acceptance

Run the focused Stage 9 and existing Stage 7/8/dashboard tests, then the full
pytest suite, `.venv/bin/python -m compileall -q .`, `git diff --check`, and a
small end-to-end run covering R0–R8 plus `trust_aware`. Inspect persisted
outputs to verify each strategy has readable routing decisions, detector
artifacts, and independent evaluation. Record mismatches, missing signals, and
the limits of comparability rather than filling gaps with assumed values.

No R0–R8 algorithm, Stage 7/8 artifact, TAMAS label, Stage 3 engine, or
existing dashboard page will be overwritten. No Stage 10 work is in scope.
