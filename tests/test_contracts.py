import pytest

from ldp_layer import Contract, FailurePolicy, LdpError, Provenance, validate


def test_compliant_result_passes():
    assert validate(Contract(max_tokens=100, deadline_s=5), 50, 1.0, output="ok") == "ok"


def test_fail_closed_raises_typed_error_and_keeps_partial_output():
    c = Contract(max_tokens=6000, failure_policy=FailurePolicy.FAIL_CLOSED)
    with pytest.raises(LdpError) as e:
        validate(c, 8200, 1.0, output="draft")
    assert e.value.category == "policy" and e.value.code == "CONTRACT_VIOLATED"
    assert e.value.partial_output == "draft" and not e.value.retryable


def test_fail_open_accepts_and_logs_violation():
    c = Contract(max_tokens=100, failure_policy=FailurePolicy.FAIL_OPEN)
    p = Provenance(produced_by="a")
    assert validate(c, 500, 1.0, output="x", provenance=p) == "x"
    assert p.violations == ["max_tokens"]
