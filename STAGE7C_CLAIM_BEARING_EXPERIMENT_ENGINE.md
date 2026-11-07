# Stage 7C: Claim-Bearing Multi-Agent Experiment Engine

Stage 7C adds a deterministic local simulation in
[`dataset/claim_bearing_agents.py`](./dataset/claim_bearing_agents.py) and the
[`run_claim_bearing_experiment.py`](./run_claim_bearing_experiment.py) CLI. It
uses the existing Stage 7A message schemas, Stage 7B identity/evidence APIs,
`crypto_ledger.py` Ed25519 keys, and Stage 3 trace serialization. It does not
modify the TAMAS engine, detector, Stage 5 runner, dashboard, or R0–R8.

## Run a controlled experiment

```bash
.venv/bin/python run_claim_bearing_experiment.py --seed 19
```

By default, this runs every controlled scenario with five agents and the five
behavior types. Select a subset of scenarios and/or behaviors with:

```bash
.venv/bin/python run_claim_bearing_experiment.py \
  --seed 19 \
  --scenarios arithmetic relay \
  --behaviours HONEST_AGENT FALSE_REPORTING_AGENT IDENTITY_MISMATCH_AGENT \
  --agents-per-task 5 \
  --output-dir data/controlled/runs/demo
```

The runner accepts only the project controlled runs directory or a child
directory. A repeated deterministic experiment refuses to overwrite output
unless `--overwrite` is explicitly supplied. No LLM API, external service,
shell command, financial action, or original TAMAS record is used.

## Agent behaviors

The simulation exposes five controlled behaviors:

- `HONEST_AGENT` reports the runtime-observed identity, role, permissions,
  tool status, result, and completion state.
- `FALSE_REPORTING_AGENT` claims recorded tool execution even though the
  trace distinguishes local simulation from actual external execution. It does
  not alter the runtime event or operation.
- `IDENTITY_MISMATCH_AGENT` asserts another task-local identity while the
  message sender and signed registry binding remain the registered identity.
- `CAPABILITY_EXAGGERATION_AGENT` claims an unexercised capability was
  demonstrated while citing its separate configured-permission evidence.
- `EVIDENCE_MISREPRESENTATION_AGENT` cites a real permission record but claims
  it came from an unrelated trace event.

These are scripted experimental conditions, not claims about real agents.
Assignments are deterministic and become part of the experiment configuration.
Behavior categories are written only to the evaluator sidecar.

## Controlled task scenarios

All tasks use deterministic in-memory operations and fixed inputs:

| Scenario | Operation | Outcome |
|---|---|---|
| `arithmetic` | Add two small integers | Completed |
| `local_lookup` | Read a record from a fixed local fixture | Completed |
| `authorized_tool` | Normalize fixed text locally | Completed |
| `relay` | Add values and hand the observed result to another agent | Completed |
| `missing_lookup` | Look up an absent fixture key | Correctly reports no result; task fails |

Each task has a task/experiment/trace ID, description, required operator role
and capability, observable inputs, fixed procedure, outcome, and a multi-agent
roster. The task input and actual tool result appear in the runtime trace.
Scenario procedure and ground-truth outcomes are retained in evaluator-only
records.

## Messages and assertions

The runner generates all Stage 7A message types: `TASK_ASSIGNMENT`,
`STATUS_UPDATE`, `CAPABILITY_REPORT`, `TOOL_REPORT`, `TASK_COMPLETION`,
`HANDOFF`, and `EVIDENCE_REFERENCE`. Assertions use all six supported claim
types. IDs and task/trace/message/agent associations are validated, messages
are bound to actual `AGENT_MESSAGE`/`HANDOFF` trace events, and every evidence
reference must resolve. The generator does not detect contradictions or
translate these messages into Stage 5 detector inputs.

Configured permission and demonstrated capability remain separate assertion
states. Every agent receives configured permission for the scenario operation
and a separate unexercised local capability. Only observed local execution is
reported as a demonstration by an honest agent.

## Evidence and output separation

Identity and role evidence is issued by the identity registry, permission
records by the runtime authority, and simulated invocation records by the local
tool gateway. An experiment-evaluator authority signs task-completion and
demonstrated-capability attestations **only inside the evaluator sidecar**.
The simulation does not provide sidecar data to the scripted agent behaviors.
Detector-facing evidence contains no evaluator truth, behavior labels, or
TAMAS labels.

Output files are written separately:

- `<experiment_id>.traces.jsonl`
- `<experiment_id>.messages.jsonl`
- `<experiment_id>.claims.jsonl`
- `<experiment_id>.evidence.jsonl`
- `<experiment_id>.trust.json` — public issuer keys and authorized categories
- `<experiment_id>.evaluator.jsonl` — evaluator-only truth and signed
  evaluator attestations

Private signing keys are held only in memory and are not serialized. Every tool
invocation is marked `LOCAL_SIMULATION` and every response
`SIMULATED_RESPONSE`; none represents an external action. A valid Ed25519
signature establishes integrity and signer authenticity under the configured
policy, not objective truth.

The Stage 5 detector does not load these outputs automatically. In particular,
the `.evaluator.jsonl` sidecar is never read by the simulation or detector-facing
output writer. Keep it in a separate evaluator workflow.

## Limitations

- The agents are deterministic scripts, not LLM-backed or autonomous agents.
- The tool operations are local fixtures and transformations, not the TAMAS
  Finance tools or any real external operation.
- The experiment is controlled synthetic data, not a TAMAS benchmark and does
  not estimate detection accuracy, precision, or recall.
- The evaluator sidecar contains expected assertion values and behavior
  categories for later evaluation; it must remain isolated from detector
  inputs.
- Cross-message contradiction detection, independent evaluation, Stage 7D,
  routing changes, and detector integration are out of scope.
