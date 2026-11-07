"""Step 2 of the protocol: the baseline must reproduce the paper's finding."""
import numpy as np

from config import Config
from metrics import summarize
from simulate import run


def _mean_y(version, attack, rho, cfg, seeds=range(4)):
    return float(np.mean([summarize(run(version, attack, rho, s, cfg), cfg)["mean_y"]
                          for s in seeds]))


def test_provenance_paradox_and_verified_proof():
    cfg = Config(T=1000)
    m = {v: _mean_y(v, "liar", 0.3, cfg) for v in (0, 1, 2)}
    assert m[1] < m[0] < m[2], m          # R1 < R0 < R2


def test_self_claims_help_when_everyone_is_honest():
    cfg = Config(T=1000)
    assert _mean_y(1, "none", 0.0, cfg) > _mean_y(0, "none", 0.0, cfg)


def test_same_seed_is_reproducible():
    cfg = Config(T=300)
    a = run(5, "twofaced", 0.3, 7, cfg).df
    b = run(5, "twofaced", 0.3, 7, cfg).df
    assert a.equals(b)
