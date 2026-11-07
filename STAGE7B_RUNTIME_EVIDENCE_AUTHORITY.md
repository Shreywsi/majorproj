# Stage 7B: Runtime Evidence Authority

Stage 7B adds typed signed evidence in
[`dataset/runtime_evidence.py`](./dataset/runtime_evidence.py), using the
existing Ed25519 keys and verification primitives in
[`crypto_ledger.py`](./crypto_ledger.py). The original ledger record format,
R2 router behavior, detector, dashboard, and TAMAS records are unchanged.

## Evidence contract and authorities

`RuntimeEvidence` is a signed, JSON-serializable envelope containing an evidence
ID, issuer, category, agent and experiment IDs, subject/predicate/value, scope,
creation and expiry timestamps, nonce, signature, and optional trace, task,
message, assertion, event-sequence, and source references. Signing uses
canonical UTF-8 JSON with sorted keys and compact separators. The detached
signature covers every field except the signature itself.

Issuers have one of four explicit authority identities:

| Authority | Categories |
|---|---|
| `IDENTITY_REGISTRY` | Identity and role |
| `RUNTIME_EXECUTION` | Configured permission and evidence source |
| `TOOL_EXECUTION` | Tool execution and evidence source |
| `EXPERIMENT_EVALUATOR` | Demonstrated capability and task completion |

Issuers can be narrowed to a subset of their categories. `TrustPolicy` contains
only allow-listed public keys and categories; its public representation never
contains a private key. There is deliberately no agent/self-attestation
authority kind. A valid signature authenticates the configured signer and
protects signed bytes from modification. It does not prove that the signer
observed events correctly or that a signed assertion is objectively true.

## Agent key registration

`AgentIdentityRegistry` issues one-time challenges. An agent must prove
possession of its proposed Ed25519 public key by signing a purpose-bound
challenge. The registry then emits signed `IDENTITY` evidence and binds that
public key to the task-local agent ID. A challenge is consumed on the
registration attempt. The registry stores and publishes public keys only.

The challenge proves possession of a key, not that a human or external
organization controls it. The registry/issuer key must be provisioned and
protected by the application operator; issuer objects must remain in trusted
runtime components and must not be passed to agents.

## Verification and tool execution

`RuntimeEvidenceVerifier` returns explicit statuses for missing records,
untrusted issuers, unauthorized categories, invalid signatures, identity or
scope mismatches, unregistered identities, future timestamps, expiry, staleness,
replay, and simulated execution where actual tool execution was required.
Evidence is added to the replay set only after all applicable checks succeed.
Callers may supply a persistent `seen_evidence_ids` set; the verifier's own
replay memory is process-local and is not durable across restarts.

Tool execution evidence must include the existing trace execution status:

- `LOCAL_SIMULATION` and `SIMULATED_RESPONSE` explicitly identify the current
  inert local tool stubs.
- `NOT_EXECUTED` does not assert execution.
- `RECORDED` can satisfy `require_actual_execution`; only trusted gateway code
  should issue such evidence after it observes actual execution.

The `RECORDED` label by itself does not prove an external action occurred.
Deployments must bind a tool issuer to a real, independently observed execution
gateway before treating such evidence as proof of external execution. No
external tools are called by this implementation.

`validate_message_evidence_links` checks that referenced evidence resolves and
agrees with the message's agent and any supplied trace/task/message/assertion
references. This check is separate from cryptographic verification; callers
must run both. `verify_reference` reports an absent record as `MISSING`.

## Security assumptions and limitations

- Trust rests on the local issuer allow-list, secure private-key provisioning,
  accurate authority implementation, and correct wall-clock time.
- Keys are in-memory Python objects; this MVP does not use a hardware security
  module, encrypted key store, key rotation, revocation, or durable replay
  database. Private keys must never be serialized into reports or sent to the
  dashboard.
- Challenge registration only proves key possession. It does not establish a
  legal or human identity.
- Issuer code is trusted to issue evidence only after the appropriate
  observation. The cryptographic layer cannot prevent a trusted issuer from
  signing a false statement.
- Freshness is checked with caller-provided integer timestamps and an optional
  maximum age; expiry is mandatory. Production systems need a documented clock
  and retention policy.
- Signed evidence is not fed into the Stage 4 detector by this stage. No
  evaluator labels, TAMAS labels, or evaluator-only sidecars are loaded here.
- This work does not implement claim-bearing agents, contradiction detection,
  evaluation, external tool calls, or trust-aware routing.
