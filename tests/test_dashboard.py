import json

import pytest
from streamlit.testing.v1 import AppTest

import app as dashboard
from dataset.download_tamas import DATASET_ROOT
from dataset.execution_engine import SafeTAMASExecutionEngine
from dataset.experiment_schemas import DetectorTaskInput
from dataset.tamas_loader import TAMASAttack, load_tamas_dataset
from dataset.trace_store import trace_to_json
from dataset.workflow_preview import InteractionMode


ROOT = dashboard.ROOT


def test_dashboard_loads_existing_project_outputs_without_evaluator_sidecars():
    data = dashboard.load_dashboard_data()

    assert data["tamas_stats"]["records"] == 70
    expected_traces = sum(
        1
        for path in dashboard.RUNS_DIR.glob("*.jsonl")
        if not path.name.endswith(".evaluator.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert len(data["traces"]) == expected_traces
    assert len(data["reports"]) == 2
    assert "raw.csv" in data["routing_csvs"]
    assert all(not item["source"].name.endswith(".evaluator.jsonl")
               for item in data["traces"])
    assert {report["_source"].name for report in data["reports"]} == {
        "stage5_no_claims.json",
        "stage5_synthetic_test.json",
    }


def test_dashboard_loads_valid_trace_records_and_warns_for_malformed_lines(tmp_path):
    dataset = load_tamas_dataset(DATASET_ROOT)
    record = next(item for item in dataset.records if item.is_benign)
    task = DetectorTaskInput.from_tamas_record(record, "dashboard-test-task")
    trace = SafeTAMASExecutionEngine(dataset.tool_definitions).run(task, seed=3).trace
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "mixed.jsonl").write_text(
        trace_to_json(trace) + "\n{bad json\n",
        encoding="utf-8",
    )

    data = dashboard.load_dashboard_data(
        runs_dir=runs_dir,
        detection_dir=tmp_path / "missing-detection",
        results_dir=tmp_path / "missing-results",
        tamas_dir=tmp_path / "missing-tamas",
    )

    assert len(data["traces"]) == 1
    assert data["reports"] == []
    assert data["routing_csvs"] == {}
    assert data["tamas"] is None
    assert any("mixed.jsonl:2" in warning for warning in data["warnings"])
    assert data["tamas_error"]


def test_dashboard_skips_malformed_report_and_csv_without_crashing(tmp_path):
    detection_dir = tmp_path / "detection"
    detection_dir.mkdir()
    (detection_dir / "broken.json").write_text(
        json.dumps({"trace_reports": "not-an-array"}), encoding="utf-8"
    )
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "raw.csv").write_text('"unterminated\n', encoding="utf-8")

    reports, report_warnings = dashboard._read_detection_reports(detection_dir)
    csvs, csv_warnings = dashboard._read_routing_csvs(results_dir)

    assert reports == []
    assert report_warnings
    assert csvs == {}
    assert csv_warnings


def test_dashboard_labels_synthetic_findings_and_no_claims_report_honestly():
    data = dashboard.load_dashboard_data()
    findings = dashboard._flatten_findings(data["reports"])

    assert findings
    assert all(
        item["synthetic"]
        for item in findings
        if item["source_file"] == "stage5_synthetic_test.json"
    )
    no_claims = next(
        report for report in data["reports"]
        if report["status"] == "NO_CLAIMS_AVAILABLE"
    )
    assert no_claims["claims_supplied"] == 0
    assert no_claims["findings_count"] == 0


def test_dashboard_loads_stage8_results_with_separate_tamas_status():
    data = dashboard.load_dashboard_data()

    detector = data["stage8"]["detector_evaluation"]
    cross = data["stage8"]["cross_message_evaluation"]
    false_negatives = data["stage8"]["false_negative_analysis"]
    tamas = data["stage8"]["tamas_integration"]
    assert detector["assertion_metrics"]["assertions_total"] == 820
    assert cross["metrics"]["true_positives"] == 50
    assert false_negatives["false_negative_count"] == 100
    assert tamas["detection_status"] == "NO_CLAIMS_AVAILABLE"
    assert tamas["claims_available"] == 0
    assert tamas["records_with_insufficient_evidence"] is None


def test_runner_output_path_is_confined_and_does_not_overwrite(tmp_path):
    output_dir = tmp_path / "runs"
    output_dir.mkdir()

    assert dashboard._resolve_run_destination("fresh.jsonl", output_dir) == (
        output_dir.resolve() / "fresh.jsonl"
    )
    with pytest.raises(ValueError):
        dashboard._resolve_run_destination("../outside.jsonl", output_dir)
    with pytest.raises(ValueError):
        dashboard._resolve_run_destination("fresh.evaluator.jsonl", output_dir)
    existing = output_dir / "exists.jsonl"
    existing.touch()
    with pytest.raises(FileExistsError):
        dashboard._resolve_run_destination(existing.name, output_dir)


@pytest.mark.parametrize(
    "page",
    [
        "Dashboard Overview",
        "Experiment Runner",
        "Agent Monitoring",
        "Provenance Investigation",
        "Detection Results",
        "TAMAS Dataset",
        "Routing Analysis",
        "Stage 8 Evaluation",
        "Trust-Aware Routing Evaluation",
    ],
)
def test_every_dashboard_page_renders(page):
    app_test = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app_test.exception
    app_test.radio[0].set_value(page).run()
    assert not app_test.exception
    if page == "Stage 8 Evaluation":
        assert any(
            item.value == "Stage 4 provenance detector — controlled simulations"
            for item in app_test.subheader
        )
        assert any(
            "TAMAS integration — record-level status only" in item.value
            for item in app_test.subheader
        )
    if page == "Trust-Aware Routing Evaluation":
        if dashboard.STAGE9_OUTPUT_DIR.is_dir():
            assert any(
                item.value == "Routing and task outcome comparison"
                for item in app_test.subheader
            )
        else:
            assert any(
                "Stage 9 routing outputs are unavailable" in item.value
                for item in app_test.warning
            )


def test_experiment_runner_calls_existing_backend_directly(monkeypatch, tmp_path):
    import run_tamas_execution

    calls = []

    def fake_run_validation_pair(**kwargs):
        calls.append(kwargs)
        return (
            {"condition": "benign", "trace_id": "trace-a"},
            {"condition": "adversarial", "trace_id": "trace-b"},
        )

    monkeypatch.setattr(run_tamas_execution, "run_validation_pair", fake_run_validation_pair)
    app_test = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app_test.radio[0].set_value("Experiment Runner").run()
    app_test.text_input[0].set_value("dashboard-run.jsonl")
    app_test.form_submit_button[0].click().run()

    assert not app_test.exception
    assert len(calls) == 1
    assert calls[0]["destination"] == dashboard.RUNS_DIR.resolve() / "dashboard-run.jsonl"
    assert calls[0]["mode"] in tuple(InteractionMode)
    assert calls[0]["attack"] in tuple(attack for attack in TAMASAttack if attack != TAMASAttack.BENIGN)
    assert calls[0]["seed"] == 19
    assert any("Run completed" in element.value for element in app_test.success)


def test_dashboard_loads_stage9_outputs_separately(tmp_path):
    stage9_dir = tmp_path / "stage9"
    stage9_dir.mkdir()
    (stage9_dir / "manifest.json").write_text(
        json.dumps({
            "tasks_per_strategy": 5,
            "seeds": [9100],
            "agent_count": 5,
            "strategies": ["R0", "trust_aware"],
            "tamas_status": "INSUFFICIENT_EVIDENCE_NO_CLAIMS_AVAILABLE",
        }),
        encoding="utf-8",
    )
    (stage9_dir / "routing_evaluation.json").write_text(
        json.dumps({
            "results_by": {"seed": {"9100": {"tasks": 5}}},
            "task_results": [],
        }),
        encoding="utf-8",
    )
    (stage9_dir / "detector_and_cross_message_evaluation.json").write_text(
        json.dumps({"by_strategy": {"trust_aware": {"detector": {}, "cross_message": {}}}}),
        encoding="utf-8",
    )
    (stage9_dir / "routing_decisions.jsonl").write_text(
        json.dumps({"strategy": "trust_aware", "selected_agent": "agent-a"}) + "\n",
        encoding="utf-8",
    )
    (stage9_dir / "strategy_comparison.csv").write_text(
        "strategy,tasks,task_success_rate\ntrust_aware,5,0.8\n",
        encoding="utf-8",
    )

    data = dashboard.load_dashboard_data(stage9_dir=stage9_dir)

    assert data["stage9"]["manifest"]["tasks_per_strategy"] == 5
    assert data["stage9"]["decisions"][0]["selected_agent"] == "agent-a"
    assert "strategy_comparison.csv" in data["stage9"]["csvs"]
