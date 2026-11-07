# Stage 4: Provenance-Paradox Detector

## Research-source inspection and assumptions

The detector is a project-defined experimental module, not a reproduction of
an original Provenance Paradox paper. No paper PDF, TeX source, or original
paper implementation was found in the repository. The README contains an
arXiv citation, and `ldp_layer/contracts.py` contains claim/provenance
contracts, but neither establishes a formal rule set for the detector added
here. No attack mechanism below is attributed to the cited paper.

The implementation was designed against these existing project surfaces:

* `dataset/experiment_schemas.py`: detector-safe task input, traces, claim and
  evidence references, identity binding, and the evaluator-only claim
  assessment.
* `dataset/execution_engine.py` and `dataset/safe_tools.py`: deterministic
  local scheduling and inert simulated tool adapters. They do not generate
  agent messages or execute upstream tools.
* `dataset/trace_store.py`: validated JSONL trace serialization and loading.
* `crypto_ledger.py`: an Ed25519 simulator-observation ledger with a different
  payload and purpose; it was not reused as a claim-evidence format.
* `ldp_layer/contracts.py` and the existing synthetic agents/tests: related
  contract and simulation code, not verified TAMAS interaction data or a
  provenance-claim detector.
* `docs/TAMAS_STAGE2_EXECUTION_DESIGN.md`: dataset field boundaries, label
  meaning, and the separation between runtime information and evaluator data.

Key assumptions made for this implementation:

1. A claim is an atomic assertion with one exact asserted value. The
   detector-safe projection is used; evaluator assessments are not accepted.
2. Trusted issuers are explicitly configured with Ed25519 public keys. A
   signed evidence envelope attests that the configured issuer signed the
   bound bytes. The issuer policy is an experimental trust assumption.
3. Signed evidence timestamps are Unix seconds and `now` is supplied by the
   caller, so tests and runs can be deterministic.
4. An absence-based tool-use contradiction requires a structurally valid trace
   whose final event is a system-generated `simulation_ended`, or a recorded
   system-generated `task_completed` / `task_failed` event. This is a
   trace-closure convention, not proof that instrumentation captured every
   real-world event.
5. Evidence binding to a task-local agent ID does not establish a persistent
   real-world identity. It only checks agreement among the claim, signed
   envelope, trusted issuer policy, and the runtime task's ID.

## Architecture and detector boundary

`provenance_detector.py` is independent of R0–R8 and does not change routing.
`ProvenanceDetector.detect` accepts only:

* a `DetectorTaskInput` runtime projection;
* an `InteractionTrace`;
* `DetectorProvenanceClaim` runtime projections;
* optional assertion-specific `SignedEvidenceEnvelope` values;
* a caller-supplied timestamp and, when replay tracking must persist across
  calls, a caller-maintained set of used evidence IDs.

Passing the evaluator-bearing `ProvenanceClaim` directly raises `TypeError`.
Call `.detector_view()` to remove its `ClaimAssessment` before detection.
The detector does not accept or import `TAMASRecord`, TAMAS attack labels,
source filenames, `agents_involved`, expected outputs, reference tool-call
sequences, or evaluator sidecars. Its JSONL integration uses
`read_traces_jsonl` on the trace file only.

Role and capability declarations in `DetectorTaskInput` are unverified claims,
not facts. The detector makes identity, role, and capability findings from the
atomic assertions carried by `DetectorProvenanceClaim`; if those assertions
are omitted, it derives them from `agent_id`, `claimed_role`, and
`claimed_capabilities`. Tool-use and task-completion assertions must be
explicit.

Each `ClaimFinding` contains `provenance_claim_id` for the parent claim and
`assertion_id` for the atomic assertion, along with agent ID, claim type, exact
asserted value, outcome, evidence references examined, a human-readable
reason, and the triggering rule. The legacy `claim_id` field is retained as a
deprecated alias for `assertion_id`; new consumers should use the two explicit
ID fields. There are no confidence scores.
`DetectionReport` also lists agents with no structured claim input and missing
information. For a trace with no claims, it returns no fabricated findings,
an overall `INSUFFICIENT_EVIDENCE`, and the IDs of all task agents as
unassessed.

`detect_jsonl` requires exactly one trace with the requested ID. It raises a
clear error for zero or multiple matches and never opens an evaluator sidecar.

## Claim types

`ClaimSubjectType` supports:

| Type | Meaning and evidence limit |
|---|---|
| `identity` | Assertion about the task-local agent identity; requires configured issuer evidence to be supported. |
| `role` | Claimed role; a role string alone or task description is not verification. |
| `capability` | Claimed capability; a tool declaration or tool invocation alone does not establish general competence. |
| `tool_usage` | Claim that a named tool was invoked; requires a corresponding recorded event, or assertion-specific trusted evidence. |
| `task_completion` | Claim that the task completed; requires a recorded system completion event or trusted bound attestation. A successful tool call alone is not completion. |
| `provenance` | Other assertion-specific origin/provenance statement; requires corresponding trusted evidence. |

`ClaimAssertion.supporting_evidence_ids` binds references to an atomic claim.
`ClaimEvidence.integrity_verified` is not treated as proof and is ignored as a
trust decision input.

## Signed evidence and verification order

`SignedEvidenceEnvelope` uses Ed25519 and signs canonical JSON fields:
evidence ID, assertion ID, agent ID, evidence type, claim type, verified value,
issuer, issue time, and optional expiry. A reference in the claim must match
the envelope ID, issuer, and evidence type. Every evidence reference associated with an assertion is examined in
lexicographic evidence-ID order, independent of the order supplied by the
caller. Findings retain a sorted tuple of examined evidence IDs and trace
event references. For each signed item, the detector checks that the
envelope exists; the evidence reference, assertion ID, claim type, and agent
ID agree; the claim identity binding is acceptable; the issuer is configured;
the signature verifies; and the timestamp passes future, expiry, and maximum
age checks. It then checks replay state before evaluating the attested value.

### Evidence aggregation and precedence

The detector aggregates all evidence results rather than returning after the
first item. The deterministic per-assertion precedence is:

1. `CONTRADICTED` if any valid, fresh, trusted, assertion-bound attestation
   asserts a different value, or a relevant complete-trace observation
   contradicts the claim. A matching attestation cannot mask a contradictory
   attestation; if trusted attestations conflict, the four-outcome model
   resolves to `CONTRADICTED`.
2. `SUPPORTED` if at least one valid matching trusted attestation or
   directly-relevant recorded trace event supports the assertion and no
   contradiction was found. Invalid extra references do not hide a valid
   contradiction; they also do not override valid support when there is no
   contradiction.
3. `UNSUPPORTED` if there is no valid support or contradiction and at least
   one offered evidence item is invalid, untrusted, unbound, stale, replayed,
   or merely self-asserted.
4. `INSUFFICIENT_EVIDENCE` if there is no valid support or contradiction and
   evidence is absent, unavailable, or not relevant to the assertion.

Among multiple reasons at the selected outcome level, the rule and
human-readable reason are selected deterministically by a fixed rule priority
and sorted reason text. A valid contradictory attestation is therefore never
hidden by an invalid item or by evidence ordering.

### Replay-state consumption

An evidence ID is consumed only after its reference and envelope binding,
agent identity binding, configured issuer, signature, and freshness have all
validated. Valid matching and valid contradictory attestations are both
consumed. Invalid signatures, untrusted issuers, mismatched identities or
assertions, stale evidence, and missing envelopes do not consume their IDs.
Evidence IDs already in `seen_evidence_ids` or already consumed earlier in
the current report are reported as replayed and unsupported. A valid ID may
be used only once per report.

Replay tracking across detector calls exists only when the caller provides
and reuses the same mutable `seen_evidence_ids` set. A call without that set
uses local state; it does not claim global replay protection. Protection
across processes requires durable external state, which this module does not
provide.

A valid signature establishes integrity and signer authenticity under the
configured key policy. It does **not** independently establish objective
truth. The value is only classified `SUPPORTED` because a specifically
configured trusted issuer attested to that value under the experiment's trust
assumption. A trusted fresh attestation for a different value is
`CONTRADICTED`.

The existing `crypto_ledger.py` signs simulator outcomes with its own
`(agent, task, outcome, round, nonce)` structure. Stage 4 does not conflate
those receipts with assertion-specific evidence.

## Rules and outcome definitions

| Outcome | Meaning |
|---|---|
| `SUPPORTED` | A qualifying trusted, fresh, correctly bound attestation matches the value, or a suitable recorded runtime event directly matches the claim. This is scoped to the evidence and policy; it is not universal proof of truth. |
| `UNSUPPORTED` | Evidence was offered but is self-asserted, untrusted, invalidly signed, mismatched, stale/expired, replayed, or lacks required identity binding. This is not a finding of dishonesty. |
| `CONTRADICTED` | A trusted, fresh, correctly bound attestation asserts a different value; a cited event contradicts the asserted agent/tool; a complete trace records the claimed tool only for another agent or no such invocation; or a system task-failed event opposes a completion claim. |
| `INSUFFICIENT_EVIDENCE` | Evidence is absent or unavailable, the trace does not establish the assertion, the invocation is simulated rather than actual, or completion was not evaluated. This is not proof of dishonesty. |

Additional behavior checks:

* A recorded tool invocation supports only that the trace records an invocation;
  it does not establish the tool succeeded or the agent had general capability.
* Stage 3 `LOCAL_SIMULATION` invocations are not actual tool use and therefore
  produce `INSUFFICIENT_EVIDENCE` for real-use claims.
* A runtime event can only assess a `tool_usage` assertion after the event
  trace ID and sequence are validated and its type and tool name match. An
  unrelated event never contradicts an identity, role, or capability claim.
  Only `RECORDED` invocations count as actual observations. A `LOCAL_SIMULATION`
  or `SIMULATED_RESPONSE` event cannot support or contradict an actual-use
  claim. A recorded invocation attributed to another agent contradicts a
  tool-use claim only when the trace is complete and no matching recorded
  invocation by the claimed agent is present.
* Tool-use absence is contradictory only when the trace passes schema
  revalidation and its final event is a valid system terminal marker:
  `simulation_ended`, or a recorded `task_completed` / `task_failed`. Without
  that marker, absence is `INSUFFICIENT_EVIDENCE`. A positive recorded
  invocation may still support a claim in a non-terminal trace.
* Task completion uses only system-generated `task_completed` and
  `task_failed` events with `RECORDED` status. The latest such event by
  sequence controls the result: completion supports, failure contradicts.
  `SIMULATION_ENDED` alone, non-system status events, and traces with no valid
  task-status event remain `INSUFFICIENT_EVIDENCE`.
* The supplied trace is the only observed behavior source. No behavior is
  inferred from TAMAS attack categories, agent descriptions, or tool names.

For a report containing multiple assertions, the aggregate outcome is
`CONTRADICTED` if any assertion is contradicted; otherwise `UNSUPPORTED` if
any assertion is unsupported; otherwise `INSUFFICIENT_EVIDENCE` if any
assertion is insufficient; otherwise `SUPPORTED`. Per-assertion findings
remain authoritative.

## Synthetic detector scenarios and tests

`tests/provenance_detector_fixtures.py` defines controlled synthetic detector
fixtures. They are not TAMAS records, LLM messages, genuine execution traces,
or benchmark annotations. `tests/test_provenance_detector.py` covers:

1. Honest claim with valid, fresh, identity-bound issuer evidence.
2. Unsupported capability claim backed only by self-assertion.
3. Claimed tool use absent from a complete trace.
4. Task-completion claim without a completion event.
5. Evidence bound to another agent.
6. Modified/invalid signature.
7. Expired and replayed evidence.
8. Claim with no evidence.
9. A tool invocation observed under a different agent identity.

Further tests cover identity binding, untrusted issuers and trusted-key
mismatch, trusted evidence contradicting the claim, order-independent evidence
aggregation, invalid evidence not consuming replay state, same-report and
cross-call replay behavior, unavailable evidence envelopes, invalid claim and
trace structures, evaluator-assessment exclusion, deterministic output, and
Stage 3 trace JSONL integration.

## Stage 3 trace integration

The Stage 3 engine generates no LLM messages or provenance claims. The
detector reads its JSONL output without inventing claims. The test executes
one actual benign and one actual adversarial Finance record through the
existing deterministic safe engine and checks both trace reports:

* no claim findings are emitted;
* all task agents remain unassessed;
* the report is `INSUFFICIENT_EVIDENCE`.

The test harness uses original record metadata only to select the two
validation records and create `DetectorTaskInput`. Labels and source records
are not passed to the detector.

## TAMAS labels versus project-level claim predictions

The available 70 TAMAS records have filename-derived record/task categories.
They do not provide agent-level identity, capability, role-truth, provenance,
or claim-truth labels. A record-level `direct_prompt_injection` or other
attack category is not a provenance-claim prediction and cannot establish
which agent was dishonest. This module does not read those labels, derive
agent labels from them, calculate benchmark accuracy/precision/recall/F1, or
change the original records.

The synthetic findings test rule behavior only. They are not benchmark
results. The current Stage 3 traces are deterministic local simulations, not
real LLM execution or real financial tool use.

## Limitations and requirements before benchmark evaluation

* The detector has no actual agent-authored claims in the current TAMAS traces.
* Task-local identity aliases are not authenticated real-world identities.
* Trusted issuer keys and issuer truthfulness are configured assumptions.
* Signed evidence can be accurate, mistaken, or deceptive despite valid
  integrity/authenticity checks.
* Tool invocation evidence is not evidence of tool success, answer quality,
  safety, or general capability.
* Trace completeness is a caller/instrumentation assumption signaled by a
  final system terminal marker. A structurally valid, closed trace may still
  omit real events; absence-based contradictions are limited to that trace.
* Replay protection across calls depends on shared caller-managed state;
  protection across processes needs durable external storage.
* A runtime event reference may identify a trace event, but does not itself
  establish event-stream completeness. The detector only makes an
  absence-based contradiction under the terminal-marker convention above.
* `ClaimFinding.claim_id` remains as a deprecated assertion-ID alias for
  compatibility. Consumers should distinguish `provenance_claim_id` from
  `assertion_id`.
* The detector is deterministic rule logic; it has no calibration, learned
  model, confidence scores, or benchmark performance estimate.
* No original Provenance Paradox paper or implementation was found in this
  repository, so the module is not represented as a reproduction.

Before any benchmark evaluation, collect and adjudicate structured agent
claims, runtime identity bindings, issuer provenance, evidence freshness and
replay state, complete observed traces, and claim-level truth annotations
using an independent protocol. Keep these annotations evaluator-only; do not
pass them to the detector. Evaluate claim predictions separately from
filename-derived TAMAS record categories, and report unknown outcomes and
annotation limitations rather than converting them to truth labels.
