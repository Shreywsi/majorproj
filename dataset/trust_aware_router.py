"""Capability-gated deterministic routing using decision-time trust history."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from dataset.agent_trust_scoring import (
    AgentTrustHistory,
    TrustScore,
    TrustWeights,
    score_agent_trust,
)


@dataclass(frozen=True)
class RoutingTask:
    task_id: str
    decision_sequence: int
    required_role: str
    required_capability: str
    execution_mode: str

    def __post_init__(self) -> None:
        for field in ("task_id", "required_role", "required_capability"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty.")
        if (
            not isinstance(self.decision_sequence, int)
            or isinstance(self.decision_sequence, bool)
            or self.decision_sequence < 0
        ):
            raise ValueError("decision_sequence must be a non-negative integer.")
        if self.execution_mode not in {"local_simulation", "external"}:
            raise ValueError("execution_mode must be local_simulation or external.")


@dataclass(frozen=True)
class AgentCandidate:
    """Router-visible runtime profile; evaluator-only values have no fields here."""

    agent_id: str
    eligible_roles: tuple[str, ...]
    declared_capabilities: tuple[str, ...]
    identity_status: str = "unverified"
    self_reported_execution_rate: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise ValueError("agent_id must be non-empty.")
        if not isinstance(self.eligible_roles, tuple) or not self.eligible_roles:
            raise ValueError("eligible_roles must be a non-empty tuple.")
        if not isinstance(self.declared_capabilities, tuple):
            raise ValueError("declared_capabilities must be a tuple.")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in self.eligible_roles
        ):
            raise ValueError("eligible_roles cannot contain empty values.")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in self.declared_capabilities
        ):
            raise ValueError("declared_capabilities cannot contain empty values.")
        if self.identity_status not in {"verified", "unverified", "mismatch"}:
            raise ValueError("identity_status must be verified, unverified, or mismatch.")
        if (
            not isinstance(self.self_reported_execution_rate, (int, float))
            or isinstance(self.self_reported_execution_rate, bool)
            or not 0.0 <= self.self_reported_execution_rate <= 1.0
        ):
            raise ValueError("self_reported_execution_rate must be in [0, 1].")


@dataclass(frozen=True)
class RoutingDecision:
    task_id: str
    decision_sequence: int
    eligible_agents: tuple[str, ...]
    excluded_agents: tuple[dict[str, str], ...]
    trust_scores: Mapping[str, dict[str, object]]
    capability_compatibility: Mapping[str, str]
    evidence_coverage: Mapping[str, float | None]
    selected_agent: str
    selection_rationale: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class TrustAwareRouter:
    """Route only to role/capability-compatible candidates and explain each choice."""

    def __init__(
        self,
        *,
        weights: TrustWeights = TrustWeights(),
        minimum_observations: int = 3,
    ):
        self.weights = weights
        self.minimum_observations = minimum_observations
        if (
            not isinstance(minimum_observations, int)
            or isinstance(minimum_observations, bool)
            or minimum_observations < 1
        ):
            raise ValueError("minimum_observations must be a positive integer.")

    def route(
        self,
        task: RoutingTask,
        candidates: tuple[AgentCandidate, ...],
        histories: Mapping[str, AgentTrustHistory],
    ) -> RoutingDecision:
        if not isinstance(task, RoutingTask):
            raise TypeError("task must be a RoutingTask.")
        if not isinstance(candidates, tuple) or not candidates:
            raise ValueError("candidates must be a non-empty tuple.")
        if any(not isinstance(item, AgentCandidate) for item in candidates):
            raise ValueError("candidates must contain AgentCandidate values.")
        ids = [candidate.agent_id for candidate in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate agent IDs must be unique.")
        if not isinstance(histories, Mapping):
            raise TypeError("histories must be a mapping.")
        if any(
            not isinstance(history, AgentTrustHistory)
            or key != history.agent_id
            for key, history in histories.items()
        ):
            raise ValueError("history keys must match their AgentTrustHistory IDs.")

        excluded: list[dict[str, str]] = []
        compatible: list[tuple[AgentCandidate, TrustScore, str, int]] = []
        capability_compatibility: dict[str, str] = {}
        scores: dict[str, dict[str, object]] = {}
        coverage: dict[str, float | None] = {}

        for candidate in candidates:
            history = histories.get(candidate.agent_id, AgentTrustHistory(candidate.agent_id))
            score = score_agent_trust(
                history,
                weights=self.weights,
                minimum_observations=self.minimum_observations,
            )
            scores[candidate.agent_id] = {
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
            reasons = []
            if task.required_role not in candidate.eligible_roles:
                reasons.append("required role is not eligible")
            if task.required_capability not in candidate.declared_capabilities:
                reasons.append("required capability is not declared")
            if candidate.identity_status == "mismatch":
                reasons.append("runtime identity binding is mismatched")
            if reasons:
                excluded.append(
                    {"agent_id": candidate.agent_id, "reason": "; ".join(reasons)}
                )
                capability_compatibility[candidate.agent_id] = "incompatible"
                continue

            proof_kinds = {
                observation.evidence_kind
                for observation in history.capabilities
                if observation.capability == task.required_capability
                and observation.verified
            }
            if task.execution_mode == "local_simulation":
                demonstrated = (
                    "actual_demonstration" in proof_kinds
                    or "simulated_demonstration" in proof_kinds
                )
                compatibility = (
                    "verified_simulated_or_actual_demonstration"
                    if demonstrated
                    else "configured_permission_cold_start_fallback"
                )
            else:
                demonstrated = "actual_demonstration" in proof_kinds
                compatibility = (
                    "verified_actual_demonstration"
                    if demonstrated
                    else "configured_permission_cold_start_fallback"
                )
            capability_compatibility[candidate.agent_id] = compatibility
            compatible.append(
                (candidate, score, compatibility, int(demonstrated))
            )

        if not compatible:
            raise ValueError(
                f"No eligible agent can satisfy task {task.task_id!r}."
            )

        # A verified demonstration is a capability priority, never an identity
        # or truth guarantee. If none exists, permission-compatible agents remain
        # eligible under the explicit cold-start fallback policy.
        selected, selected_score, selected_compatibility, _ = min(
            compatible,
            key=lambda row: (
                -row[3],
                -row[1].score,
                row[0].agent_id,
            ),
        )
        rationale = (
            f"Selected {selected.agent_id}: capability tier "
            f"{selected_compatibility}, trust score {selected_score.score:.4f}; "
            "ties are resolved by stable agent ID. A local simulated demonstration "
            "is not evidence of external execution."
        )
        return RoutingDecision(
            task_id=task.task_id,
            decision_sequence=task.decision_sequence,
            eligible_agents=tuple(sorted(candidate.agent_id for candidate, *_ in compatible)),
            excluded_agents=tuple(sorted(excluded, key=lambda item: item["agent_id"])),
            trust_scores=scores,
            capability_compatibility=capability_compatibility,
            evidence_coverage=coverage,
            selected_agent=selected.agent_id,
            selection_rationale=rationale,
        )
