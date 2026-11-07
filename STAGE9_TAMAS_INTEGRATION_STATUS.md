# Stage 9 TAMAS Integration Status

## Result

The existing TAMAS Finance sample remains a record-level data integration, not
a trust-aware agent-routing evaluation. The canonical Stage 9 machine-readable
status is
[`data/controlled/stage9_routing_20261107_final_v3/tamas_integration_status.json`](./data/controlled/stage9_routing_20261107_final_v3/tamas_integration_status.json).

| Item | Observed value |
|---|---:|
| TAMAS Finance records | 70 |
| Benign records | 20 |
| Adversarial records | 50 |
| Existing local simulation traces | 4 |
| Existing simulated tool invocations | 14 |
| Available claims | 0 |
| Detector findings | 0 |
| Records with a verified per-record insufficiency count | unavailable (`null`) |
| Genuine claim-bearing agent executions | No |
| Trust-aware TAMAS routing performed | No |
| Per-agent accuracy | unavailable (`null`) |
| Status | `INSUFFICIENT_EVIDENCE_NO_CLAIMS_AVAILABLE` |

Available record-level attack categories in the sample are:

| Category | Records |
|---|---:|
| colluding_agents | 10 |
| contradicting_agents | 10 |
| direct_prompt_injection | 10 |
| impersonation | 10 |
| indirect_prompt_injection | 10 |
| benign | 20 |

The input includes 50 records with `agents_involved`, 40 agent specifications
with a tools field out of 290 specifications, 33 tool definitions, 20 records
with a sequence of tool calls, and 10 records with an expected output. These
are dataset annotations and task-level fields, not independent proof of
agent behavior. Stage 9 does not pass attack labels, expected outputs,
filenames, or `agents_involved` into its router.

## What was exercised

The Stage 9 runner reads the previously generated Stage 8 TAMAS summary as
status metadata; it does not rerun or transform the TAMAS records into synthetic
claims. Existing Stage 3 traces are deterministic local scheduling/no-op
simulations, with no genuine claim-bearing agent conversation. Stage 5/Stage 4
claim detection therefore has no TAMAS claims to evaluate. There are no
per-agent trust histories or validated task-to-agent truth labels to initialize
or score trust-aware routing.

No trust-aware TAMAS route was run, no claims were manufactured from traces,
and no per-agent accuracy was computed. `records_with_insufficient_evidence`
is `null`, rather than zero, because record-level insufficiency was not
assessed as an assertion metric. The appropriate status is
`INSUFFICIENT_EVIDENCE_NO_CLAIMS_AVAILABLE`.

## Boundary and reproduction

TAMAS labels remain record/task-level. `agents_involved` is not treated as an
agent-dishonesty label. This report must not be merged with Stage 9 controlled
synthetic metrics.

To reproduce the Stage 9 controlled output, see
[`STAGE9_ROUTING_EVALUATION.md`](./STAGE9_ROUTING_EVALUATION.md). To run the
existing TAMAS detector pipeline without claims, use the Stage 5 command in
README; it emits `NO_CLAIMS_AVAILABLE` and does not fabricate detector results.
