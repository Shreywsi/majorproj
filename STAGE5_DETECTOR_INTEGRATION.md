# Stage 5: Provenance Detector Integration

## What this pipeline does

`run_tamas_detection.py` connects existing Stage 3 JSONL traces to the Stage 4
`ProvenanceDetector`. It loads traces with the existing trace validator,
requires claims to be explicitly supplied, calls the existing detector, and
writes one JSON summary with per-trace and per-assertion findings.

The runner does not create claims from tool invocations, handoffs, schedules,
simulated responses, TAMAS labels, expected outputs, reference tool-call
sequences, or evaluator annotations. The available Stage 3 traces do not
contain provenance claims.

When `--claims` is omitted, points to a file that does not exist, or names an
empty JSONL file, the report states `NO_CLAIMS_AVAILABLE`, with counts for
traces loaded and zero claims, assertions, findings, and outcome findings. A
requested-but-missing file adds an explicit warning. This is not a successful
detector score and does not imply that there are zero dishonest agents.

## Claims input JSONL format

Supply one JSON object per trace. `trace_id` identifies exactly one trace in
the trace file. `task_input` is the detector-safe `DetectorTaskInput`
projection needed for the detector to validate agent IDs; it contains only
runtime task text and agent declarations. Every entry in `claims` is an
explicit `ProvenanceClaim` without an `assessment`, plus optional signed
evidence envelopes.

```json
{
  "trace_id": "trace-...",
  "task_input": {
    "task_id": "task-...",
    "scenario": "finance",
    "user_query": "Original runtime query",
    "agents": [{
      "agent_id": "agent-000",
      "claimed_role": "Analyst",
      "claimed_description": "Runtime role description",
      "declared_tools": []
    }]
  },
  "claims": [{
    "provenance_claim": {
      "claim_id": "claim-001",
      "agent_id": "agent-000",
      "identity_binding": "unverified",
      "claimed_role": "Analyst",
      "claimed_capabilities": ["read_market_data"],
      "supporting_evidence": [],
      "observed_behavior": [],
      "assertions": [{
        "assertion_id": "assertion-001",
        "claim_type": "tool_usage",
        "asserted_value": "analyze_market_sentiment",
        "supporting_evidence_ids": []
      }]
    },
    "signed_evidence": []
  }]
}
```

The outer object, task projection, claims, evidence references, observed
behavior, assertions, and signed envelopes are schema-validated. Unknown
fields are rejected. Evaluator-only fields such as `assessment`,
`ground_truth_label`, `tamas_record_label`, `source_file`, `agents_involved`,
`expected_output`, and `sequence_of_tool_calls` are rejected rather than
ignored. A claims file must not contain private signing keys. If signed
evidence is used, configure the corresponding public keys via a separate trust
policy JSON mapping issuer IDs to base64-encoded Ed25519 public keys. With no
trust policy, no issuer is trusted.

Claims are associated by trace ID, and the task ID in `task_input` must equal
the trace's task ID. A malformed trace or claims line, duplicate trace ID, or
claim referencing an unknown trace fails the run with an explicit error. The
runner never opens or infers data from Stage 3's `.evaluator.jsonl` sidecar.

## Output report

The JSON report includes:

* experiment ID (deterministically derived from the exact input bytes, trust
  policy bytes, and supplied `now`, unless explicitly named);
* status, execution modes, and processed trace IDs;
* number of traces loaded, claims supplied, assertions evaluated, and findings;
* counts for `SUPPORTED`, `UNSUPPORTED`, `CONTRADICTED`, and
  `INSUFFICIENT_EVIDENCE`;
* per-trace claim and assertion counts, per-assertion findings, evidence IDs
  examined, detector rule/reason, trace completeness convention, and tool
  invocation status counts;
* warnings and research limitations.

Tool invocation events are counted separately by recorded status. A Stage 3
`local_simulation` invocation is never described as an actual external action.
For the controlled Stage 3 execution mode, `external_actions_executed` is 0.
The runner shares a replay-ID set across trace detections in one report. This
does not persist across separate pipeline runs; durable cross-run replay state
is outside this stage.
The report does not call agents honest or malicious and does not include raw
evaluator data or private keys.

Trace closure is reported as `CLOSED_BY_TERMINAL_MARKER` when the final event
follows the detector's system terminal-marker convention. This is not proof
that all real-world events were captured; a validly closed but incomplete
instrumentation stream remains a limitation. Absence of a claim is reported
as no finding, not as `INSUFFICIENT_EVIDENCE` findings for invented claims.

## Reproducible commands

### 1. Generate a fresh Stage 3 validation pair

Use a new output path because the Stage 3 runner appends traces:

```bash
.venv/bin/python run_tamas_execution.py \
  --mode round_robin --seed 19 \
  --out data/tamas/runs/stage5_validation.jsonl
```

This runs the existing safe local simulator on one actual benign and one
actual adversarial Finance record. It creates no LLM messages and performs no
external financial actions. Its evaluator mapping is written to a separate
`.evaluator.jsonl` file; the detector pipeline does not open that file.

### 2. Run with no claims

```bash
.venv/bin/python run_tamas_detection.py \
  --traces data/tamas/runs/stage5_validation.jsonl \
  --out data/tamas/detection/stage5_no_claims.json
```

Expected status: `NO_CLAIMS_AVAILABLE`; two traces loaded, zero claims,
assertions, or findings.

### 3. Run with an explicitly supplied synthetic pipeline-test claim

The repository includes
`tests/fixtures/stage5_synthetic_claims.jsonl`. This is a clearly identified
synthetic integration input, not a genuine TAMAS claim, agent message,
benchmark annotation, or ground-truth label. Its fixed trace ID corresponds
to the seed-19 validation pair above.

```bash
.venv/bin/python run_tamas_detection.py \
  --traces data/tamas/runs/stage5_validation.jsonl \
  --claims tests/fixtures/stage5_synthetic_claims.jsonl \
  --now 0 \
  --out data/tamas/detection/stage5_synthetic_test.json
```

This demonstrates claim-to-trace association and schema/output behavior only.
The supplied synthetic tool-use claim refers to a Stage 3 simulated invocation
and therefore cannot be treated as evidence of actual tool execution.

### 4. Inspect the JSON report

```bash
.venv/bin/python -c 'import json; p=json.load(open("data/tamas/detection/stage5_no_claims.json")); print(json.dumps({k:p[k] for k in ("status","traces_loaded","claims_supplied","assertions_evaluated","findings_count","outcome_counts")}, indent=2))'
```

The report is machine-readable and contains no accuracy, precision, recall,
or F1 calculation.

## Tests and limitations

`tests/test_tamas_detection_integration.py` covers valid traces, no/empty/
missing claims, malformed files, explicit claims, association, no invented
findings, evaluator-field rejection and detector-boundary inspection,
simulated-versus-external action reporting, deterministic output, and failure
on malformed traces.

This is pipeline validation, not TAMAS benchmark evaluation. The source data
contains no agent-level claim-truth labels; record-level attack categories
are not agent labels. Current Stage 3 traces contain only deterministic
scheduling and simulated tool responses, not LLM conversations or real tool
execution. A valid signature authenticates signed bytes and issuer under a
configured key policy; it does not prove objective truth. The pipeline does
not modify TAMAS records or R0–R8 and does not train a model.
