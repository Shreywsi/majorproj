"""Each defence must do what the ladder claims, at least on its home attack."""
import numpy as np

from config import Config
from metrics import summarize
from simulate import run


def _avg(version, attack, rho, key, cfg, seeds=range(4)):
    return float(np.mean([summarize(run(version, attack, rho, s, cfg), cfg)[key]
                          for s in seeds]))


def test_cusum_detects_two_faced_agents():
    cfg = Config(T=1500)
    assert _avg(7, "twofaced", 0.3, "detect_rate", cfg) > 0.05
    assert _avg(5, "twofaced", 0.3, "detect_rate", cfg) == 0.0   # no detector before R7


def test_cusum_cuts_two_faced_damage():
    cfg = Config(T=1500)
    assert _avg(7, "twofaced", 0.3, "asr", cfg) < _avg(5, "twofaced", 0.3, "asr", cfg)


def test_identity_layer_cuts_collusion():
    cfg = Config(T=1500)
    assert _avg(6, "colluder", 0.3, "asr", cfg) <= _avg(5, "colluder", 0.3, "asr", cfg)


def test_injection_hardening():
    cfg = Config(T=1000)
    assert _avg(7, "injector", 0.3, "asr", cfg) > 0.2
    assert _avg(8, "injector", 0.3, "asr", cfg) < 0.05


def test_false_accusations_stay_low_without_attackers():
    cfg = Config(T=1500)
    assert _avg(7, "none", 0.0, "far", cfg) <= 0.1


def test_contract_reroute_recovers_from_violations():
    cfg = Config(T=500)
    r = summarize(run(1, "liar", 0.3, 1, cfg, use_contracts=True), cfg)
    assert r["viol_rate"] > 0 and r["recovery_rate"] > 0.5
