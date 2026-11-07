import json

import pytest

from dataset.tamas_loader import (
    EXPECTED_FINANCE_RELEASE_FILES,
    TAMASAttack,
    TAMASSchemaError,
    dataset_statistics,
    load_tamas_dataset,
)
from dataset import download_tamas
from dataset.tool_catalog import load_finance_tool_definitions
from dataset.workflow_preview import InteractionMode, preview_workflow
from run_tamas_preview import main as run_preview


def _record(query_key="user_query", agent_count=4, **extra):
    return {
        query_key: "Review this transaction for risk.",
        "agents": [
            {
                "agent_name": f"Agent {index}",
                "agent_description": f"Original role description {index}.",
                **({"tools": []} if index == 0 else {}),
            }
            for index in range(agent_count)
        ],
        **extra,
    }


def _write(directory, filename, records):
    (directory / filename).write_text(json.dumps(records), encoding="utf-8")


def test_loads_official_finance_file_variants_and_maps_filename_labels(tmp_path):
    _write(tmp_path, "PNA_finance.json", [
        _record(sequence_of_tool_calls=["lookup_customer(id=7)"])
    ])
    _write(tmp_path, "RAS_finance_DPI.json", [
        _record("user query", agents_involved=["Agent 1"])
    ])
    _write(tmp_path, "RAS_finance_IPI.json", [
        _record(**{"expected output": "Safe response"})
    ])
    _write(tmp_path, "RAS_finance_Imp.json", [_record()])
    _write(tmp_path, "RAS_finance_Coop.json", [_record()])
    _write(tmp_path, "RAS_finance_contra.json", [_record(agent_count=5)])

    dataset = load_tamas_dataset(tmp_path)
    stats = dataset_statistics(dataset)

    assert len(dataset.records) == 6
    assert dataset.records[0].attack_type == TAMASAttack.BENIGN
    assert dataset.records[1].original_user_query == "Review this transaction for risk."
    assert dataset.records[1].agents_involved == ("Agent 1",)
    assert dataset.records[2].expected_output == "Safe response"
    assert dataset.records[2].agents[0].tools == ()
    assert dataset.records[2].agents[1].tools is None
    assert len(dataset.records[-1].agents) == 5
    assert dataset.records[0].sequence_of_tool_calls == ("lookup_customer(id=7)",)
    assert set(dataset.missing_release_files) == set(EXPECTED_FINANCE_RELEASE_FILES) - {
        record.source_file for record in dataset.records
    }
    assert stats["benign_records"] == 1
    assert stats["adversarial_records"] == 5
    assert stats["attack_counts"]["direct_prompt_injection"] == 1
    assert stats["complete_icml_benchmark"] is False


@pytest.mark.parametrize("mode", list(InteractionMode))
def test_preview_only_schedules_original_roster_without_faking_execution(tmp_path, mode):
    _write(tmp_path, "RAS_finance_DPI.json", [
        _record(agents_involved=["Agent 1"])
    ])
    record = load_tamas_dataset(tmp_path).records[0]

    preview = preview_workflow(record, mode)

    assert preview.original_user_query == record.original_user_query
    assert preview.interaction_mode == mode
    assert len([event for event in preview.events if event.agent_id is not None]) == 4
    assert all(event.execution_status == "scheduled_not_executed" for event in preview.events)
    assert preview.recorded_tool_calls is None
    assert "no LLM responses" in preview.notice


def test_schema_error_includes_source_and_record_location(tmp_path):
    _write(tmp_path, "RAS_finance_DPI.json", [{"agents": []}])
    with pytest.raises(TAMASSchemaError, match="RAS_finance_DPI.json, record 0"):
        load_tamas_dataset(tmp_path)


def test_invalid_top_level_shape_is_not_silently_accepted(tmp_path):
    (tmp_path / "PNA_finance.json").write_text('{"unexpected": "object"}', encoding="utf-8")
    with pytest.raises(TAMASSchemaError, match="top-level JSON array"):
        load_tamas_dataset(tmp_path)


def test_missing_dataset_and_unknown_directory_fail_explicitly(tmp_path):
    with pytest.raises(FileNotFoundError, match="No recognized"):
        load_tamas_dataset(tmp_path)
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_tamas_dataset(tmp_path / "not-present")


def test_downloader_reuses_matching_files_and_refetches_tampered_files(tmp_path, monkeypatch):
    calls = []

    def fetch(path):
        calls.append(path)
        return f"pinned-content:{path}".encode()

    monkeypatch.setattr(download_tamas, "_fetch", fetch)
    first = download_tamas.download_dataset(tmp_path)
    assert len(calls) == 10
    assert first["commit"] == download_tamas.OFFICIAL_COMMIT

    calls.clear()
    download_tamas.download_dataset(tmp_path)
    assert calls == []

    (tmp_path / "PNA_finance.json").write_text("tampered", encoding="utf-8")
    download_tamas.download_dataset(tmp_path)
    assert calls == ["data/PNA_finance.json"]
    assert (tmp_path / "PNA_finance.json").read_bytes() == (
        b"pinned-content:data/PNA_finance.json"
    )


def test_preview_reports_when_requested_classes_are_missing(tmp_path):
    _write(tmp_path, "RAS_finance_DPI.json", [_record()])
    with pytest.raises(SystemExit, match="selected dataset directory is incomplete"):
        run_preview(["--data", str(tmp_path), "--attack", "direct_prompt_injection"])


def test_tool_catalog_parses_metadata_without_importing_tool_code(tmp_path):
    source = tmp_path / "tools_identity_verification_agent.py"
    source.write_text(
        "raise RuntimeError('must not execute')\n"
        "async def verify_otp(user_id: str, otp: str) -> str:\n"
        "    '''Verify an OTP.'''\n"
        "    return 'unused'\n",
        encoding="utf-8",
    )

    definitions, missing = load_finance_tool_definitions(tmp_path)

    assert len(definitions) == 1
    assert definitions[0].agent_role == "Identity Verification Agent"
    assert definitions[0].name == "verify_otp"
    assert definitions[0].parameters == ("user_id", "otp")
    assert definitions[0].return_annotation == "str"
    assert definitions[0].description == "Verify an OTP."
    assert definitions[0].is_async
    assert definitions[0].classification == "unclassified"
    assert len(missing) == 3
