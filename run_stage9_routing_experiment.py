"""Run the paired Stage 9 routing comparison on controlled local simulations."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np

from agents import Profile
from config import Config
from crypto_ledger import EvidenceAuthority, EvidenceVerifier
from dataset.agent_trust_scoring import (
    AgentTrustHistory,
    CapabilityObservation,
    merge_counts,
    score_agent_trust,
)
from dataset.claim_bearing_agents import (
    AgentBehaviour,
    ScenarioName,
    run_claim_bearing_experiment,
    scenario_details,
)
from dataset.claim_message_schemas import (
    ClaimBearingAssertion,
    ProvenanceClaimType,
    claim_message_to_dict,
)
from dataset.experiment_schemas import EventExecutionStatus, TraceEventType
from dataset.runtime_evidence import (
    EvidenceCategory,
    EvidenceVerificationStatus,
    RuntimeEvidence,
)
from dataset.stage8_analysis import evaluate_cross_message_findings
from dataset.stage9_routing_evaluator import evaluate_routing_decisions
from dataset.trace_store import trace_to_json
from dataset.trust_aware_router import (
    AgentCandidate,
    RoutingTask,
    TrustAwareRouter,
)
from provenance_detector import ClaimOutcome
from router import Router
from run_claim_bearing_detection import detect_claim_bearing_experiment
from run_claim_bearing_experiment import write_detector_outputs, write_evaluator_sidecar
from run_claim_bearing_evaluation import _write_outputs as write_stage7_evaluation
from dataset.independent_evaluator import evaluate_claim_bearing_experiments


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "controlled" / "stage9_routing_20261107_final_v3"
STRATEGIES = tuple(f"R{version}" for version in range(9)) + ("trust_aware",)
BEHAVIOUR_ORDER = tuple(AgentBehaviour)
SCENARIO_ORDER = tuple(ScenarioName)
OUTCOME_VALUES = {
    ClaimOutcome.SUPPORTED.value: 1.0,
    ClaimOutcome.CONTRADICTED.value: 0.0,
    ClaimOutcome.UNSUPPORTED.value: 0.5,
    ClaimOutcome.INSUFFICIENT_EVIDENCE.value: 0.5,
}


def _json_write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _jsonl_write(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                       allow_nan=False)
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _stable_agent_ids(namespace: str, agent_count: int = 5) -> tuple[str, ...]:
    return tuple(
        f"agent-{_short_hash(namespace)}-{index:02d}"
        for index in range(agent_count)
    )


def _rotated_conditions(seed: int) -> tuple[
    tuple[AgentBehaviour, ...], tuple[ScenarioName, ...]
]:
    offset = seed % len(BEHAVIOUR_ORDER)
    behaviours = tuple(
        BEHAVIOUR_ORDER[(index + offset) % len(BEHAVIOUR_ORDER)]
        for index in range(len(BEHAVIOUR_ORDER))
    )
    scenarios = tuple(
        SCENARIO_ORDER[(index + offset) % len(SCENARIO_ORDER)]
        for index in range(len(SCENARIO_ORDER))
    )
    return behaviours, scenarios


def _candidate_profiles(
    agent_ids: tuple[str, ...],
    required_capability: str,
    histories: dict[str, AgentTrustHistory],
    self_claim_counts: dict[str, list[int]],
) -> tuple[AgentCandidate, ...]:
    candidates = []
    for agent_id in agent_ids:
        history = histories[agent_id]
        claims, claimed_recorded = self_claim_counts[agent_id]
        claim_rate = (
            (claimed_recorded + 1) / (claims + 2)
            if claims
            else 0.5
        )
        candidates.append(
            AgentCandidate(
                agent_id=agent_id,
                eligible_roles=("operator",),
                declared_capabilities=(required_capability,),
                identity_status=(
                    "verified"
                    if history.identity_checks and history.identity_verified
                    else "unverified"
                ),
                self_reported_execution_rate=claim_rate,
            )
        )
    return tuple(candidates)


def _capability_compatibility(
    candidate: AgentCandidate,
    history: AgentTrustHistory,
    required_capability: str,
) -> str:
    proof_kinds = {
        observation.evidence_kind
        for observation in history.capabilities
        if observation.capability == required_capability and observation.verified
    }
    if "actual_demonstration" in proof_kinds:
        return "verified_actual_demonstration"
    if "simulated_demonstration" in proof_kinds:
        return "verified_simulated_or_actual_demonstration"
    if required_capability in candidate.declared_capabilities:
        return "configured_permission_cold_start_fallback"
    return "incompatible"


def _legacy_evidence(
    authority: EvidenceAuthority,
    *,
    agent_id: str,
    task_type: int,
    value: float,
    sequence: int,
    strategy: str,
    seed: int,
) -> dict[str, Any]:
    return authority.attest(
        agent_id,
        task_type,
        value,
        sequence,
        nonce=f"stage9-{seed}-{strategy}-{sequence}-{agent_id}",
    )


def _legacy_router(strategy: str, seed: int) -> tuple[Router, EvidenceAuthority]:
    version = int(strategy[1:])
    config = Config(N=5, T=5, K=6, eps=0.05, real_crypto=True)
    authority_seed = hashlib.sha256(
        f"stage9-attestation|{seed}|{strategy}".encode("utf-8")
    ).digest()
    authority = EvidenceAuthority(authority_seed)
    router = Router(
        version,
        config,
        np.random.default_rng(seed),
        EvidenceVerifier(authority.vk),
    )
    return router, authority


def _choose_baseline(
    *,
    strategy: str,
    router: Router,
    agent_ids: tuple[str, ...],
    candidates: tuple[AgentCandidate, ...],
    task_type: int,
    sequence: int,
    histories: dict[str, AgentTrustHistory],
) -> tuple[int, dict[str, Any]]:
    profiles = [
        Profile(
            claim=candidate.self_reported_execution_rate,
            key=f"stage9-key-{index}",
            text=f"Declared task capability: {candidate.declared_capabilities[0]}",
        )
        for index, candidate in enumerate(candidates)
    ]
    router.observe_profiles(agent_ids, profiles, sequence)
    strategy_scores = router.score_all(
        agent_ids, task_type, sequence, profiles
    )
    selected_index, is_probe = router.select(strategy_scores, list(agent_ids))
    score_map = {
        candidate.agent_id: float(strategy_scores[index])
        for index, candidate in enumerate(candidates)
    }
    trust_map = {}
    coverage = {}
    for candidate in candidates:
        score = score_agent_trust(histories[candidate.agent_id])
        trust_map[candidate.agent_id] = {
            "score": score.score,
            "raw_score": score.raw_score,
            "components": dict(score.components),
            "evaluated_assertions": score.evaluated_assertions,
            "confidence": score.confidence,
            "cold_start": score.cold_start,
            "historical_detector_outcomes": dict(
                score.historical_outcome_counts
            ),
            "historical_evidence_status_counts": dict(
                score.historical_evidence_status_counts
            ),
            "identity_checks": score.identity_checks,
            "identity_verified": score.identity_verified,
            "cross_message_checks": score.cross_message_checks,
            "cross_message_findings": score.cross_message_findings,
        }
        coverage[candidate.agent_id] = score.evidence_coverage
    rationale = {
        "R0": "Uniform/random selection over the common eligible candidate pool.",
        "R1": "Select by smoothed prior self-reported execution claims.",
    }.get(strategy, "Select using the unchanged legacy Router posterior and policy.")
    if is_probe:
        rationale += " Legacy router marked this selection as exploration/probe."
    return selected_index, {
        "eligible_agents": list(agent_ids),
        "excluded_agents": [],
        "strategy_scores": score_map,
        "trust_scores_diagnostic_only": trust_map,
        "evidence_coverage": coverage,
        "selection_rationale": rationale,
        "is_probe": bool(is_probe),
    }


def _update_history(
    prior: AgentTrustHistory,
    agent_id: str,
    detection: dict[str, Any],
    assertions: tuple[ClaimBearingAssertion, ...],
    evidence_records: tuple[RuntimeEvidence, ...],
) -> AgentTrustHistory:
    findings = [
        finding
        for finding in detection["findings"]
        if finding["agent_id"] == agent_id
    ]
    outcomes = Counter(finding["detector_outcome"] for finding in findings)
    evidence_statuses: Counter[str] = Counter()
    assertions_with_evidence = 0
    identity_checks = 0
    identity_verified = 0
    finding_by_assertion = {
        finding["assertion_id"]: finding for finding in findings
    }
    for finding in findings:
        verification = finding.get("evidence_verification", [])
        associations = finding.get("evidence_association", [])
        for item in verification:
            evidence_statuses[item["status"]] += 1
        valid_associations = [
            item for item in associations if item["status"] == "valid"
        ]
        if valid_associations:
            assertions_with_evidence += 1
        evidence_statuses["association_mismatch"] += sum(
            item["status"] != "valid" for item in associations
        )
        if finding["claim_type"] == ProvenanceClaimType.IDENTITY.value:
            identity_checks += 1
            if any(item["status"] == "valid" for item in verification):
                identity_verified += 1

    cross_agent = [
        finding
        for finding in detection["cross_message_findings"]
        if finding.get("agent_id") == agent_id
    ]
    capabilities = list(prior.capabilities)
    evidence_by_id = {record.evidence_id: record for record in evidence_records}
    for assertion in assertions:
        if (
            assertion.agent_id != agent_id
            or assertion.claim_type != ProvenanceClaimType.CAPABILITY
            or assertion.predicate != "demonstrated_capability"
            or not isinstance(assertion.asserted_value, dict)
            or assertion.asserted_value.get("demonstrated") is not True
        ):
            continue
        finding = finding_by_assertion.get(assertion.assertion_id)
        if finding is None:
            continue
        if any(
            item["status"] != "valid"
            for item in finding.get("evidence_association", [])
        ):
            continue
        runtime_observation = any(
            evidence_by_id[evidence_id].category == EvidenceCategory.TOOL_EXECUTION
            and evidence_by_id[evidence_id].execution_status
            == EventExecutionStatus.LOCAL_SIMULATION
            for evidence_id in assertion.evidence_refs
            if evidence_id in evidence_by_id
        )
        if runtime_observation:
            capabilities.append(
                CapabilityObservation(
                    capability=assertion.subject,
                    evidence_kind="simulated_demonstration",
                    verified=True,
                )
            )

    return AgentTrustHistory(
        agent_id=agent_id,
        outcome_counts=merge_counts(prior.outcome_counts, outcomes),
        evidence_status_counts=merge_counts(
            prior.evidence_status_counts, evidence_statuses
        ),
        assertions_with_evidence=(
            prior.assertions_with_evidence + assertions_with_evidence
        ),
        assertions_evaluated=prior.assertions_evaluated + len(findings),
        identity_checks=prior.identity_checks + identity_checks,
        identity_verified=prior.identity_verified + identity_verified,
        cross_message_checks=prior.cross_message_checks + 1,
        cross_message_findings=(
            prior.cross_message_findings + int(bool(cross_agent))
        ),
        capabilities=tuple(capabilities),
    )


def _update_self_claims(
    counters: dict[str, list[int]],
    assertions: tuple[ClaimBearingAssertion, ...],
) -> None:
    for assertion in assertions:
        if assertion.claim_type != ProvenanceClaimType.TOOL_USAGE:
            continue
        row = counters[assertion.agent_id]
        row[0] += 1
        value = assertion.asserted_value
        if isinstance(value, dict) and value.get("execution_status") == "RECORDED":
            row[1] += 1


def _selected_evidence_coverage(
    selected_agent: str,
    runtime_assertions: tuple[ClaimBearingAssertion, ...],
    detection: dict[str, Any],
) -> float | None:
    selected_assertions = [
        assertion
        for assertion in runtime_assertions
        if assertion.agent_id == selected_agent
    ]
    if not selected_assertions:
        return None
    findings = {row["assertion_id"]: row for row in detection["findings"]}
    covered = sum(
        bool(
            findings.get(assertion.assertion_id, {}).get("evidence_association")
            and all(
                item["status"] == EvidenceVerificationStatus.VALID.value
                for item in findings[assertion.assertion_id]["evidence_association"]
            )
        )
        for assertion in selected_assertions
    )
    return covered / len(selected_assertions)


def _write_detection_result(result: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _jsonl_write(directory / "detector_findings.jsonl", result["findings"])
    _jsonl_write(
        directory / "cross_message_findings.jsonl",
        result["cross_message_findings"],
    )
    summary = {
        key: value
        for key, value in result.items()
        if key not in {"findings", "cross_message_findings"}
    }
    _json_write(directory / "detection_summary.json", summary)


def _strategy_result(
    *,
    strategy: str,
    seed: int,
    behaviours: tuple[AgentBehaviour, ...],
    scenarios: tuple[ScenarioName, ...],
    namespace: str,
    output_root: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    agent_ids = _stable_agent_ids(namespace)
    histories = {agent_id: AgentTrustHistory(agent_id) for agent_id in agent_ids}
    self_claim_counts = {agent_id: [0, 0] for agent_id in agent_ids}
    trust_router = TrustAwareRouter()
    legacy: Router | None = None
    authority: EvidenceAuthority | None = None
    if strategy != "trust_aware":
        legacy, authority = _legacy_router(strategy, seed)

    decisions: list[dict[str, Any]] = []
    evaluator_sidecars: list[dict[str, Any]] = []
    cross_experiments: list[dict[str, Any]] = []
    for sequence, scenario in enumerate(scenarios):
        task_spec = scenario_details(scenario)
        candidates = _candidate_profiles(
            agent_ids, task_spec.required_capability, histories, self_claim_counts
        )
        task_type = SCENARIO_ORDER.index(scenario)
        if strategy == "trust_aware":
            routed = trust_router.route(
                RoutingTask(
                    task_id=f"pending-{seed}-{sequence}",
                    decision_sequence=sequence,
                    required_role=task_spec.required_role,
                    required_capability=task_spec.required_capability,
                    execution_mode="local_simulation",
                ),
                candidates,
                histories,
            )
            selected_index = agent_ids.index(routed.selected_agent)
            decision = routed.to_dict()
            decision["strategy"] = strategy
        else:
            if legacy is None or authority is None:
                raise RuntimeError("Legacy router setup is incomplete.")
            selected_index, decision = _choose_baseline(
                strategy=strategy,
                router=legacy,
                agent_ids=agent_ids,
                candidates=candidates,
                task_type=task_type,
                sequence=sequence,
                histories=histories,
            )
            decision.update({
                "strategy": strategy,
                "decision_sequence": sequence,
                "capability_compatibility": {
                    candidate.agent_id: _capability_compatibility(
                        candidate,
                        histories[candidate.agent_id],
                        candidate.declared_capabilities[0],
                    )
                    for candidate in candidates
                },
            })

        run = run_claim_bearing_experiment(
            seed=seed,
            scenarios=(scenario,),
            behaviours=behaviours,
            agents_per_task=len(agent_ids),
            selected_operator_index=selected_index,
            stable_agent_namespace=namespace,
            run_label=f"{strategy}-{seed}",
        )
        if len(run.traces) != 1:
            raise RuntimeError("Stage 9 expects one generated task per routing decision.")
        if tuple(
            sorted({assertion.agent_id for assertion in run.assertions})
        ) != tuple(sorted(agent_ids)):
            raise RuntimeError("Generated agent identities differ from the routing pool.")

        task_directory = (
            output_root
            / f"seed-{seed}"
            / strategy
            / f"task-{sequence:02d}-{scenario.value}"
        )
        source_directory = task_directory / "source"
        detection_directory = task_directory / "detection"
        source_directory.mkdir(parents=True, exist_ok=False)
        paths = write_detector_outputs(run, source_directory)
        write_evaluator_sidecar(
            run, source_directory / f"{run.experiment_id}.evaluator.jsonl"
        )
        detected = detect_claim_bearing_experiment(
            traces_path=paths["traces"],
            messages_path=paths["messages"],
            claims_path=paths["claims"],
            evidence_path=paths["evidence"],
            trust_policy_path=paths["trust"],
        )
        _write_detection_result(detected, detection_directory)
        cross_experiments.append({
            "experiment_id": run.experiment_id,
            "ground_truth_records": list(run.evaluator_sidecar),
            "messages": [claim_message_to_dict(message) for message in run.messages],
            "traces": {
                trace.trace_id: json.loads(trace_to_json(trace))
                for trace in run.traces
            },
            "cross_message_findings": detected["cross_message_findings"],
        })
        trace = run.traces[0]
        selected_agent = agent_ids[selected_index]
        decision.update({
            "task_id": trace.task_id,
            "experiment_id": run.experiment_id,
            "trace_id": trace.trace_id,
            "scenario": scenario.value,
            "seed": seed,
            "selected_agent": selected_agent,
            "routing_task_id": decision.get("task_id"),
            "handoffs": sum(
                event.event_type == TraceEventType.HANDOFF for event in trace.events
            ),
            "simulated_tool_invocations": run.simulated_tool_invocations,
            "selected_evidence_coverage": _selected_evidence_coverage(
                selected_agent, run.assertions, detected
            ),
            "detector_outcome_counts": detected["detector_outcome_counts"],
            "cross_message_finding_count": detected["cross_message_finding_count"],
            "detection_output_directory": str(detection_directory.relative_to(output_root)),
            "source_output_directory": str(source_directory.relative_to(output_root)),
        })
        if strategy == "trust_aware":
            decision["selection_rationale"] = routed.selection_rationale
        decisions.append(decision)
        evaluator_sidecars.extend(run.evaluator_sidecar)

        if legacy is not None and authority is not None and int(strategy[1:]) >= 2:
            findings_by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for finding in detected["findings"]:
                findings_by_agent[finding["agent_id"]].append(finding)
            for agent_id in agent_ids:
                agent_findings = findings_by_agent[agent_id]
                values = [
                    OUTCOME_VALUES[finding["detector_outcome"]]
                    for finding in agent_findings
                ]
                observed = sum(values) / len(values) if values else 0.5
                evidence = _legacy_evidence(
                    authority,
                    agent_id=agent_id,
                    task_type=task_type,
                    value=observed,
                    sequence=sequence,
                    strategy=strategy,
                    seed=seed,
                )
                if legacy.update(
                    agent_id,
                    task_type,
                    observed,
                    sequence,
                    evidence=evidence,
                ) is not True:
                    raise ValueError(
                        f"{strategy} rejected its detector-derived runtime observation."
                    )

        _update_self_claims(self_claim_counts, run.assertions)
        for agent_id in agent_ids:
            histories[agent_id] = _update_history(
                histories[agent_id],
                agent_id,
                detected,
                run.assertions,
                run.evidence,
            )

    return decisions, evaluator_sidecars, cross_experiments


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _tamas_integration_status() -> dict[str, Any]:
    source = (
        ROOT / "data" / "tamas" / "stage8_integration_20261107"
        / "tamas_integration_summary.json"
    )
    try:
        summary = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read existing TAMAS status report: {exc}") from exc
    if not isinstance(summary, dict) or not isinstance(
        summary.get("dataset_statistics"), dict
    ):
        raise ValueError("Existing TAMAS status report has an invalid shape.")
    stats = summary["dataset_statistics"]
    execution = summary.get("execution_summary", {})
    annotations = summary.get("available_annotations", {})
    return {
        "status": "INSUFFICIENT_EVIDENCE_NO_CLAIMS_AVAILABLE",
        "source_report": str(source.relative_to(ROOT)),
        "records": stats.get("records"),
        "benign_records": stats.get("benign_records"),
        "adversarial_records": stats.get("adversarial_records"),
        "record_level_attack_categories": stats.get("attack_counts"),
        "available_agent_tool_annotations": annotations,
        "existing_traces": execution.get("traces_loaded"),
        "existing_simulated_tool_invocations": execution.get(
            "simulated_tool_invocations"
        ),
        "claims_available": summary.get("claims_available"),
        "detector_findings": summary.get("detector_findings"),
        "real_claim_bearing_execution": summary.get("real_claim_bearing_execution"),
        "records_with_insufficient_evidence": summary.get(
            "records_with_insufficient_evidence"
        ),
        "per_agent_accuracy": None,
        "trust_aware_tamas_routing_performed": False,
        "router_used_record_labels": False,
        "limitations": [
            "TAMAS labels are record/task-level, not per-agent assertion truth.",
            "agents_involved is not evidence of agent dishonesty.",
            "Existing traces are local simulations without claim-bearing agent conversations.",
            "No claims or agent trust histories were fabricated.",
        ],
    }


def run_stage9_experiment(
    *,
    seeds: tuple[int, ...] = (9100, 9101, 9102, 9103, 9104),
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("seeds must be a non-empty tuple of unique integers.")
    if any(not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 for seed in seeds):
        raise ValueError("seeds must be non-negative integers.")
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(
            f"Stage 9 output directory already exists; choose a fresh path: {output_root}"
        )
    output_root.mkdir(parents=True)

    all_decisions: list[dict[str, Any]] = []
    all_sidecars: list[dict[str, Any]] = []
    strategy_evaluations: dict[str, dict[str, Any]] = {}
    strategy_cross_evaluations: dict[str, dict[str, Any]] = {}
    strategy_cross_experiments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    strategy_detection_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    strategy_cross_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    strategy_sidecars: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for strategy in STRATEGIES:
        for seed in seeds:
            behaviours, scenarios = _rotated_conditions(seed)
            namespace = f"stage9-roster-{seed}"
            decisions, sidecars, cross_experiments = _strategy_result(
                strategy=strategy,
                seed=seed,
                behaviours=behaviours,
                scenarios=scenarios,
                namespace=namespace,
                output_root=output_root,
            )
            all_decisions.extend(decisions)
            all_sidecars.extend(sidecars)
            strategy_sidecars[strategy].extend(sidecars)
            strategy_cross_experiments[strategy].extend(cross_experiments)
            for decision in decisions:
                detection_dir = output_root / decision["detection_output_directory"]
                findings_path = detection_dir / "detector_findings.jsonl"
                cross_path = detection_dir / "cross_message_findings.jsonl"
                strategy_detection_rows[strategy].extend(
                    json.loads(line)
                    for line in findings_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
                strategy_cross_rows[strategy].extend(
                    json.loads(line)
                    for line in cross_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )

    # Evaluator-only data enters only after every route and detector run is complete.
    for strategy in STRATEGIES:
        evaluation = evaluate_claim_bearing_experiments(
            strategy_detection_rows[strategy],
            strategy_cross_rows[strategy],
            strategy_sidecars[strategy],
        )
        strategy_evaluations[strategy] = evaluation
        strategy_cross_evaluations[strategy] = evaluate_cross_message_findings(
            experiments=strategy_cross_experiments[strategy]
        )
        write_stage7_evaluation(
            evaluation,
            output_root / "evaluation" / strategy,
        )

    routing_evaluation = evaluate_routing_decisions(
        all_decisions,
        all_sidecars,
        bootstrap_seed=9,
    )
    _jsonl_write(output_root / "routing_decisions.jsonl", all_decisions)
    _jsonl_write(
        output_root / "evaluator_sidecars" / "sidecars.jsonl",
        all_sidecars,
    )
    _json_write(
        output_root / "routing_evaluation.json",
        routing_evaluation,
    )
    _json_write(
        output_root / "detector_and_cross_message_evaluation.json",
        {
            "by_strategy": {
                strategy: {
                    "detector": evaluation["summary"]["assertion_metrics"],
                    "detector_results_by": evaluation["summary"]["results_by"],
                    "cross_message": strategy_cross_evaluations[strategy],
                    "evidence_verification_failure_counts": (
                        evaluation["summary"]["evidence_verification_failure_counts"]
                    ),
                    "evidence_association_failure_counts": (
                        evaluation["summary"]["evidence_association_failure_counts"]
                    ),
                }
                for strategy, evaluation in strategy_evaluations.items()
            },
            "interpretation": (
                "Stage 4 assertion metrics and Stage 7D cross-message metrics are "
                "separate from routing/task-completion metrics."
            ),
        },
    )
    strategy_csv_rows = []
    for strategy in STRATEGIES:
        route_metrics = routing_evaluation["strategy_metrics"][strategy]
        detector_metrics = strategy_evaluations[strategy]["summary"]["assertion_metrics"]
        strategy_csv_rows.append({
            "strategy": strategy,
            **route_metrics,
            "stage4_tp": detector_metrics["true_positives"],
            "stage4_fp": detector_metrics["false_positives"],
            "stage4_tn": detector_metrics["true_negatives"],
            "stage4_fn": detector_metrics["false_negatives"],
            "stage4_precision": detector_metrics["precision"],
            "stage4_recall": detector_metrics["recall"],
            "stage4_f1": detector_metrics["f1_score"],
            "stage4_accuracy": detector_metrics["accuracy"],
            "stage4_outcome_counts": json.dumps(
                detector_metrics["detector_outcome_counts"], sort_keys=True
            ),
        })
    _write_csv(
        output_root / "strategy_comparison.csv",
        strategy_csv_rows,
        [
            "strategy", "tasks", "task_successes", "task_failures",
            "task_success_rate", "handoffs", "simulated_tool_invocations",
            "selected_agents_with_false_assertions", "selected_false_assertions",
            "selected_assertions",
            "selected_task_local_simulated_capability_demonstrations",
            "selected_predecision_verified_capability_demonstrations",
            "selected_evidence_coverage",
            "selected_agents_with_prior_insufficient_evidence",
            "selected_prior_insufficient_evidence_assertions",
            "stage4_tp", "stage4_fp", "stage4_tn", "stage4_fn",
            "stage4_precision", "stage4_recall", "stage4_f1", "stage4_accuracy",
            "stage4_outcome_counts",
        ],
    )
    _write_csv(
        output_root / "routing_group_metrics.csv",
        [
            {"dimension": dimension, "group": group, **metrics}
            for dimension, groups in routing_evaluation["results_by"].items()
            for group, metrics in groups.items()
        ],
        [
            "dimension", "group", "tasks", "task_successes", "task_failures",
            "task_success_rate", "handoffs", "simulated_tool_invocations",
            "selected_agents_with_false_assertions", "selected_false_assertions",
            "selected_assertions",
            "selected_task_local_simulated_capability_demonstrations",
            "selected_predecision_verified_capability_demonstrations",
            "selected_evidence_coverage",
            "selected_agents_with_prior_insufficient_evidence",
            "selected_prior_insufficient_evidence_assertions",
        ],
    )
    _write_csv(
        output_root / "routing_strategy_group_metrics.csv",
        [
            {
                "strategy": strategy,
                "dimension": dimension,
                "group": group,
                **metrics,
            }
            for strategy, dimensions in routing_evaluation[
                "results_by_strategy"
            ].items()
            for dimension, groups in dimensions.items()
            for group, metrics in groups.items()
        ],
        [
            "strategy", "dimension", "group", "tasks", "task_successes",
            "task_failures", "task_success_rate", "handoffs",
            "simulated_tool_invocations",
            "selected_agents_with_false_assertions", "selected_false_assertions",
            "selected_assertions",
            "selected_task_local_simulated_capability_demonstrations",
            "selected_predecision_verified_capability_demonstrations",
            "selected_evidence_coverage",
            "selected_agents_with_prior_insufficient_evidence",
            "selected_prior_insufficient_evidence_assertions",
        ],
    )
    tamas_status = _tamas_integration_status()
    _json_write(output_root / "tamas_integration_status.json", tamas_status)
    manifest = {
        "stage": 9,
        "execution_mode": "deterministic scripted agents; controlled local simulation",
        "strategies": list(STRATEGIES),
        "seeds": list(seeds),
        "tasks_per_strategy": len(seeds) * len(SCENARIO_ORDER),
        "behavior_assignments_by_seed": {
            str(seed): [item.value for item in _rotated_conditions(seed)[0]]
            for seed in seeds
        },
        "scenario_order_by_seed": {
            str(seed): [item.value for item in _rotated_conditions(seed)[1]]
            for seed in seeds
        },
        "agent_count": 5,
        "weights": {
            "assertion_reliability": 0.40,
            "evidence_integrity": 0.20,
            "identity_binding": 0.15,
            "cross_message_consistency": 0.15,
            "evidence_coverage": 0.10,
        },
        "legacy_adapter_signal_mapping": {
            "R0": "unchanged legacy random policy",
            "R1": "smoothed rate of prior self-reported RECORDED tool execution",
            "R2-R8": (
                "unchanged legacy policies updated with system-attested detector "
                "outcome means: SUPPORTED=1, CONTRADICTED=0, "
                "UNSUPPORTED/INSUFFICIENT_EVIDENCE=0.5"
            ),
            "attestation_limit": (
                "The signature authenticates the detector-derived summary, not "
                "objective truth."
            ),
        },
        "stage8_results_preserved": True,
        "tamas_status": tamas_status["status"],
        "output_root": str(output_root),
    }
    _json_write(output_root / "manifest.json", manifest)
    return {
        "output_root": output_root,
        "manifest": manifest,
        "routing_evaluation": routing_evaluation,
        "strategy_evaluations": strategy_evaluations,
    }


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--seeds", type=int, nargs="+", default=[9100, 9101, 9102, 9103, 9104])
    args = parser.parse_args(argv)
    try:
        result = run_stage9_experiment(
            seeds=tuple(args.seeds),
            output_root=args.out_dir,
        )
    except (OSError, ValueError, TypeError) as exc:
        print(f"Stage 9 experiment failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output_root": str(result["output_root"]),
        "strategies": result["manifest"]["strategies"],
        "tasks_per_strategy": result["manifest"]["tasks_per_strategy"],
    }, indent=2, sort_keys=True))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
