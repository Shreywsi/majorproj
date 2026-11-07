"""Phase 1-2 foundation: simulated agents and the R0/R1/R2 router boundary."""
import numpy as np
import pytest

from agents import Profile, build_agents
from config import Config, ORACLE
from crypto_ledger import EvidenceAuthority, EvidenceVerifier
from router import Router
from simulate import run


def test_default_research_configuration():
    cfg = Config()
    assert (cfg.N, cfg.T, cfg.K, cfg.eps) == (15, 5000, 6, 0.05)
    assert np.isclose(cfg.task_probs().sum(), 1)


@pytest.mark.parametrize("kwargs", [
    {"N": 0}, {"T": 0}, {"K": 1}, {"eps": 1.1}, {"sleeper_rate": -0.1},
    {"a0": 0},
])
def test_invalid_simulation_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)


def test_agent_population_separates_claims_and_hidden_quality():
    cfg = Config(N=10, T=10)
    agents = build_agents(["liar"], 0.3, cfg, np.random.default_rng(42))
    assert len(agents) == 10
    assert sum(agent.bad for agent in agents) == 3
    liar = agents[0]
    profile = liar.publish(0, 0)
    assert profile.claim > liar.q[0]
    assert liar.q.min() >= 0 and liar.q.max() <= 1


def test_claim_router_uses_profile_claim_not_hidden_quality():
    cfg = Config(N=2, T=1, eps=0)
    router = Router(1, cfg, np.random.default_rng(1))
    profiles = [Profile(0.1, "a", ""), Profile(0.9, "b", "")]
    scores = router.score_all([0, 1], 0, 0, profiles)
    assert scores.tolist() == [0.1, 0.9]
    assert router.select(scores, [0, 1])[0] == 1


def test_phase_two_routers_are_not_passed_hidden_ground_truth(monkeypatch):
    observed = []
    original = Router.score_all

    def inspect_inputs(router, uids, task, timestamp, profiles, true_q=None):
        observed.append((router.v, true_q))
        return original(router, uids, task, timestamp, profiles, true_q)

    monkeypatch.setattr(Router, "score_all", inspect_inputs)
    cfg = Config(N=4, T=8)
    for version in (0, 1, 2):
        run(version, "liar", 0.5, 23, cfg)
    assert observed
    assert all(version in (0, 1, 2) and truth is None for version, truth in observed)


def test_only_oracle_router_receives_ground_truth():
    cfg = Config(N=3, T=4, eps=0)
    result = run(ORACLE, "none", 0, 17, cfg)
    assert len(result.df) == cfg.T
    assert result.df.qstar.between(0, 1).all()


def test_r2_accepts_trusted_attestation_and_rejects_mismatch_and_replay():
    cfg = Config(N=2, T=4, eps=0)
    evaluator, attacker = EvidenceAuthority(), EvidenceAuthority()
    router = Router(
        2, cfg, np.random.default_rng(5),
        evidence_verifier=EvidenceVerifier(evaluator.vk),
    )
    trusted = evaluator.attest(0, 1, 0.8, 1, nonce="trusted-1")
    assert router.update(0, 1, 0.8, 1, evidence=trusted)
    assert not router.update(0, 1, 0.8, 1, evidence=trusted)
    mismatched = attacker.attest(1, 1, 0.8, 2, nonce="attacker-1")
    assert not router.update(0, 1, 0.8, 2, evidence=mismatched)
    assert router.rejected == 2
    assert router._post(0, 1, 2) == (1.8, 1.2)


def test_random_seed_is_validated():
    with pytest.raises(ValueError, match="seed"):
        run(0, "none", 0, -1, Config(N=2, T=1))


def test_unknown_attack_and_ratio_are_rejected():
    cfg = Config(N=3, T=1)
    with pytest.raises(ValueError, match="unknown attack"):
        build_agents(["unknown"], 0.5, cfg, np.random.default_rng(0))
    with pytest.raises(ValueError, match="rho"):
        build_agents(["liar"], 1.1, cfg, np.random.default_rng(0))
