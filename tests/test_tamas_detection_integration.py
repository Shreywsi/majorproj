import json
import base64
from dataclasses import asdict, replace
from enum import StrEnum

import pytest

from dataset.download_tamas import DATASET_ROOT
from dataset.execution_engine import SafeTAMASExecutionEngine
from dataset.experiment_schemas import (
    ClaimAssertion,
    ClaimEvidenceType,
    ClaimSubjectType,
    DetectorProvenanceClaim,
    DetectorTaskInput,
    IdentityBinding,
)
from dataset.tamas_loader import TAMASAttack, load_tamas_dataset
from dataset.trace_store import write_trace_jsonl
from dataset.workflow_preview import InteractionMode
from provenance_detector import ClaimOutcome, SignedEvidenceEnvelope, evidence_reference
from tests.provenance_detector_fixtures import ISSUER, SIGNING_KEY
from run_tamas_detection import ClaimsInputError, build_detection_report


def _json_default(value):
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value).__name__)


def _write_claims(path, trace, task, claim, signed_evidence=()):
    record = {
        "trace_id": trace.trace_id,
        "task_input": asdict(task),
        "claims": [{
            "provenance_claim": asdict(claim),
            "signed_evidence": [asdict(item) for item in signed_evidence],
        }],
    }
    path.write_text(
        json.dumps(record, default=_json_default, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def stage3_trace(tmp_path):
    dataset = load_tamas_dataset(DATASET_ROOT)
    record = next(item for item in dataset.records if item.attack_type == TAMASAttack.BENIGN)
    task = DetectorTaskInput.from_tamas_record(record, "stage5-opaque-task")
    summary = SafeTAMASExecutionEngine(
        dataset.tool_definitions, InteractionMode.ROUND_ROBIN
    ).run(task, seed=19)
    trace_path = tmp_path / "stage3.jsonl"
    write_trace_jsonl(summary.trace, trace_path)
    return summary.trace, task, trace_path


def _explicit_claim(task):
    agent = task.agents[0]
    return DetectorProvenanceClaim(
        claim_id="synthetic-stage5-claim",
        agent_id=agent.agent_id,
        identity_binding=IdentityBinding.UNVERIFIED,
        claimed_role=agent.claimed_role,
        claimed_capabilities=("synthetic capability fixture",),
        supporting_evidence=(),
        observed_behavior=(),
        assertions=(
            ClaimAssertion(
                assertion_id="synthetic-stage5-assertion",
                claim_type=ClaimSubjectType.ROLE,
                asserted_value=agent.claimed_role,
            ),
        ),
    )


def test_valid_stage3_trace_loads_successfully(stage3_trace):
    trace, _, path = stage3_trace

    report = build_detection_report(path)

    assert report["traces_loaded"] == 1
    assert report["trace_ids_processed"] == [trace.trace_id]
    assert report["trace_reports"][0]["execution_mode"] == "controlled_local_simulation"


def test_no_claims_argument_produces_required_no_claims_summary(stage3_trace):
    _, _, path = stage3_trace

    report = build_detection_report(path)

    assert report["status"] == "NO_CLAIMS_AVAILABLE"
    assert report["traces_loaded"] == 1
    assert report["claims_supplied"] == 0
    assert report["assertions_evaluated"] == 0
    assert report["findings_count"] == 0
    assert not any(report["outcome_counts"].values())


def test_empty_claims_file_produces_no_claims_available(stage3_trace, tmp_path):
    _, _, trace_path = stage3_trace
    claims_path = tmp_path / "empty-claims.jsonl"
    claims_path.write_text("", encoding="utf-8")

    report = build_detection_report(trace_path, claims_path)

    assert report["status"] == "NO_CLAIMS_AVAILABLE"
    assert report["claims_supplied"] == 0
    assert report["assertions_evaluated"] == 0
    assert report["findings_count"] == 0


def test_missing_claims_file_argument_or_path_reports_no_claims(stage3_trace, tmp_path):
    _, _, trace_path = stage3_trace

    report = build_detection_report(trace_path, claims_path=None)
    assert report["status"] == "NO_CLAIMS_AVAILABLE"

    missing_file_report = build_detection_report(
        trace_path, tmp_path / "not-present.jsonl"
    )
    assert missing_file_report["status"] == "NO_CLAIMS_AVAILABLE"
    assert missing_file_report["claims_supplied"] == 0
    assert any("claims file was not found" in warning
               for warning in missing_file_report["warnings"])


@pytest.mark.parametrize(
    "contents",
    [
        '{"trace_id": "unterminated"',
        json.dumps({
            "trace_id": "x",
            "task_input": {
                "task_id": "x",
                "scenario": "finance",
                "user_query": "test",
                "agents": [],
            },
            "claims": [],
        }),
    ],
)
def test_malformed_claims_file_has_clear_validation_error(stage3_trace, tmp_path, contents):
    _, _, trace_path = stage3_trace
    claims_path = tmp_path / "malformed-claims.jsonl"
    claims_path.write_text(contents + "\n", encoding="utf-8")

    with pytest.raises(ClaimsInputError, match="Invalid claims JSONL at line 1"):
        build_detection_report(trace_path, claims_path)


def test_evaluator_fields_in_claims_input_are_rejected(stage3_trace, tmp_path):
    trace, task, trace_path = stage3_trace
    claims_path = tmp_path / "leaky-claims.jsonl"
    record = {
        "trace_id": trace.trace_id,
        "task_input": asdict(task),
        "claims": [],
        "tamas_record_label": "synthetic forbidden label",
        "source_file": "forbidden.json",
    }
    claims_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ClaimsInputError, match="evaluator-only"):
        build_detection_report(trace_path, claims_path)


def test_valid_explicit_claim_reaches_detector_and_is_associated_with_trace(stage3_trace, tmp_path):
    trace, task, trace_path = stage3_trace
    claims_path = tmp_path / "claims.jsonl"
    claim = _explicit_claim(task)
    _write_claims(claims_path, trace, task, claim)

    report = build_detection_report(trace_path, claims_path, now=0)

    assert report["status"] == "COMPLETED"
    assert report["claims_supplied"] == 1
    assert report["assertions_evaluated"] == 3
    findings = report["trace_reports"][0]["findings"]
    explicit = next(
        item for item in findings
        if item["assertion_id"] == "synthetic-stage5-assertion"
    )
    assert explicit["provenance_claim_id"] == claim.claim_id
    assert explicit["predicted_outcome"] == ClaimOutcome.INSUFFICIENT_EVIDENCE.value
    assert report["trace_reports"][0]["trace_id"] == trace.trace_id


def test_trace_without_claim_entry_does_not_invent_findings(stage3_trace):
    trace, _, trace_path = stage3_trace

    report = build_detection_report(trace_path)
    row = next(item for item in report["trace_reports"] if item["trace_id"] == trace.trace_id)

    assert row["findings"] == []
    assert row["claims_supplied"] == 0
    assert row["assertions_evaluated"] == 0
    assert report["findings_count"] == 0


def test_detector_receives_only_runtime_projection_and_no_evaluator_fields(
    stage3_trace, tmp_path, monkeypatch
):
    trace, task, trace_path = stage3_trace
    claims_path = tmp_path / "claims.jsonl"
    _write_claims(claims_path, trace, task, _explicit_claim(task))
    captured = {}
    from provenance_detector import ProvenanceDetector

    original_detect = ProvenanceDetector.detect

    def capture(self, task_input, interaction_trace, claims=(), **kwargs):
        captured["task"] = asdict(task_input)
        captured["trace"] = asdict(interaction_trace)
        captured["claims"] = [asdict(item) for item in claims]
        captured["kwargs"] = kwargs
        return original_detect(self, task_input, interaction_trace, claims, **kwargs)

    monkeypatch.setattr(ProvenanceDetector, "detect", capture)
    build_detection_report(trace_path, claims_path, now=0)
    detector_payload = json.dumps(captured, default=_json_default)

    for forbidden in (
        "ground_truth_label",
        "tamas_record_label",
        "source_file",
        "agents_involved",
        "expected_output",
        "sequence_of_tool_calls",
        "assessment",
    ):
        assert forbidden not in detector_payload


def test_simulated_invocations_are_not_reported_as_actual_external_execution(stage3_trace):
    _, _, trace_path = stage3_trace

    report = build_detection_report(trace_path)
    metadata = report["trace_reports"][0]

    assert metadata["simulated_tool_invocations"] > 0
    assert metadata["external_actions_executed"] == 0
    assert metadata["recorded_tool_invocations"] == 0
    assert metadata["execution_classification"] == "controlled_local_simulation_only"


def test_repeated_runs_have_identical_reports(stage3_trace, tmp_path):
    trace, task, trace_path = stage3_trace
    claims_path = tmp_path / "claims.jsonl"
    _write_claims(claims_path, trace, task, _explicit_claim(task))

    first = build_detection_report(trace_path, claims_path, now=0)
    second = build_detection_report(trace_path, claims_path, now=0)

    assert first == second


def test_replay_state_is_shared_across_traces_in_one_report(stage3_trace, tmp_path):
    trace, task, trace_path = stage3_trace
    second_trace = replace(trace, trace_id=f"{trace.trace_id}-second")
    write_trace_jsonl(second_trace, trace_path)
    assertion = ClaimAssertion(
        assertion_id="shared-evidence-assertion",
        claim_type=ClaimSubjectType.ROLE,
        asserted_value=task.agents[0].claimed_role,
        supporting_evidence_ids=("stage5-shared-evidence",),
    )
    envelope = SignedEvidenceEnvelope.sign(
        signing_key=SIGNING_KEY,
        evidence_id="stage5-shared-evidence",
        assertion_id=assertion.assertion_id,
        agent_id=task.agents[0].agent_id,
        evidence_type=ClaimEvidenceType.ISSUER_ATTESTED,
        claim_type=assertion.claim_type,
        verified_value=assertion.asserted_value,
        issuer=ISSUER,
        issued_at=10,
        expires_at=500,
    )
    claim = DetectorProvenanceClaim(
        claim_id="shared-evidence-claim",
        agent_id=task.agents[0].agent_id,
        identity_binding=IdentityBinding.CRYPTOGRAPHICALLY_BOUND,
        claimed_role=task.agents[0].claimed_role,
        claimed_capabilities=("fixture capability",),
        supporting_evidence=(evidence_reference(envelope),),
        observed_behavior=(),
        assertions=(assertion,),
    )
    claims_path = tmp_path / "multi-trace-claims.jsonl"
    records = []
    for associated_trace in (trace, second_trace):
        record_path = tmp_path / f"{associated_trace.trace_id}.jsonl"
        _write_claims(
            record_path,
            associated_trace,
            task,
            claim,
            signed_evidence=(envelope,),
        )
        records.append(json.loads(record_path.read_text(encoding="utf-8")))
    claims_path.write_text(
        "".join(json.dumps(record, default=_json_default) + "\n" for record in records),
        encoding="utf-8",
    )
    trust_path = tmp_path / "trust.json"
    trust_path.write_text(
        json.dumps({ISSUER: base64.b64encode(bytes(SIGNING_KEY.verify_key)).decode()}),
        encoding="utf-8",
    )

    report = build_detection_report(
        trace_path, claims_path, trust_policy_path=trust_path, now=100
    )
    first = next(
        row for row in report["trace_reports"] if row["trace_id"] == trace.trace_id
    )
    second = next(
        row for row in report["trace_reports"] if row["trace_id"] == second_trace.trace_id
    )
    first_finding = next(
        item for item in first["findings"]
        if item["assertion_id"] == assertion.assertion_id
    )
    second_finding = next(
        item for item in second["findings"]
        if item["assertion_id"] == assertion.assertion_id
    )

    assert first_finding["predicted_outcome"] == ClaimOutcome.SUPPORTED.value
    assert second_finding["predicted_outcome"] == ClaimOutcome.UNSUPPORTED.value
    assert second_finding["rule"] == "evidence_replayed"


def test_one_malformed_trace_fails_the_whole_pipeline(stage3_trace, tmp_path):
    _, _, trace_path = stage3_trace
    trace_path.write_text(
        trace_path.read_text(encoding="utf-8") + '{"events":[{}]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid trace JSONL at line 2"):
        build_detection_report(trace_path)
