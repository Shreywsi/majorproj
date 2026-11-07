"""LDP governance extensions from the base paper (Prakash, arXiv:2603.18043).

Implements, as plain Python:
  * delegation contracts with budgets, deadlines and a failure policy
  * claimed-vs-attested quality claims (claim_type)
  * typed failures (LdpError with category / severity / retryable / partial_output)
  * verification status + lineage

Validation is client-side and best-effort, exactly as in the paper: the
contract expresses intent and enables auditing, it is not platform enforcement.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class ClaimType(str, Enum):
    SELF_CLAIMED = "self_claimed"
    RUNTIME_OBSERVED = "runtime_observed"
    ISSUER_ATTESTED = "issuer_attested"
    EXTERNALLY_BENCHMARKED = "externally_benchmarked"


# Router versions of this testbed map onto the paper's claim types:
#   R1      -> routes on SELF_CLAIMED scores
#   R2..R8  -> route on RUNTIME_OBSERVED scores (signed evaluator-side receipts)
CLAIM_TRUST_ORDER = [
    ClaimType.SELF_CLAIMED,
    ClaimType.RUNTIME_OBSERVED,
    ClaimType.ISSUER_ATTESTED,
    ClaimType.EXTERNALLY_BENCHMARKED,
]


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    SELF_VERIFIED = "self_verified"
    PEER_VERIFIED = "peer_verified"
    TOOL_VERIFIED = "tool_verified"
    HUMAN_VERIFIED = "human_verified"


class FailurePolicy(str, Enum):
    FAIL_CLOSED = "fail_closed"
    FAIL_OPEN = "fail_open"


@dataclass
class QualityClaim:
    value: float
    claim_type: ClaimType = ClaimType.SELF_CLAIMED
    skill: str = "general"
    issuer: Optional[str] = None
    issued_at: int = 0


@dataclass
class Contract:
    objective: str = "complete the task"
    success_criteria: List[str] = field(default_factory=list)
    max_tokens: int = 800
    max_cost_usd: float = 0.05
    deadline_s: float = 5.0
    failure_policy: FailurePolicy = FailurePolicy.FAIL_CLOSED
    max_delegation_depth: int = 2  # tracked, not enforced (as in the paper)
    contract_id: str = "ctr-0"


@dataclass
class Provenance:
    produced_by: str
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    evidence: List[str] = field(default_factory=list)
    lineage: List[str] = field(default_factory=list)  # delegates that handled it
    violations: List[str] = field(default_factory=list)


# category -> (retryable, severity, typical response)   (Table in Sec. 3.3)
ERROR_TABLE = {
    "runtime": (True, "error", "retry or reroute to another delegate"),
    "transport": (True, "warning", "retry with exponential backoff"),
    "policy": (False, "fatal", "escalate; contract violation"),
    "capability": (False, "error", "select a different delegate"),
    "quality": (False, "warning", "accept with quality warning"),
    "identity": (False, "error", "authentication failure"),
    "session": (True, "error", "re-establish session"),
}


class LdpError(Exception):
    """Typed, machine-readable failure."""

    def __init__(self, category: str, code: str, message: str = "",
                 partial_output: Any = None):
        if category not in ERROR_TABLE:
            raise ValueError(f"unknown error category {category!r}")
        super().__init__(f"{category}/{code}: {message}")
        self.category = category
        self.code = code
        self.message = message
        self.partial_output = partial_output
        self.retryable, self.severity, _ = ERROR_TABLE[category]


def check_contract(contract: Contract, tokens_used: float, elapsed_s: float,
                   cost_usd: float = 0.0) -> List[str]:
    """Return the list of violated constraints (empty if compliant)."""
    out = []
    if tokens_used > contract.max_tokens:
        out.append("max_tokens")
    if elapsed_s > contract.deadline_s:
        out.append("deadline")
    if cost_usd > contract.max_cost_usd:
        out.append("max_cost_usd")
    return out


def validate(contract: Contract, tokens_used: float, elapsed_s: float,
             output: Any = None, cost_usd: float = 0.0,
             provenance: Optional[Provenance] = None) -> Any:
    """Client-side validation.

    fail_closed: raise LdpError(policy, CONTRACT_VIOLATED) with the delegate's
                 output preserved as partial_output.
    fail_open:   accept the output but record the violations in provenance.
    """
    violations = check_contract(contract, tokens_used, elapsed_s, cost_usd)
    if not violations:
        return output
    if contract.failure_policy == FailurePolicy.FAIL_CLOSED:
        raise LdpError("policy", "CONTRACT_VIOLATED", ",".join(violations),
                       partial_output=output)
    if provenance is not None:
        provenance.violations.extend(violations)
    return output
