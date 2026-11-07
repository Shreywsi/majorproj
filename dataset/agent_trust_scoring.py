"""Transparent assertion-history scoring for decision-time agent selection."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from typing import Mapping

from provenance_detector import ClaimOutcome


_EVIDENCE_STATUSES = frozenset(
    {
        "valid",
        "missing",
        "invalid_signature",
        "untrusted_issuer",
        "category_not_authorized",
        "expired",
        "future_dated",
        "stale",
        "replayed",
        "identity_mismatch",
        "identity_unregistered",
        "scope_mismatch",
        "simulated_execution",
        "association_mismatch",
    }
)


@dataclass(frozen=True)
class TrustWeights:
    """Fixed, configurable component weights; defaults are not data-tuned."""

    assertion_reliability: float = 0.40
    evidence_integrity: float = 0.20
    identity_binding: float = 0.15
    cross_message_consistency: float = 0.15
    evidence_coverage: float = 0.10

    def __post_init__(self) -> None:
        values = (
            self.assertion_reliability,
            self.evidence_integrity,
            self.identity_binding,
            self.cross_message_consistency,
            self.evidence_coverage,
        )
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(value)
            or value < 0
            for value in values
        ):
            raise ValueError("Trust weights must be finite and non-negative.")
        if not isclose(sum(values), 1.0, abs_tol=1e-9):
            raise ValueError("Trust weights must sum to 1.")


@dataclass(frozen=True)
class CapabilityObservation:
    capability: str
    evidence_kind: str
    verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.capability, str) or not self.capability.strip():
            raise ValueError("capability must be non-empty.")
        if self.evidence_kind not in {
            "configured_permission",
            "simulated_demonstration",
            "actual_demonstration",
        }:
            raise ValueError("Unsupported capability evidence kind.")
        if not isinstance(self.verified, bool):
            raise ValueError("verified must be a bool.")


@dataclass(frozen=True)
class AgentTrustHistory:
    """Detector-derived history only; this type has no evaluator-label fields."""

    agent_id: str
    outcome_counts: tuple[tuple[str, int], ...] = ()
    evidence_status_counts: tuple[tuple[str, int], ...] = ()
    assertions_with_evidence: int = 0
    assertions_evaluated: int = 0
    identity_checks: int = 0
    identity_verified: int = 0
    cross_message_checks: int = 0
    cross_message_findings: int = 0
    capabilities: tuple[CapabilityObservation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise ValueError("agent_id must be non-empty.")
        outcome_names = {outcome.value for outcome in ClaimOutcome}
        _validate_counts(self.outcome_counts, outcome_names, "outcome_counts")
        _validate_counts(
            self.evidence_status_counts, _EVIDENCE_STATUSES, "evidence_status_counts"
        )
        for field in (
            "assertions_with_evidence",
            "assertions_evaluated",
            "identity_checks",
            "identity_verified",
            "cross_message_checks",
            "cross_message_findings",
        ):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer.")
        if self.assertions_with_evidence > self.assertions_evaluated:
            raise ValueError("Assertions with evidence cannot exceed evaluated assertions.")
        if self.identity_verified > self.identity_checks:
            raise ValueError("identity_verified cannot exceed identity_checks.")
        if self.cross_message_findings > self.cross_message_checks:
            raise ValueError("cross-message findings cannot exceed checks.")
        if any(not isinstance(item, CapabilityObservation) for item in self.capabilities):
            raise ValueError("capabilities must contain CapabilityObservation values.")

    def outcomes(self) -> dict[str, int]:
        return dict(self.outcome_counts)

    def evidence_statuses(self) -> dict[str, int]:
        return dict(self.evidence_status_counts)


@dataclass(frozen=True)
class TrustScore:
    agent_id: str
    score: float
    raw_score: float
    components: Mapping[str, float]
    evidence_coverage: float | None
    evaluated_assertions: int
    confidence: float
    cold_start: bool
    historical_outcome_counts: Mapping[str, int]
    historical_evidence_status_counts: Mapping[str, int]
    identity_checks: int
    identity_verified: int
    cross_message_checks: int
    cross_message_findings: int


def _validate_counts(
    rows: tuple[tuple[str, int], ...], allowed: frozenset[str] | set[str], field: str
) -> None:
    if not isinstance(rows, tuple):
        raise ValueError(f"{field} must be a tuple of (name, count) pairs.")
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 2:
            raise ValueError(f"{field} must contain (name, count) pairs.")
        name, count = row
        if not isinstance(name, str) or name not in allowed or name in names:
            raise ValueError(f"{field} contains an unknown or duplicate name.")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"{field} counts must be non-negative integers.")
        names.add(name)


def score_agent_trust(
    history: AgentTrustHistory,
    *,
    weights: TrustWeights = TrustWeights(),
    minimum_observations: int = 3,
) -> TrustScore:
    """Score evidence quality, not agent intent or maliciousness.

    SUPPORTED and CONTRADICTED alone inform the smoothed assertion-reliability
    estimate. UNSUPPORTED and INSUFFICIENT_EVIDENCE remain separate counts and
    are not treated as false claims. Sparse estimates are shrunk toward 0.5.
    """
    if not isinstance(history, AgentTrustHistory):
        raise TypeError("history must be an AgentTrustHistory.")
    if not isinstance(weights, TrustWeights):
        raise TypeError("weights must be TrustWeights.")
    if (
        not isinstance(minimum_observations, int)
        or isinstance(minimum_observations, bool)
        or minimum_observations < 1
    ):
        raise ValueError("minimum_observations must be a positive integer.")

    outcomes = history.outcomes()
    supported = outcomes.get(ClaimOutcome.SUPPORTED.value, 0)
    contradicted = outcomes.get(ClaimOutcome.CONTRADICTED.value, 0)
    reliability = (supported + 1) / (supported + contradicted + 2)

    evidence = history.evidence_statuses()
    evidence_total = sum(evidence.values())
    valid_evidence = evidence.get("valid", 0)
    evidence_integrity = (
        (valid_evidence + 1) / (evidence_total + 2)
        if evidence_total
        else 0.5
    )
    identity_binding = (
        (history.identity_verified + 1) / (history.identity_checks + 2)
        if history.identity_checks
        else 0.5
    )
    cross_message_consistency = (
        (history.cross_message_checks - history.cross_message_findings + 1)
        / (history.cross_message_checks + 2)
        if history.cross_message_checks
        else 0.5
    )
    coverage = (
        history.assertions_with_evidence / history.assertions_evaluated
        if history.assertions_evaluated
        else None
    )
    coverage_component = 0.5 if coverage is None else coverage

    components = {
        "assertion_reliability": reliability,
        "evidence_integrity": evidence_integrity,
        "identity_binding": identity_binding,
        "cross_message_consistency": cross_message_consistency,
        "evidence_coverage": coverage_component,
    }
    raw_score = sum(
        components[name] * getattr(weights, name)
        for name in components
    )
    evaluated = history.assertions_evaluated
    confidence = evaluated / (evaluated + minimum_observations)
    score = 0.5 + confidence * (raw_score - 0.5)
    return TrustScore(
        agent_id=history.agent_id,
        score=min(1.0, max(0.0, score)),
        raw_score=raw_score,
        components=components,
        evidence_coverage=coverage,
        evaluated_assertions=evaluated,
        confidence=confidence,
        cold_start=evaluated < minimum_observations,
        historical_outcome_counts=outcomes,
        historical_evidence_status_counts=evidence,
        identity_checks=history.identity_checks,
        identity_verified=history.identity_verified,
        cross_message_checks=history.cross_message_checks,
        cross_message_findings=history.cross_message_findings,
    )


def merge_counts(
    first: tuple[tuple[str, int], ...], second: Mapping[str, int]
) -> tuple[tuple[str, int], ...]:
    """Return a canonical sum used by the online experiment feedback adapter."""
    merged = dict(first)
    for name, count in second.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("Merged counts must be non-negative integers.")
        merged[name] = merged.get(name, 0) + count
    return tuple(sorted((name, count) for name, count in merged.items() if count))
