# Stage 7A: Claim-Bearing Message Schemas

Stage 7A adds a validated structured message contract in
[`dataset/claim_message_schemas.py`](./dataset/claim_message_schemas.py). It is a
data layer only: it does not create agent messages, parse prose into claims, run
agents, detect contradictions, or change the Stage 4 detector.

## Message envelope

`ClaimBearingMessage` records:

- Stable message, experiment, trace, task, agent, and optional subtask IDs.
- The runtime-associated `agent_id`, separately from `claimed_identity` and
  `claimed_role`. Conflicting identity assertions can therefore be represented
  without changing the message's recorded sender.
- One supported message type: `TASK_ASSIGNMENT`, `STATUS_UPDATE`,
  `CAPABILITY_REPORT`, `TOOL_REPORT`, `TASK_COMPLETION`, `HANDOFF`, or
  `EVIDENCE_REFERENCE`.
- JSON content, zero or more structured assertions and evidence IDs.
- Optional `handoff_to`, trace event `sequence_number`, and Unix timestamp.

Identifiers are restricted to 1–128 ASCII letters, digits, dots, underscores,
colons, or hyphens, beginning with a letter or digit. Batch validation checks
experiment/trace/task association, registered task-local agents, optional
subtask/evidence references, unique message/assertion IDs, unique sequence
numbers, and handoff recipients. `validate_messages_against_trace` additionally
checks the sequence, actor, recipient, and event type against the existing
trace. In this mapping, a structured handoff corresponds to a recorded
`HANDOFF` event; other message types correspond to `AGENT_MESSAGE`.

Assertions carry their own assertion/provenance IDs, agent, claim type, subject,
predicate, JSON asserted value, scope, trace/message references, and evidence
references. Assertions must match their containing message's agent and trace.
Evidence referenced by an assertion must also be declared by the containing
message; callers may provide the known evidence-ID set to reject missing
records.

## Capability semantics

Capability assertions require an explicit `capability_state`:

- `CONFIGURED_PERMISSION` means the runtime configuration authorizes an
  operation.
- `DEMONSTRATED_CAPABILITY` means a capability has been demonstrated through
  observable evidence.

These are separate enum values. Configured permission is not evidence that an
agent can perform an operation. The schema does not decide whether either claim
is true.

## Serialization and limitations

`claim_message_to_json` and `claim_message_from_json` provide strict JSON
round-tripping and reject unknown or missing fields, invalid enums, malformed
identifiers, and non-JSON values. `validate_claim_messages` accepts differing
values for the same subject and predicate: contradictions are retained as
claims, not detected or adjudicated.

These schemas do not yet adapt messages into Stage 5 detector inputs, derive
claims from message text, validate that a message was authored by its stated
agent, or establish objective truth. Those require a runtime transport and
separate evidence validation. Existing TAMAS record labels and evaluator
sidecars are not part of this message contract.
