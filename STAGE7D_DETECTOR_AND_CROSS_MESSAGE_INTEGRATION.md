# Stage 7D: Detector and Cross-Message Integration

Stage 7D connects Stage 7C runtime artifacts to the existing Stage 4
[`ProvenanceDetector`](./provenance_detector.py) without modifying that detector
or loading evaluator-only sidecars. It adds
[`run_claim_bearing_detection.py`](./run_claim_bearing_detection.py) and
[`dataset/cross_message_analysis.py`](./dataset/cross_message_analysis.py).

## Run

Generate Stage 7C source artifacts first:

```bash
.venv/bin/python run_claim_bearing_experiment.py \
  --seed 401 \
  --output-dir data/controlled/runs/demo
```

Then pass the corresponding five detector-facing input files:

```bash
.venv/bin/python run_claim_bearing_detection.py \
  --traces data/controlled/runs/demo/EXPERIMENT.traces.jsonl \
  --messages data/controlled/runs/demo/EXPERIMENT.messages.jsonl \
  --claims data/controlled/runs/demo/EXPERIMENT.claims.jsonl \
  --evidence data/controlled/runs/demo/EXPERIMENT.evidence.jsonl \
  --trust-policy data/controlled/runs/demo/EXPERIMENT.trust.json
```

The deterministic default clock is one second after the newest signed evidence
creation time, consistent with the Stage 7C simulated timeline. Supply `--now`
to test another clock value, including expiry. Output is written to
`data/controlled/detection/EXPERIMENT/`:

- `detector_findings.jsonl` — one Stage 4 outcome per input assertion.
- `cross_message_findings.jsonl` — separate assertion-level cross-message results.
- `detection_summary.json` — counts and evidence-verification failures.

The runner refuses duplicate IDs, malformed schemas, unknown traces, experiment,
task, agent, message, or claim associations, and disagreement between flat
claims and their structured message assertions. A missing referenced evidence
record remains visible as `MISSING` and produces insufficient evidence rather
than disappearing. Signature, trust, freshness, replay, scope, and evidence
relevance results are recorded per reference. The summary keeps cryptographic
and trust verification statuses separate from assertion-to-evidence association
statuses, including scope, identity, missing-reference, relevance, and
evidence-relationship failures.

## Stage 7B evidence adaptation

All Stage 7B evidence records are parsed and verified with
`RuntimeEvidenceVerifier` using the input trust policy. Expired, replayed,
invalid-signature, untrusted, scope-mismatched, and otherwise failed evidence
is retained in finding metadata. A failed record is adapted to Stage 4's
unsupported evidence state; a missing record is mapped to insufficient
evidence. Valid Stage 7B tool execution references can be passed as a
Stage 4 trace observation only after their source trace event resolves.
Evidence with a valid signature but no Stage 4-compatible support semantics is
not promoted into a legacy signed envelope.

Stage 4 `SignedEvidenceEnvelope` and Stage 7B `RuntimeEvidence` sign different
canonical payloads. Treating one as the other would invalidate the security
boundary. Stage 7D therefore verifies Stage 7B signatures first and lets Stage
4 make its normal outcome decision using its existing trace/evidence input
contract. It never uses ground truth to change an outcome. The existing outcome
categories are preserved:

- `SUPPORTED`
- `UNSUPPORTED`
- `CONTRADICTED`
- `INSUFFICIENT_EVIDENCE`

Stage 4's tool-use and completion rules remain authoritative. In particular,
simulated invocations are not treated as actual external tool execution. Some
valid Stage 7B claims remain insufficient to Stage 4 because the detector does
not adjudicate every Stage 7A claim type from those evidence formats.

## Cross-message analysis

`analyze_cross_message_assertions` compares assertion pairs only when they have
the same runtime-associated agent, task, trace, exact scope and subject, with
compatible message timestamps. When timestamps are absent, order is taken from
the validated per-trace sequence numbers. It does not merge identities across
agents or compare unrelated task/trace/scope data.

It reports:

- Conflicting completion assertions only when the later status lacks matching
  recorded terminal task evidence.
- Opposing tool-use assertions by one agent when no matching invocation by that
  agent appears in the trace.
- A demonstrated-capability claim that reuses configured-permission context
  without a matching invocation by that agent.
- An identity assertion that differs from the runtime-associated sender and is
  repeated inconsistently in another message.

Every result references the assertion IDs and both messages (or the assertion
plus the corroborating sender message for identity findings). These findings
are not merged with Stage 4 findings, are not agent-level malice labels, and do
not rewrite the Stage 4 outcome.

## Security boundary and limitations

Detection reads only traces, messages, claims, runtime evidence, and public
trust policy. Neither the runner nor cross-message module imports the
independent evaluator or accepts a sidecar argument. The evaluator-only file
is read later by the separate Stage 7E CLI. Stage 7C tools are deterministic
local simulations, and simulator issuer signatures authenticate configured
issuers/bytes rather than objective truth. The local run is not an external
execution benchmark or a TAMAS evaluation.
