"""Local ProvenanceGuard research dashboard."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from config import VERSION_NAMES
from dataset.download_tamas import DATASET_ROOT
from dataset.experiment_schemas import TraceEventType
from dataset.tamas_loader import TAMASAttack, dataset_statistics, load_tamas_dataset
from dataset.trace_store import trace_from_dict
from dataset.workflow_preview import InteractionMode


ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "data" / "tamas" / "runs"
DETECTION_DIR = ROOT / "data" / "tamas" / "detection"
RESULTS_DIR = ROOT / "results"
STAGE8_EVALUATION_DIR = ROOT / "data" / "controlled" / "evaluation" / "stage8_broader_20261107"
STAGE8_TAMAS_SUMMARY = (
    ROOT / "data" / "tamas" / "stage8_integration_20261107"
    / "tamas_integration_summary.json"
)
STAGE9_OUTPUT_DIR = ROOT / "data" / "controlled" / "stage9_routing_20261107_final_v3"
OUTCOMES = (
    "SUPPORTED",
    "UNSUPPORTED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
)
OUTCOME_COLORS = {
    "SUPPORTED": "#28c98b",
    "UNSUPPORTED": "#f0a74a",
    "CONTRADICTED": "#ff5d70",
    "INSUFFICIENT_EVIDENCE": "#91a4bd",
}


def _read_trace_files(directory: Path) -> tuple[list[dict[str, Any]], list[str]]:
    traces: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not directory.is_dir():
        return traces, warnings

    for path in sorted(directory.glob("*.jsonl")):
        if path.name.endswith(".evaluator.jsonl"):
            continue
        try:
            with path.open(encoding="utf-8") as source:
                for line_number, line in enumerate(source, start=1):
                    if not line.strip():
                        continue
                    try:
                        trace = trace_from_dict(json.loads(line))
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                        warnings.append(f"{path.name}:{line_number}: {exc}")
                        continue
                    traces.append({"trace": trace, "source": path})
        except (OSError, UnicodeError) as exc:
            warnings.append(f"{path.name}: {exc}")
    return traces, warnings


def _read_detection_reports(directory: Path) -> tuple[list[dict[str, Any]], list[str]]:
    reports: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not directory.is_dir():
        return reports, warnings

    for path in sorted(directory.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("report must be a JSON object")
            trace_reports = report.get("trace_reports", [])
            if not isinstance(trace_reports, list) or any(
                not isinstance(item, dict) for item in trace_reports
            ):
                raise ValueError("trace_reports must be an array of objects")
            if not isinstance(report.get("outcome_counts", {}), dict):
                raise ValueError("outcome_counts must be an object")
            if any(
                not isinstance(item.get("findings", []), list)
                for item in trace_reports
            ):
                raise ValueError("each trace report findings field must be an array")
            report["_source"] = path
            report["_synthetic"] = "synthetic" in path.stem.casefold()
            reports.append(report)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            warnings.append(f"{path.name}: {exc}")
    return reports, warnings


def _read_routing_csvs(directory: Path) -> tuple[dict[str, pd.DataFrame], list[str]]:
    frames: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []
    if not directory.is_dir():
        return frames, warnings

    for path in sorted(directory.glob("*.csv")):
        try:
            frames[path.name] = pd.read_csv(path)
        except (OSError, UnicodeError, pd.errors.ParserError, ValueError) as exc:
            warnings.append(f"{path.name}: {exc}")
    return frames, warnings


def _read_jsonl_objects(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not path.is_file():
        return rows, warnings
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    warnings.append(f"{path.name}:{line_number}: {exc}")
                    continue
                if not isinstance(value, dict):
                    warnings.append(f"{path.name}:{line_number}: expected an object.")
                    continue
                rows.append(value)
    except (OSError, UnicodeError) as exc:
        warnings.append(f"{path.name}: {exc}")
    return rows, warnings


def _read_optional_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, f"{path.name}: Stage 8 report is not available."
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("report must be a JSON object")
        return value, None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return None, f"{path.name}: {exc}"


def load_dashboard_data(
    *,
    runs_dir: Path = RUNS_DIR,
    detection_dir: Path = DETECTION_DIR,
    results_dir: Path = RESULTS_DIR,
    tamas_dir: Path = DATASET_ROOT,
    stage9_dir: Path = STAGE9_OUTPUT_DIR,
) -> dict[str, Any]:
    """Load real project outputs while keeping evaluator sidecars out of traces."""
    traces, trace_warnings = _read_trace_files(runs_dir)
    reports, report_warnings = _read_detection_reports(detection_dir)
    routing_csvs, csv_warnings = _read_routing_csvs(results_dir)
    try:
        tamas = load_tamas_dataset(tamas_dir)
        tamas_stats = dataset_statistics(tamas)
        tamas_error = None
    except (OSError, ValueError) as exc:
        tamas = None
        tamas_stats = None
        tamas_error = str(exc)

    stage8_reports = {}
    stage8_warnings = []
    for key, path in {
        "detector_evaluation": STAGE8_EVALUATION_DIR / "aggregate" / "evaluation_summary.json",
        "cross_message_evaluation": STAGE8_EVALUATION_DIR / "cross_message_evaluation.json",
        "false_negative_analysis": STAGE8_EVALUATION_DIR / "stage8_false_negative_analysis.json",
        "stage7_false_negative_analysis": (
            STAGE8_EVALUATION_DIR / "stage7_false_negative_analysis.json"
        ),
        "reproducibility": STAGE8_EVALUATION_DIR / "reproducibility_analysis.json",
        "tamas_integration": STAGE8_TAMAS_SUMMARY,
    }.items():
        report, warning = _read_optional_json(path)
        stage8_reports[key] = report
        if warning:
            stage8_warnings.append(warning)

    stage9_reports: dict[str, dict[str, Any] | None] = {}
    stage9_warnings: list[str] = []
    for key, filename in {
        "manifest": "manifest.json",
        "routing_evaluation": "routing_evaluation.json",
        "detector_and_cross_message_evaluation": (
            "detector_and_cross_message_evaluation.json"
        ),
    }.items():
        report, warning = _read_optional_json(stage9_dir / filename)
        stage9_reports[key] = report
        if warning:
            stage9_warnings.append(warning)
    stage9_decisions, decision_warnings = _read_jsonl_objects(
        stage9_dir / "routing_decisions.jsonl"
    )
    stage9_csvs, stage9_csv_warnings = _read_routing_csvs(stage9_dir)
    stage9_warnings.extend(decision_warnings + stage9_csv_warnings)

    return {
        "traces": traces,
        "reports": reports,
        "routing_csvs": routing_csvs,
        "tamas": tamas,
        "tamas_stats": tamas_stats,
        "tamas_error": tamas_error,
        "stage8": stage8_reports,
        "stage9": {
            **stage9_reports,
            "decisions": stage9_decisions,
            "csvs": stage9_csvs,
            "output_dir": stage9_dir,
        },
        "warnings": (
            trace_warnings + report_warnings + csv_warnings + stage8_warnings
            + stage9_warnings
        ),
    }


def _flatten_findings(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for report in reports:
        for trace_report in report.get("trace_reports", []):
            if not isinstance(trace_report, dict):
                continue
            for finding in trace_report.get("findings", []):
                if isinstance(finding, dict):
                    findings.append({
                        **finding,
                        "trace_id": trace_report.get("trace_id", "Not available"),
                        "task_id": trace_report.get("task_id", "Not available"),
                        "experiment_id": report.get("experiment_id", "Not available"),
                        "source_file": report["_source"].name,
                        "synthetic": report["_synthetic"],
                    })
    return findings


def _trace_agents(trace) -> set[str]:
    agents = set()
    for event in trace.events:
        if event.source.value == "agent" and event.actor_id:
            agents.add(event.actor_id)
        if event.recipient_id and not event.recipient_id.startswith("local-stub:"):
            agents.add(event.recipient_id)
    return agents


def _trace_event_rows(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in traces:
        trace = item["trace"]
        for event in trace.events:
            rows.append({
                "trace_id": trace.trace_id,
                "source_file": item["source"].name,
                "sequence": event.sequence,
                "event_type": event.event_type.value,
                "source": event.source.value,
                "actor_id": event.actor_id,
                "recipient_id": event.recipient_id,
                "tool_name": event.tool_name,
                "execution_status": event.execution_status.value,
                "content": event.content,
                "elapsed_ms": event.elapsed_ms,
            })
    return rows


def _agent_roles(traces: list[dict[str, Any]]) -> dict[str, str]:
    roles: dict[str, str] = {}
    role_pattern = re.compile(r"Scheduled original role '(.+?)' from the task query")
    for item in traces:
        for event in item["trace"].events:
            if event.event_type == TraceEventType.ROUTER_DECISION and event.recipient_id:
                match = role_pattern.search(event.content or "")
                if match:
                    roles.setdefault(event.recipient_id, match.group(1))
    return roles


def _resolve_run_destination(filename: str, runs_dir: Path = RUNS_DIR) -> Path:
    """Accept only a new JSONL basename in the known local run-output folder."""
    name = filename.strip()
    if not name or Path(name).name != name or Path(name).suffix.casefold() != ".jsonl":
        raise ValueError("Enter a JSONL filename only, for example demo_run.jsonl.")
    if name.endswith(".evaluator.jsonl"):
        raise ValueError("Evaluator sidecar filenames are reserved.")
    directory = runs_dir.resolve()
    destination = (directory / name).resolve()
    if destination.parent != directory:
        raise ValueError("Output must remain inside the project runs directory.")
    if destination.exists():
        raise FileExistsError("That output file already exists; choose a new filename.")
    return destination


def _metric_card(label: str, value: str, detail: str = "", tone: str = "blue") -> None:
    safe_label = html.escape(label)
    safe_value = html.escape(value)
    safe_detail = html.escape(detail)
    st.markdown(
        f"""
        <div class="metric-card {tone}">
          <div class="metric-label">{safe_label}</div>
          <div class="metric-value">{safe_value}</div>
          <div class="metric-detail">{safe_detail}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _plotly_theme(fig):
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#c8d6e8", "family": "Inter, sans-serif"},
        margin={"l": 12, "r": 12, "t": 28, "b": 12},
        legend={"bgcolor": "rgba(0,0,0,0)"},
    )
    fig.update_xaxes(gridcolor="rgba(149,170,198,0.12)", zerolinecolor="rgba(149,170,198,0.12)")
    fig.update_yaxes(gridcolor="rgba(149,170,198,0.12)", zerolinecolor="rgba(149,170,198,0.12)")
    return fig


def _all_outcome_counts(reports: list[dict[str, Any]]) -> dict[str, int]:
    totals = Counter()
    for report in reports:
        counts = report.get("outcome_counts", {})
        if isinstance(counts, dict):
            for outcome in OUTCOMES:
                totals[outcome] += int(counts.get(outcome, 0) or 0)
    return {outcome: totals[outcome] for outcome in OUTCOMES}


def _render_overview(data: dict[str, Any]) -> None:
    reports = data["reports"]
    traces = data["traces"]
    findings = _flatten_findings(reports)
    outcomes = _all_outcome_counts(reports)
    agents = set().union(*(_trace_agents(item["trace"]) for item in traces)) if traces else set()
    event_rows = _trace_event_rows(traces)
    explicit_claims = sum(int(report.get("claims_supplied", 0) or 0) for report in reports)
    routing_runs = len(data["routing_csvs"].get("raw.csv", ()))
    detector_runs = sum(bool(report.get("experiment_id")) for report in reports)
    experiment_runs = routing_runs + detector_runs

    st.markdown(
        '<div class="hero"><div class="eyebrow">RESEARCH MONITORING CONSOLE</div>'
        '<h1>PROVENANCE<span>GUARD</span></h1>'
        '<p>Trust-Aware Multi-Agent Monitoring and Manipulation Detection</p></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Counts below are read from discovered trace, detector-report, and routing-result files. "
        "Detection reports are separate runs and may cover the same trace."
    )

    message_count = sum(
        row["event_type"] == TraceEventType.AGENT_MESSAGE.value
        for row in event_rows
    )
    cols = st.columns(5)
    cards = [
        ("Experiment runs", str(experiment_runs), "R0–R8 raw rows + Stage 5 detector reports", "blue"),
        ("Traces", str(len(traces)), "Validated JSONL trace records", "cyan"),
        ("Agents observed", str(len(agents)), "Unique task-local IDs in trace events", "green"),
        ("Agent messages", str(message_count), "Recorded structured message events", "cyan"),
        ("Events", str(len(event_rows)), "Events across loaded traces", "blue"),
    ]
    for column, args in zip(cols, cards):
        with column:
            _metric_card(*args)

    cols = st.columns(5)
    cards = [
        ("Claims supplied", str(explicit_claims), "Counted per detector report", "cyan"),
        ("Supported", str(outcomes["SUPPORTED"]), "Across loaded detector reports", "green"),
        ("Unsupported", str(outcomes["UNSUPPORTED"]), "Evidence insufficient/invalid", "orange"),
        ("Contradicted", str(outcomes["CONTRADICTED"]), "Evidence conflicts with claim", "red"),
        ("Insufficient evidence", str(outcomes["INSUFFICIENT_EVIDENCE"]), "Cannot conclude from available evidence", "blue"),
    ]
    for column, args in zip(cols, cards):
        with column:
            _metric_card(*args)

    left, right = st.columns((1, 1))
    with left:
        st.subheader("Detection outcome distribution")
        if reports:
            frame = pd.DataFrame(
                [{"Outcome": key, "Findings": value} for key, value in outcomes.items()]
            )
            fig = px.pie(
                frame, names="Outcome", values="Findings", hole=0.68,
                color="Outcome", color_discrete_map=OUTCOME_COLORS,
            )
            st.plotly_chart(_plotly_theme(fig), width="stretch")
            st.caption("A report with zero findings is shown as an empty distribution, not a detector success.")
        else:
            st.info("No detector report files are available.")

    with right:
        st.subheader("Agent activity")
        if event_rows:
            activity = Counter()
            for row in event_rows:
                for agent_id in {row["actor_id"], row["recipient_id"]} - {None}:
                    if not str(agent_id).startswith("local-stub:"):
                        activity[str(agent_id)] += 1
            activity_frame = pd.DataFrame(
                [{"Agent": agent_id, "Associated events": count}
                 for agent_id, count in sorted(activity.items())]
            )
            if not activity_frame.empty:
                fig = px.bar(
                    activity_frame, x="Agent", y="Associated events",
                    color_discrete_sequence=["#34b6e8"],
                )
                st.plotly_chart(_plotly_theme(fig), width="stretch")
            else:
                st.info("No agent-attributed events were found.")
        else:
            st.info("No trace events are available.")

    left, right = st.columns((1, 1))
    with left:
        st.subheader("TAMAS record-category distribution")
        stats = data.get("tamas_stats")
        if stats and stats["attack_counts"]:
            category_frame = pd.DataFrame(
                [{"Record category": name, "Records": count}
                 for name, count in stats["attack_counts"].items()]
            )
            fig = px.bar(
                category_frame, x="Record category", y="Records",
                color_discrete_sequence=["#5478ff"],
            )
            st.plotly_chart(_plotly_theme(fig), width="stretch")
            st.caption("These are record-level categories, not per-agent truth labels.")
        else:
            st.info(data.get("tamas_error") or "TAMAS dataset is not available.")

    with right:
        st.subheader("Recent detector reports")
        if reports:
            rows = []
            for report in sorted(
                reports,
                key=lambda item: item["_source"].stat().st_mtime,
                reverse=True,
            ):
                rows.append({
                    "Report": report["_source"].name,
                    "Experiment ID": report.get("experiment_id", "Not available"),
                    "Status": report.get("status", "Not available"),
                    "Traces": report.get("traces_loaded", "Not available"),
                    "Findings": report.get("findings_count", "Not available"),
                    "Data type": "CONTROLLED SYNTHETIC TEST DATA"
                    if report["_synthetic"] else "Project output",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.info("No detector reports are available yet.")

    st.subheader("Pipeline status")
    trace_count = len(traces)
    report_count = len(reports)
    pipeline = [
        ("TAMAS data loading", "AVAILABLE" if data.get("tamas") else "UNAVAILABLE",
         f"{data['tamas_stats']['records']} records loaded" if data.get("tamas_stats") else data.get("tamas_error", "")),
        ("Local execution engine", "IMPLEMENTED",
         "Deterministic local simulation; no external tools or LLM calls."),
        ("Trace generation", "AVAILABLE" if trace_count else "AWAITING DATA",
         f"{trace_count} validated trace(s) loaded."),
        ("Provenance detector", "IMPLEMENTED" if report_count else "AWAITING DATA",
         f"{report_count} detector report(s) discovered."),
        ("Evidence verification", "IMPLEMENTED",
         "Ed25519/trust checks exist; loaded reports do not establish that signed evidence was exercised."),
        ("Cross-message analysis", "NOT IMPLEMENTED",
         "Current detector reports assertions independently; cross-message contradiction analysis is pending."),
        ("Independent evaluator", "PARTIAL",
         "Stage 3 record mappings exist separately; claim-level evaluator annotations are not available."),
        ("Trust-aware routing", "NOT INTEGRATED",
         "Detector findings are not connected to agent selection."),
    ]
    st.dataframe(
        pd.DataFrame(pipeline, columns=("Component", "Status", "Evidence")),
        hide_index=True,
        width="stretch",
    )
    if data["warnings"]:
        with st.expander(f"Data loading warnings ({len(data['warnings'])})"):
            for warning in data["warnings"]:
                st.warning(warning)


def _render_experiment_runner() -> None:
    st.subheader("Run a controlled local validation pair")
    st.info(
        "This calls the existing `run_validation_pair` API with one benign and one selected "
        "record-level TAMAS category. It creates deterministic scheduling/tool-simulation traces "
        "only; it does not call external tools or generate LLM messages. Record categories do not "
        "label individual agents as malicious."
    )
    modes = list(InteractionMode)
    attack_options = [attack for attack in TAMASAttack if attack != TAMASAttack.BENIGN]
    with st.form("execution_form"):
        mode = st.selectbox(
            "Interaction mode",
            options=[item.value for item in modes],
            format_func=lambda value: value.replace("_", " ").title(),
        )
        attack = st.selectbox(
            "Adversarial record category",
            options=[item.value for item in attack_options],
            format_func=lambda value: value.replace("_", " ").title(),
            help="Chooses a record category for the validation pair; this is not agent-level ground truth.",
        )
        seed = st.number_input("Random seed", min_value=0, max_value=2_147_483_647, value=19, step=1)
        filename = st.text_input(
            "Output filename (inside data/tamas/runs/)",
            value=f"provenanceguard_{mode}_{seed}.jsonl",
        )
        submitted = st.form_submit_button("Run local simulation", type="primary")

    st.caption("Backend: `run_tamas_execution.run_validation_pair` (direct Python API; no shell command input).")
    if submitted:
        try:
            from run_tamas_execution import run_validation_pair

            destination = _resolve_run_destination(filename)
            with st.spinner("Running deterministic local simulation..."):
                summaries = run_validation_pair(
                    data_directory=DATASET_ROOT,
                    destination=destination,
                    attack=TAMASAttack(attack),
                    mode=InteractionMode(mode),
                    seed=int(seed),
                )
            st.session_state["last_execution"] = {
                "status": "COMPLETED",
                "mode": mode,
                "seed": int(seed),
                "trace_path": str(destination),
                "evaluator_path": str(destination.with_suffix(".evaluator.jsonl")),
                "summaries": list(summaries),
            }
            st.rerun()
        except (OSError, ValueError, RuntimeError, FileExistsError) as exc:
            st.session_state["last_execution"] = {
                "status": "FAILED",
                "error": str(exc),
                "mode": mode,
                "seed": int(seed),
            }
        except ImportError as exc:
            st.session_state["last_execution"] = {
                "status": "FAILED",
                "error": f"Unable to load the existing execution runner: {exc}",
                "mode": mode,
                "seed": int(seed),
            }

    result = st.session_state.get("last_execution")
    if result:
        if result["status"] == "COMPLETED":
            st.success(
                f"Run completed · {result['mode']} · seed {result['seed']}"
            )
            st.code(json.dumps(result["summaries"], indent=2, default=str), language="json")
            st.write("Trace output:", result["trace_path"])
            st.write("Evaluator-only sidecar (not loaded by detector workflows):", result["evaluator_path"])
            st.caption("Dashboard data is refreshed on this rerun.")
        else:
            st.error(f"Run failed: {result['error']}")
            st.caption(f"Configuration: mode={result.get('mode')}, seed={result.get('seed')}")


def _render_agent_monitoring(data: dict[str, Any]) -> None:
    traces = data["traces"]
    if not traces:
        st.info("Awaiting trace data. Run a local validation pair or add a valid trace JSONL.")
        return

    trace_options = {
        f"{item['trace'].trace_id} · {item['source'].name}": item
        for item in traces
    }
    selected_trace_label = st.selectbox("Trace", list(trace_options))
    item = trace_options[selected_trace_label]
    trace = item["trace"]
    roles = _agent_roles([item])
    agents = sorted(_trace_agents(trace))
    st.caption(
        f"Source: {item['source'].name} · execution: {trace.execution_mode.value} · "
        f"interaction mode: {trace.interaction_mode.value} · seed: {trace.seed if trace.seed is not None else 'Not available'}"
    )
    if not any(event.event_type == TraceEventType.AGENT_MESSAGE for event in trace.events):
        st.warning(
            "This is a deterministic execution trace, not a natural-language agent conversation. "
            "Scheduled activity and local simulated tool events are shown separately."
        )

    event_rows = _trace_event_rows([item])
    event_frame = pd.DataFrame(event_rows)
    if not event_frame.empty:
        event_frame["activity_class"] = event_frame["event_type"].map({
            "router_decision": "Scheduled activity",
            "handoff": "Handoff",
            "tool_invocation": "Tool invocation",
            "tool_result": "Tool result",
            "agent_message": "Agent message",
        }).fillna("System/task event")
        st.dataframe(
            event_frame[[
                "sequence", "activity_class", "event_type", "actor_id",
                "recipient_id", "tool_name", "execution_status", "elapsed_ms", "content",
            ]],
            hide_index=True,
            width="stretch",
        )

    if agents:
        selected_agent = st.selectbox(
            "Agent detail",
            agents,
            format_func=lambda agent_id: f"{agent_id} · {roles.get(agent_id, 'role not recorded')}",
        )
        associated = [
            row for row in event_rows
            if selected_agent in (row["actor_id"], row["recipient_id"])
        ]
        st.markdown(f"#### Activity timeline · `{selected_agent}`")
        if associated:
            st.dataframe(pd.DataFrame(associated), hide_index=True, width="stretch")
        else:
            st.info("No events are associated with this agent in the selected trace.")
    else:
        st.info("No task-local agent IDs were observed in this trace.")


def _report_findings_view(data: dict[str, Any]) -> None:
    reports = data["reports"]
    findings = _flatten_findings(reports)
    report_options = {
        report["_source"].name: report for report in reports
    }

    if not reports:
        st.info("No detector report files are available. This differs from a report with zero findings.")
        return
    selected_report_name = st.selectbox(
        "Detector report",
        list(report_options),
        key="findings_report",
    )
    report = report_options[selected_report_name]
    if report["_synthetic"]:
        st.warning("CONTROLLED SYNTHETIC TEST DATA — not TAMAS benchmark findings.")
    if report.get("status") == "NO_CLAIMS_AVAILABLE":
        st.info(
            "NO CLAIMS AVAILABLE: the report contains traces but no supplied structured claims. "
            "No findings were generated."
        )
    report_findings = [
        finding for finding in findings
        if finding["source_file"] == selected_report_name
    ]
    counts = {
        outcome: int(report.get("outcome_counts", {}).get(outcome, 0) or 0)
        for outcome in OUTCOMES
    }
    frame = pd.DataFrame([{"Outcome": key, "Findings": value} for key, value in counts.items()])
    st.plotly_chart(
        _plotly_theme(px.pie(
            frame, names="Outcome", values="Findings", hole=0.68,
            color="Outcome", color_discrete_map=OUTCOME_COLORS,
        )),
        width="stretch",
    )
    if not report_findings:
        if report.get("status") == "NO_CLAIMS_AVAILABLE":
            st.caption("This is an honest empty state, not evidence that agents are honest or dishonest.")
        else:
            st.info("This detector report contains zero findings.")
        st.write(f"Report source: `{selected_report_name}`")
        return

    outcome_filter = st.multiselect(
        "Filter by outcome", OUTCOMES, default=list(OUTCOMES), key="outcome_filter"
    )
    agent_options = sorted({str(item.get("agent_id", "")) for item in report_findings})
    agent_filter = st.multiselect(
        "Filter by agent", agent_options, default=agent_options, key="agent_filter"
    )
    type_options = sorted({str(item.get("claim_type", "")) for item in report_findings})
    type_filter = st.multiselect(
        "Filter by claim type", type_options, default=type_options, key="type_filter"
    )
    filtered = [
        item for item in report_findings
        if item.get("predicted_outcome") in outcome_filter
        and item.get("agent_id") in agent_filter
        and item.get("claim_type") in type_filter
    ]
    if not filtered:
        st.info("No findings match the selected filters.")
        return
    table = pd.DataFrame([{
        "Outcome": item.get("predicted_outcome"),
        "Agent": item.get("agent_id"),
        "Claim type": item.get("claim_type"),
        "Assertion ID": item.get("assertion_id"),
        "Claim ID": item.get("provenance_claim_id"),
        "Trace": item.get("trace_id"),
        "Rule": item.get("rule"),
        "Source": item.get("source_file"),
    } for item in filtered])
    st.dataframe(table, hide_index=True, width="stretch")
    finding_ids = [
        f"{item.get('assertion_id')} · {item.get('agent_id')} · {item.get('predicted_outcome')}"
        for item in filtered
    ]
    selected_index = st.selectbox(
        "Inspect a finding", range(len(filtered)),
        format_func=lambda index: finding_ids[index],
    )
    finding = filtered[selected_index]
    st.markdown("#### Finding explanation")
    st.write(finding.get("detection_reason", "Not available"))
    st.json({
        key: finding.get(key) for key in (
            "provenance_claim_id", "assertion_id", "agent_id", "claim_type",
            "asserted_value", "predicted_outcome", "evidence_examined", "rule",
            "trace_id", "source_file",
        )
    })


def _render_provenance_investigation(data: dict[str, Any]) -> None:
    findings = _flatten_findings(data["reports"])
    if not findings:
        st.info(
            "NO CLAIMS AVAILABLE: Current traces do not contain structured provenance claims. "
            "The detector requires claims and evidence to be explicitly supplied; it does not "
            "infer them from trace text, tool calls, or schedules."
        )
        if data["reports"]:
            st.caption("Loaded detector reports contain no assertion findings.")
        return

    report_names = sorted({item["source_file"] for item in findings})
    report_name = st.selectbox("Report source", report_names, key="investigation_report")
    rows = [item for item in findings if item["source_file"] == report_name]
    if any(item["synthetic"] for item in rows):
        st.warning("CONTROLLED SYNTHETIC TEST DATA — not a TAMAS benchmark result.")
    labels = [
        f"{item['assertion_id']} · {item['agent_id']} · {item['claim_type']}"
        for item in rows
    ]
    index = st.selectbox("Claim / assertion", range(len(rows)), format_func=lambda i: labels[i])
    finding = rows[index]

    left, right = st.columns(2)
    with left:
        st.markdown("#### Assertion")
        st.write(f"**Parent claim ID:** {finding.get('provenance_claim_id', 'Not available')}")
        st.write(f"**Assertion ID:** {finding.get('assertion_id', 'Not available')}")
        st.write(f"**Agent ID:** {finding.get('agent_id', 'Not available')}")
        st.write(f"**Claim type:** {finding.get('claim_type', 'Not available')}")
        st.write(f"**Asserted value:** {finding.get('asserted_value', 'Not available')}")
        st.write("**Subject / predicate:** Not represented in the current Stage 5 finding schema.")
        st.write("**Scope:** Not available in current traces/reports.")
        st.write("**Message reference:** Not available; Stage 3 has no structured agent messages.")
        st.write(f"**Trace reference:** {finding.get('trace_id', 'Not available')}")
    with right:
        st.markdown("#### Detector assessment")
        st.write(f"**Outcome:** {finding.get('predicted_outcome', 'Not available')}")
        st.write(f"**Rule:** {finding.get('rule', 'Not available')}")
        st.write(f"**Reason:** {finding.get('detection_reason', 'Not available')}")
        st.write(f"**Report source:** {finding.get('source_file', 'Not available')}")
        st.write("**Evidence references examined:**")
        evidence = finding.get("evidence_examined") or []
        if evidence:
            st.code("\n".join(str(value) for value in evidence))
        else:
            st.caption("No evidence references were examined.")
        st.write("**Issuer/signature/freshness details:** Not included in the current detection report.")
        st.write("**Trust status:** See the finding outcome/rule; no separate trust-detail record is available.")
    if rows[index]["synthetic"]:
        st.caption("These findings validate pipeline behavior only, not TAMAS benchmark performance.")


def _render_tamas(data: dict[str, Any]) -> None:
    dataset = data.get("tamas")
    stats = data.get("tamas_stats")
    if dataset is None or stats is None:
        st.error(f"TAMAS dataset unavailable: {data.get('tamas_error', 'No details')}")
        return
    st.subheader("TAMAS Finance sample")
    st.success(
        f"{stats['records']} records loaded from the local Finance sample. "
        f"{'The inspected sample count is 70.' if stats['records'] == 70 else 'The current record count differs from 70.'}"
    )
    st.warning(
        "TAMAS categories are record-level labels. They do not establish per-agent dishonesty. "
        "`agents_involved` is not shown as agent ground truth."
    )
    cols = st.columns(4)
    for col, args in zip(cols, [
        ("Records", str(stats["records"]), "Loaded from local source JSON", "blue"),
        ("Benign records", str(stats["benign_records"]), "Record-level category", "green"),
        ("Adversarial records", str(stats["adversarial_records"]), "Record-level categories", "orange"),
        ("Tool definitions", str(stats["tool_definitions"]), "Parsed declarations; not executed", "cyan"),
    ]):
        with col:
            _metric_card(*args)

    st.markdown("#### Record categories")
    category_frame = pd.DataFrame([
        {"Record category": category, "Records": count}
        for category, count in stats["attack_counts"].items()
    ])
    st.dataframe(category_frame, hide_index=True, width="stretch")
    st.markdown("#### Sample records")
    sample_rows = [{
        "Record ID": record.record_id,
        "Record category": record.attack_type.value,
        "Agent roles": ", ".join(agent.name for agent in record.agents),
        "Query excerpt": record.original_user_query[:180],
    } for record in dataset.records[:10]]
    st.dataframe(pd.DataFrame(sample_rows), hide_index=True, width="stretch")
    st.caption(
        "This view uses the loader's record-level category metadata for dataset inspection only. "
        "It is not supplied to the provenance detector."
    )
    st.write(f"Tool definitions by role: {stats['tool_definitions_by_agent']}")
    st.write(f"Missing release files: {', '.join(stats['missing_release_files']) or 'None'}")
    st.write(f"Missing tool modules: {', '.join(stats['missing_tool_modules']) or 'None'}")


def _render_routing(data: dict[str, Any]) -> None:
    st.subheader("Existing R0–R8 routing simulation")
    st.info("Trust-aware optimized routing integration is a subsequent implementation step.")
    raw = data["routing_csvs"].get("raw.csv")
    summary = data["routing_csvs"].get("summary.csv")
    router_rows = [
        {"Router": VERSION_NAMES.get(version, f"Version {version}"), "Version": version}
        for version in sorted(VERSION_NAMES)
    ]
    st.dataframe(pd.DataFrame(router_rows), hide_index=True, width="stretch")
    if summary is not None and not summary.empty:
        st.caption("Available aggregate results are read from results/summary.csv.")
        available_metrics = [
            column for column in ("mean_y", "asr", "dq", "far", "detect_rate", "dl", "ms_per_round")
            if column in summary.columns
        ]
        version_col = "version" if "version" in summary.columns else None
        if version_col and available_metrics:
            displayed = summary.copy()
            displayed["router"] = displayed["version"].map(_version_label)
            chosen_metric = st.selectbox("Aggregate metric", available_metrics)
            grouping = [column for column in ("router", "attack", "rho") if column in displayed.columns]
            metric_frame = displayed.groupby(grouping, dropna=False)[chosen_metric].mean().reset_index()
            fig = px.bar(
                metric_frame, x="router", y=chosen_metric,
                color="attack" if "attack" in metric_frame.columns else None,
                barmode="group",
                title=f"Mean {chosen_metric} by router",
            )
            st.plotly_chart(_plotly_theme(fig), width="stretch")
            st.dataframe(displayed.head(100), hide_index=True, width="stretch")
        else:
            st.dataframe(summary.head(100), hide_index=True, width="stretch")
    else:
        st.info("No aggregate routing results are available; router descriptions are shown above.")

    if raw is not None and not raw.empty and {"version", "agent"}.issubset(raw.columns):
        st.markdown("#### Agent-selection distribution")
        distribution = (
            raw.groupby(["version", "agent"]).size().reset_index(name="Selections")
        )
        distribution["Router"] = distribution["version"].map(_version_label)
        fig = px.bar(
            distribution, x="Router", y="Selections", color="agent",
            barmode="stack", title="Recorded selections by router version and simulated agent index",
        )
        st.plotly_chart(_plotly_theme(fig), width="stretch")
        st.caption(
            "Selection counts come from results/raw.csv and identify simulated indices, "
            "not TAMAS agents or detector trust scores."
        )
    else:
        st.info("Agent-level routing selection rows are not available.")
    sources = ", ".join(sorted(data["routing_csvs"])) or "None"
    st.caption(f"Routing result CSV sources discovered: {sources}")


def _render_stage8(data: dict[str, Any]) -> None:
    st.caption(
        "Controlled synthetic simulations and TAMAS record-level integration are "
        "reported separately. These results do not classify agents as malicious."
    )
    stage8 = data["stage8"]
    detector = stage8.get("detector_evaluation")
    if detector is None:
        st.warning("Controlled Stage 8 detector evaluation is unavailable.")
    else:
        metrics = detector.get("assertion_metrics", {})
        st.subheader("Stage 4 provenance detector — controlled simulations")
        st.caption(
            "Assertion-level evaluation against evaluator-sidecar ground truth; "
            "sidecars are not detector inputs."
        )
        columns = st.columns(4)
        for column, label, key in zip(
            columns,
            ("TP", "FP", "TN", "FN"),
            ("true_positives", "false_positives", "true_negatives", "false_negatives"),
        ):
            value = metrics.get(key)
            column.metric(label, value if value is not None else "N/A")
        columns = st.columns(4)
        for column, label, key in zip(
            columns,
            ("Precision", "Recall", "F1", "Accuracy"),
            ("precision", "recall", "f1_score", "accuracy"),
        ):
            value = metrics.get(key)
            column.metric(
                label,
                f"{value:.4f}" if isinstance(value, (int, float)) else "N/A",
            )
        outcomes = metrics.get("detector_outcome_counts", {})
        st.markdown("#### Four-outcome distribution")
        st.dataframe(
            pd.DataFrame([
                {"Outcome": outcome, "Assertions": outcomes.get(outcome, 0)}
                for outcome in OUTCOMES
            ]),
            hide_index=True,
            width="stretch",
        )
        results_by = detector.get("results_by", {})
        seed_results = results_by.get("seed", {})
        if seed_results:
            st.markdown("#### Per-seed metrics")
            seed_rows = [
                {"seed": seed, **values}
                for seed, values in sorted(seed_results.items())
            ]
            st.dataframe(pd.DataFrame(seed_rows), hide_index=True, width="stretch")
        else:
            st.info("Per-seed detector metrics are not available.")

        st.markdown("#### Metric breakdowns")
        for dimension in ("claim_type", "agent_behaviour", "scenario"):
            groups = results_by.get(dimension, {})
            if groups:
                st.markdown(f"**{dimension.replace('_', ' ').title()}**")
                st.dataframe(
                    pd.DataFrame([
                        {"group": group, **values}
                        for group, values in sorted(groups.items())
                    ]),
                    hide_index=True,
                    width="stretch",
                )

    cross = stage8.get("cross_message_evaluation")
    st.subheader("Stage 7D cross-message analysis — separate evaluation")
    if cross is None:
        st.warning("Cross-message evaluation is unavailable.")
    else:
        cross_metrics = cross.get("metrics", {})
        st.caption(
            "Scored independently against sidecar truth and exact associations; "
            "true negatives are undefined because no finite absence universe exists."
        )
        columns = st.columns(3)
        for column, label, key in zip(
            columns,
            ("TP", "FP", "FN"),
            ("true_positives", "false_positives", "false_negatives"),
        ):
            column.metric(label, cross_metrics.get(key, "N/A"))
        columns = st.columns(3)
        for column, label, key in zip(
            columns, ("Precision", "Recall", "F1"),
            ("precision", "recall", "f1_score"),
        ):
            value = cross_metrics.get(key)
            column.metric(label, f"{value:.4f}" if isinstance(value, (int, float)) else "N/A")
        finding_types = cross.get("results_by_finding_type", {})
        if finding_types:
            st.dataframe(
                pd.DataFrame([
                    {"finding_type": kind, **values}
                    for kind, values in sorted(finding_types.items())
                ]),
                hide_index=True,
                width="stretch",
            )

    def render_fn_breakdown(title: str, fn: dict[str, Any] | None) -> None:
        st.subheader(title)
        if fn is None:
            st.warning("False-negative detail is unavailable.")
            return
        st.metric("False negatives", fn.get("false_negative_count", "N/A"))
        for label, key in (
            ("Claim type", "by_claim_type"),
            ("Agent behavior", "by_agent_behaviour"),
            ("Scenario", "by_scenario"),
            ("Failure reason", "by_failure_reason"),
        ):
            rows = fn.get(key, [])
            if rows:
                st.markdown(f"**{label}**")
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    render_fn_breakdown(
        "Stage 8 expanded-corpus false-negative breakdown",
        stage8.get("false_negative_analysis"),
    )
    render_fn_breakdown(
        "Stage 7 baseline false-negative breakdown",
        stage8.get("stage7_false_negative_analysis"),
    )

    st.subheader("TAMAS integration — record-level status only")
    tamas = stage8.get("tamas_integration")
    if tamas is None:
        st.warning("TAMAS integration summary is unavailable.")
    else:
        st.info(
            f"Detection status: {tamas.get('detection_status', 'Not available')}. "
            "TAMAS labels are record/task-level and are not per-agent or per-assertion truth."
        )
        stats = tamas.get("dataset_statistics", {})
        trace = tamas.get("execution_summary", {})
        columns = st.columns(4)
        columns[0].metric("Records", stats.get("records", "N/A"))
        columns[1].metric("Benign records", stats.get("benign_records", "N/A"))
        columns[2].metric("Adversarial records", stats.get("adversarial_records", "N/A"))
        columns[3].metric("Existing traces", trace.get("traces_loaded", "N/A"))
        columns = st.columns(3)
        columns[0].metric("Claims available", tamas.get("claims_available", "N/A"))
        columns[1].metric("Detector findings", tamas.get("detector_findings", "N/A"))
        insufficient = tamas.get("records_with_insufficient_evidence")
        columns[2].metric(
            "Records with insufficient evidence",
            insufficient if insufficient is not None else "Not measurable",
        )
        st.write("Record-level attack categories:", stats.get("attack_counts", {}))
        st.write(
            "Available annotations:",
            tamas.get("available_annotations", {}),
        )
        st.caption(tamas.get("claim_availability_note", ""))


def _render_trust_aware_routing(data: dict[str, Any]) -> None:
    stage9 = data["stage9"]
    manifest = stage9.get("manifest")
    routing = stage9.get("routing_evaluation")
    detector_cross = stage9.get("detector_and_cross_message_evaluation")
    decisions = stage9.get("decisions", [])
    if manifest is None or routing is None or detector_cross is None:
        st.warning(
            "Stage 9 routing outputs are unavailable. Run "
            "`python run_stage9_routing_experiment.py` to generate them."
        )
        return

    st.caption(
        "Synthetic controlled experiments · deterministic scripted agents · "
        "local simulation only. Evaluator-sidecar outcomes below are post-routing "
        "evaluation inputs, never router inputs."
    )
    st.info(
        f"{manifest.get('tasks_per_strategy', 'N/A')} task runs per strategy across "
        f"{len(manifest.get('seeds', []))} paired seed/configuration rotations and "
        f"{manifest.get('agent_count', 'N/A')} scripted agents. "
        "No LLM or external tool execution is claimed."
    )

    comparison = stage9["csvs"].get("strategy_comparison.csv")
    if comparison is not None and not comparison.empty:
        st.subheader("Routing and task outcome comparison")
        st.dataframe(comparison, hide_index=True, width="stretch")
    else:
        st.warning("The Stage 9 strategy comparison CSV is unavailable or empty.")

    per_seed = routing.get("results_by", {}).get("seed", {})
    if per_seed:
        st.subheader("Task results by seed/configuration — all strategies")
        seed_rows = [
            {"seed": seed, **metrics}
            for seed, metrics in sorted(per_seed.items())
        ]
        st.dataframe(pd.DataFrame(seed_rows), hide_index=True, width="stretch")

    selected_strategy = st.selectbox(
        "Inspect strategy",
        options=list(manifest.get("strategies", [])),
        index=(
            list(manifest.get("strategies", [])).index("trust_aware")
            if "trust_aware" in manifest.get("strategies", [])
            else 0
        ),
        key="stage9_strategy",
    )
    strategy_groups = routing.get("results_by_strategy", {}).get(
        selected_strategy, {}
    )
    detailed_rows = [
        {"dimension": dimension, "group": group, **metrics}
        for dimension, groups in strategy_groups.items()
        for group, metrics in groups.items()
    ]
    if detailed_rows:
        st.subheader(f"{selected_strategy} task results by seed and scenario")
        st.dataframe(
            pd.DataFrame(detailed_rows),
            hide_index=True,
            width="stretch",
        )
    selected_decisions = [
        item for item in decisions if item.get("strategy") == selected_strategy
    ]
    st.subheader(f"{selected_strategy} routing decisions")
    st.dataframe(
        pd.DataFrame([
            {
                "seed": item.get("seed"),
                "scenario": item.get("scenario"),
                "task_id": item.get("task_id"),
                "selected_agent": item.get("selected_agent"),
                "eligible_agents": ", ".join(item.get("eligible_agents", [])),
                "excluded_agents": item.get("excluded_agents", []),
                "selection_rationale": item.get("selection_rationale"),
                "handoffs": item.get("handoffs"),
                "simulated_tool_invocations": item.get("simulated_tool_invocations"),
            }
            for item in selected_decisions
        ]),
        hide_index=True,
        width="stretch",
    )

    trust_rows = []
    for decision in selected_decisions:
        score_map = (
            decision.get("trust_scores", {})
            if selected_strategy == "trust_aware"
            else decision.get("trust_scores_diagnostic_only", {})
        )
        for agent_id, details in score_map.items():
            components = details.get("components", {})
            trust_rows.append({
                "seed": decision.get("seed"),
                "scenario": decision.get("scenario"),
                "task_id": decision.get("task_id"),
                "agent_id": agent_id,
                "trust_score": details.get("score"),
                "raw_score": details.get("raw_score"),
                "evaluated_assertions": details.get("evaluated_assertions"),
                "cold_start": details.get("cold_start"),
                "evidence_coverage": decision.get("evidence_coverage", {}).get(agent_id),
                "historical_insufficient_evidence": details.get(
                    "historical_detector_outcomes", {}
                ).get("INSUFFICIENT_EVIDENCE", 0),
                "historical_contradicted": details.get(
                    "historical_detector_outcomes", {}
                ).get("CONTRADICTED", 0),
                "identity_checks": details.get("identity_checks"),
                "identity_verified": details.get("identity_verified"),
                "cross_message_findings": details.get("cross_message_findings"),
                "capability_compatibility": (
                    decision.get("capability_compatibility", {}).get(agent_id)
                ),
                "selected": agent_id == decision.get("selected_agent"),
                **{
                    f"component_{name}": value
                    for name, value in components.items()
                },
            })
    if trust_rows:
        st.subheader("Trust scores and score components")
        if selected_strategy != "trust_aware":
            st.caption(
                "For R0–R8, these trust scores are diagnostics only; legacy selection "
                "uses its own unchanged profile/posterior policy."
            )
        st.dataframe(pd.DataFrame(trust_rows), hide_index=True, width="stretch")

    evaluation_rows = [
        row
        for row in routing.get("task_results", [])
        if row.get("strategy") == selected_strategy
    ]
    if evaluation_rows:
        st.subheader("Independent post-routing behavior analysis")
        st.caption(
            "Behavior labels and false-assertion counts are read from evaluator "
            "sidecars only after selection; they are not trust inputs."
        )
        st.dataframe(pd.DataFrame([
            {
                "seed": row.get("seed"),
                "scenario": row.get("scenario"),
                "selected_behavior": row.get("selected_behavior"),
                "task_success": row.get("task_success"),
                "selected_false_assertions": row.get("selected_false_assertions"),
                "selected_task_local_simulated_capability_demonstrated": row.get(
                    "selected_task_local_simulated_capability_demonstrated"
                ),
                "predecision_verified_capability_demonstrated": row.get(
                    "selected_predecision_verified_capability_demonstrated"
                ),
                "selected_evidence_coverage": row.get("selected_evidence_coverage"),
                "selected_prior_insufficient_evidence_assertions": row.get(
                    "selected_prior_insufficient_evidence_assertions"
                ),
            }
            for row in evaluation_rows
        ]), hide_index=True, width="stretch")

    by_strategy = detector_cross.get("by_strategy", {})
    strategy_eval = by_strategy.get(selected_strategy)
    if strategy_eval:
        detector_metrics = strategy_eval.get("detector", {})
        st.subheader("Stage 4 detector — separate assertion-level evaluation")
        st.caption(
            "Only CONTRADICTED is a positive binary prediction; this is not routing "
            "or task-completion accuracy."
        )
        columns = st.columns(4)
        for column, label, key in zip(
            columns,
            ("TP", "FP", "TN", "FN"),
            ("true_positives", "false_positives", "true_negatives", "false_negatives"),
        ):
            column.metric(label, detector_metrics.get(key, "N/A"))
        outcomes = detector_metrics.get("detector_outcome_counts", {})
        st.markdown("#### Four-outcome detector distribution")
        st.dataframe(pd.DataFrame([
            {"outcome": outcome, "assertions": outcomes.get(outcome, 0)}
            for outcome in OUTCOMES
        ]), hide_index=True, width="stretch")

        detector_groups = strategy_eval.get("detector_results_by", {})
        st.markdown("#### Detector false negatives by claim type")
        claim_groups = detector_groups.get("claim_type", {})
        if claim_groups:
            st.dataframe(pd.DataFrame([
                {"claim_type": group, **metrics}
                for group, metrics in sorted(claim_groups.items())
            ]), hide_index=True, width="stretch")
        else:
            st.info("Claim-type breakdown is unavailable.")

    st.subheader("Stage 7D cross-message evaluation — separate")
    cross_rows = []
    for strategy, result in by_strategy.items():
        metrics = result.get("cross_message", {}).get("metrics", {})
        cross_rows.append({
            "strategy": strategy,
            "TP": metrics.get("true_positives"),
            "FP": metrics.get("false_positives"),
            "FN": metrics.get("false_negatives"),
            "TN": metrics.get("true_negatives"),
            "precision": metrics.get("precision"),
            "recall": metrics.get("recall"),
            "F1": metrics.get("f1_score"),
        })
    if cross_rows:
        st.caption(
            "Cross-message findings are independently checked against eligible "
            "sidecar contradiction patterns. TN is undefined because no finite "
            "absence universe exists."
        )
        st.dataframe(pd.DataFrame(cross_rows), hide_index=True, width="stretch")
        finding_types = (
            strategy_eval.get("cross_message", {}).get("results_by_finding_type", {})
            if strategy_eval
            else {}
        )
        if finding_types:
            st.markdown(f"#### {selected_strategy} findings by type")
            st.dataframe(pd.DataFrame([
                {"finding_type": kind, **metrics}
                for kind, metrics in sorted(finding_types.items())
            ]), hide_index=True, width="stretch")

    st.subheader("TAMAS integration status — record-level only")
    tamas_status = manifest.get("tamas_status", "INSUFFICIENT_EVIDENCE")
    st.info(
        f"{tamas_status}. TAMAS labels and agent annotations are not used in "
        "routing. The existing Finance sample has no genuine claim-bearing agent "
        "execution; no per-agent TAMAS routing metric is available."
    )
    st.caption(
        "Actual trust evidence is limited to authenticated runtime evidence and "
        "detector findings. Tool invocations in this Stage 9 corpus are local "
        "simulations; selected capability evidence is not external execution."
    )


def _render_app() -> None:
    st.set_page_config(
        page_title="PROVENANCEGUARD",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        :root { color-scheme: dark; }
        .stApp { background: radial-gradient(ellipse at 12% 0%, #14223c 0, #0b1423 38%, #08111d 100%); color: #e8f0fa; }
        section[data-testid="stSidebar"] { width: 18rem !important; min-width: 18rem !important; }
        section[data-testid="stSidebar"] > div { width: 18rem !important; }
        [data-testid="stSidebar"] { background: linear-gradient(180deg, #0d192a 0%, #0a1321 100%); border-right: 1px solid rgba(115,151,195,.17); }
        [data-testid="stHeader"] { background: rgba(8,17,29,.72); }
        .block-container { max-width: 1540px; padding-top: 1.5rem; padding-bottom: 3rem; }
        h1, h2, h3 { color: #eef5ff; letter-spacing: -.025em; }
        p, label, [data-testid="stCaptionContainer"] { color: #a9bad0; }
        .hero { border: 1px solid rgba(90,158,230,.24); border-radius: 18px; padding: 1.45rem 1.7rem; margin-bottom: 1.15rem; background: linear-gradient(110deg, rgba(28,66,120,.5), rgba(16,35,59,.55) 60%, rgba(12,26,43,.75)); box-shadow: 0 16px 42px rgba(0,0,0,.18); }
        .hero h1 { font-size: clamp(2.1rem, 4vw, 3.2rem); margin: .25rem 0 0; font-weight: 780; letter-spacing: .035em; }
        .hero h1 span { color: #4fc4ed; }
        .hero p { margin: .3rem 0 0; font-size: 1rem; }
        .eyebrow { color: #67b9ff; font-size: .72rem; font-weight: 750; letter-spacing: .18em; }
        .metric-card { border: 1px solid rgba(129,158,194,.18); border-radius: 14px; padding: 1rem 1.05rem .9rem; min-height: 112px; margin-bottom: .75rem; background: linear-gradient(145deg, rgba(20,37,59,.95), rgba(12,25,42,.92)); box-shadow: 0 10px 24px rgba(0,0,0,.14); }
        .metric-card.cyan { border-top: 2px solid #45c7ec; }
        .metric-card.blue { border-top: 2px solid #6288ff; }
        .metric-card.green { border-top: 2px solid #28c98b; }
        .metric-card.red { border-top: 2px solid #ff5d70; }
        .metric-card.orange { border-top: 2px solid #f0a74a; }
        .metric-label { color: #9eb2cc; font-size: .78rem; font-weight: 650; letter-spacing: .055em; text-transform: uppercase; }
        .metric-value { color: #f0f6ff; font-size: 1.75rem; font-weight: 760; margin-top: .35rem; line-height: 1.1; }
        .metric-detail { color: #8095b0; font-size: .72rem; margin-top: .35rem; }
        [data-testid="stDataFrame"], [data-testid="stTable"] { border: 1px solid rgba(129,158,194,.14); border-radius: 10px; }
        div.stButton > button[kind="primary"], button[kind="primaryFormSubmit"] { background: linear-gradient(90deg, #426df4, #27aeda); border: none; color: white; border-radius: 9px; font-weight: 700; }
        [data-testid="stAlert"] { border-radius: 10px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown(
            '<div style="font-size:1.15rem;font-weight:800;letter-spacing:.07em;color:#eaf3ff">'
            'PROVENANCE<span style="color:#49c2e9">GUARD</span></div>'
            '<div style="font-size:.77rem;color:#91a7c2;margin:.25rem 0 1.1rem">'
            'Research monitoring console</div>',
            unsafe_allow_html=True,
        )
        page = st.radio(
            "Navigation",
            (
                
                "Experiment Runner",
                "Agent Monitoring",
                "Provenance Investigation",
                "Detection Results",
                "TAMAS Dataset",
                "Routing Analysis",
                "Stage 8 Evaluation",
                "Trust-Aware Routing Evaluation",
            ),
            label_visibility="collapsed",
        )
        st.divider()
        if st.button("Refresh project data", width="stretch"):
            st.rerun()
        st.caption("Local files only · no external tool execution")
        st.caption(f"Workspace: `{ROOT.name}`")

    data = load_dashboard_data()
    renderers = {
        "Agent Monitoring": _render_agent_monitoring,
        "TAMAS Dataset": _render_tamas,
        "Routing Analysis": _render_routing,
        "Stage 8 Evaluation": _render_stage8,
        "Trust-Aware Routing Evaluation": _render_trust_aware_routing,
    }
    if page == "Experiment Runner":
        st.title(page)
        _render_experiment_runner()
    elif page == "Provenance Investigation":
        st.title(page)
        _render_provenance_investigation(data)
    elif page == "Detection Results":
        st.title(page)
        _report_findings_view(data)
    else:
        renderers[page](data)


def _version_label(value: Any) -> str:
    try:
        version = int(value)
    except (TypeError, ValueError, OverflowError):
        return f"Version {value}"
    return VERSION_NAMES.get(version, f"Version {version}")


if __name__ == "__main__":
    _render_app()
