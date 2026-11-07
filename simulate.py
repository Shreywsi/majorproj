"""Main simulation loop (Algorithm 1 of the LaTeX plan)."""
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from agents import build_agents
from config import Config, ORACLE
from crypto_ledger import EvidenceAuthority, EvidenceVerifier
from ldp_layer.contracts import Contract, LdpError, validate
from router import Router


@dataclass
class RunResult:
    df: pd.DataFrame
    meta: dict = field(default_factory=dict)


def run(version, attacks, rho, seed, cfg: Config, use_contracts: bool = False) -> RunResult:
    """Run one simulation.

    Agent construction, task assignment, router choices, probe recognition,
    and each agent's output noise use independent seed-derived streams. Router
    random draws therefore do not consume an agent's outcome-noise stream.
    """
    if not isinstance(seed, (int, np.integer)) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    streams = np.random.SeedSequence(int(seed)).spawn(cfg.N + 4)
    rng_build = np.random.default_rng(streams[0])
    rng_task = np.random.default_rng(streams[1])
    rng_router = np.random.default_rng(streams[2])
    rng_probe = np.random.default_rng(streams[3])

    agents = build_agents(attacks, rho, cfg, rng_build)
    for agent, stream in zip(agents, streams[4:]):
        agent.rng = np.random.default_rng(stream)
    evaluator = EvidenceAuthority() if version >= 2 and cfg.real_crypto else None
    verifier = EvidenceVerifier(evaluator.vk) if evaluator is not None else None
    router = Router(version, cfg, rng_router, evidence_verifier=verifier)
    tasks = rng_task.choice(cfg.K, size=cfg.T, p=cfg.task_probs())
    contract = Contract(max_tokens=cfg.max_tokens, deadline_s=cfg.deadline_s)

    next_uid = [cfg.N]
    uid_phys = {a.uid: a.idx for a in agents}
    feedback = [a for a in agents if a.kind in ("adaptive", "sybil")]

    rows = []
    t0 = time.perf_counter()
    for t in range(cfg.T):
        k = int(tasks[t])
        profiles = [a.publish(k, t) for a in agents]
        uids = [a.uid for a in agents]
        router.observe_profiles(uids, profiles, t)
        oracle_q = [a.effective_q(k, t) for a in agents] if version == ORACLE else None
        u = router.score_all(uids, k, t, profiles, oracle_q)

        excluded, viol, delivered, ok = set(), 0, None, False
        attempts = cfg.max_retries + 1 if use_contracts else 1
        for _ in range(attempts):
            idx, probe = router.select(u, uids, exclude=excluded)
            ag = agents[idx]
            seen = probe and rng_probe.random() < cfg.probe_detect
            y = ag.execute(k, t, seen)
            if use_contracts:
                tokens, lat = ag.resource_use()
                try:
                    validate(contract, tokens, lat, output=y)
                except LdpError:
                    viol += 1
                    outcome = 0.0
                    evidence = evaluator.attest(ag.uid, k, outcome, t) if evaluator else None
                    router.update(ag.uid, k, outcome, t, evidence=evidence)
                    excluded.add(idx)
                    delivered = (idx, 0.0, probe)      # if every retry fails
                    continue
            evidence = evaluator.attest(ag.uid, k, y, t) if evaluator else None
            router.update(ag.uid, k, y, t, evidence=evidence)
            delivered, ok = (idx, y, probe), True
            break
        idx, y, probe = delivered
        ag = agents[idx]
        recovered = viol > 0 and ok

        # feedback to attackers (they see their own outcomes and whether they were picked)
        ag.observe_outcome(y)
        for a in feedback:
            a.observe_round(a is ag)
            if a.kind == "sybil" and a.maybe_rotate(router.is_flagged(a.uid), t, next_uid[0]):
                uid_phys[next_uid[0]] = a.idx
                next_uid[0] += 1

        qstar = max(agent.effective_q(k, t) for agent in agents)
        rows.append((t, idx, ag.bad, k, y, qstar, probe, viol, recovered))
    elapsed = time.perf_counter() - t0

    df = pd.DataFrame(rows, columns=["t", "agent", "is_bad", "k", "y", "qstar",
                                     "probe", "viol", "recovered"])
    honest = {a.idx for a in agents if not a.bad}
    alert_by_phys = {}
    for uid, ts in router.alert_times.items():
        alert_by_phys.setdefault(uid_phys[uid], []).extend(ts)
    meta = dict(
        ms_per_round=1000.0 * elapsed / cfg.T,
        honest=honest,
        bad={a.idx: a.attack_start() for a in agents if a.bad},
        alert_by_phys=alert_by_phys,
        ever_alert_phys={uid_phys[u_] for u_ in router.ever_alert},
        ever_susp_phys={uid_phys[u_] for u_ in router.ever_susp},
        n_idents=sum(a.n_ident for a in agents if a.kind == "sybil"),
        rejected=router.rejected,
    )
    return RunResult(df, meta)
