"""Honest and dishonest agent behaviour models (Algorithm 2 of the LaTeX plan).

Attack kinds
  liar      attack 1  inflates its claim
  adaptive  attack 2  tunes its claim to hit a target selection share
  twofaced  attack 3  good while t < t_trust or when it recognises a probe
  sybil     attack 4  rotates to a fresh identity when flagged / unpopular
  colluder  attack 5  ring that endorses each other and shares output drift
  injector  attack 6  hides a (canary) instruction in its profile text
  sleeper   attack 7  sabotages only on a rare trigger task type
  drifter   extra     degrades slowly, below the CUSUM slack
"""
from collections import deque
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from config import ATTACK_NAMES, Config, INJECT_MARKER

HONEST = "honest"
BENIGN_TEXT = "General-purpose assistant agent."


@dataclass
class Profile:
    claim: float
    key: str
    text: str
    endorse: tuple = ()


class Agent:
    def __init__(self, idx: int, kind: str, q: np.ndarray, rng, cfg: Config):
        self.idx, self.kind, self.q, self.rng, self.cfg = idx, kind, q, rng, cfg
        self.uid = idx                  # router-visible identity (changes for sybils)
        self.key = f"key{idx}"          # signature/claim key shown in the profile
        self.trigger = cfg.K - 1        # sleeper trigger = rare last task type
        self.ring: Sequence["Agent"] = ()
        self.shock: Optional[np.ndarray] = None
        # state for adaptive / sybil agents
        self.claim_state = 0.6
        self.p_hat = 0.0
        self.own = deque(maxlen=10)
        self.last_rot = 0
        self.n_ident = 1
        self.first_sabotage: Optional[int] = None

    # ------------------------------------------------------------------
    @property
    def bad(self) -> bool:
        return self.kind != HONEST

    def attack_start(self) -> Optional[int]:
        """Round at which the harmful behaviour starts (None = never observed)."""
        if not self.bad:
            return None
        if self.kind in ("twofaced", "drifter"):
            return self.cfg.t_trust
        if self.kind == "sleeper":
            return self.first_sabotage
        return 0

    def effective_q(self, k: int, t: int) -> float:
        """Expected true quality right now (used for q*_t and the oracle)."""
        c = self.cfg
        if self.kind == "twofaced" and t >= c.t_trust:
            return min(self.q[k], c.q_low)
        if self.kind == "sleeper" and k == self.trigger:
            return 0.0
        if self.kind == "drifter" and t >= c.t_trust:
            return max(c.q_low, self.q[k] - c.drip * (t - c.t_trust))
        return float(self.q[k])

    # ------------------------------------------------------------------
    def publish(self, k: int, t: int) -> Profile:
        c = self.cfg
        text = BENIGN_TEXT
        endorse = ()
        if self.kind == "liar":
            claim = min(1.0, self.q[k] + c.liar_delta)
        elif self.kind == "adaptive":
            claim = float(np.clip(self.claim_state, 0.0, 1.0))
        elif self.kind == "sybil":
            claim = 0.99
        elif self.kind == "colluder":
            claim = 0.99
            endorse = tuple(a.uid for a in self.ring if a is not self)
        elif self.kind == "injector":
            claim = float(self.q[k])
            text = BENIGN_TEXT + " " + INJECT_MARKER
        else:  # honest, twofaced, sleeper, drifter look honest in their profile
            claim = float(self.q[k])
        return Profile(claim, self.key, text, endorse)

    def execute(self, k: int, t: int, probe_seen: bool = False) -> float:
        c = self.cfg
        noise = self.rng.normal(0.0, c.noise_sd)
        if self.kind == "twofaced":
            base = self.q[k] if (t < c.t_trust or probe_seen) else c.q_low
        elif self.kind == "sleeper":
            if k == self.trigger:
                base = 0.0
                if self.first_sabotage is None:
                    self.first_sabotage = t
            else:
                base = self.q[k]
        elif self.kind == "drifter":
            base = self.effective_q(k, t)
        elif self.kind == "colluder" and self.shock is not None:
            base = self.q[k] + self.shock[t // c.W]
        else:
            base = self.q[k]
        return float(np.clip(base + noise, 0.0, 1.0))

    def resource_use(self):
        """(tokens, latency_s) for the optional contract experiment."""
        c = self.cfg
        tokens = self.rng.normal(500, 80)
        lat = self.rng.normal(2.0, 0.6)
        if self.bad and self.rng.random() < c.overrun_p:
            tokens *= 2.0
        return float(tokens), float(lat)

    # ------------------------------------------------------------------
    def observe_round(self, selected: bool):
        """Feedback available to attackers: whether they were picked."""
        if self.kind == "adaptive":
            self.p_hat = 0.98 * self.p_hat + 0.02 * float(selected)
            self.claim_state = float(np.clip(
                self.claim_state + self.cfg.adapt_eta * (self.cfg.adapt_target - self.p_hat),
                0.0, 1.0))

    def observe_outcome(self, y: float):
        self.own.append(y)

    def maybe_rotate(self, flagged: bool, t: int, new_uid: int) -> bool:
        """Sybil: abandon a burnt identity and start a fresh one."""
        if self.kind != "sybil" or self.n_ident >= self.cfg.sybil_max_idents:
            return False
        unpopular = len(self.own) >= 10 and float(np.mean(self.own)) < 0.55
        if (flagged or unpopular) and t - self.last_rot >= 20:
            self.uid = new_uid
            if self.rng.random() >= self.cfg.sybil_key_reuse:
                self.key = f"key{self.idx}-{self.n_ident}"
            self.own.clear()
            self.last_rot = t
            self.n_ident += 1
            return True
        return False


# ----------------------------------------------------------------------
def build_agents(attacks, rho: float, cfg: Config, rng):
    """Create N agents; ceil(rho*N) of them get attack kinds from `attacks`
    (round-robin when several attacks are combined)."""
    if isinstance(attacks, str):
        attacks = [attacks] if attacks and attacks != "none" else []
    attacks = list(attacks)
    if not 0.0 <= rho <= 1.0:
        raise ValueError("dishonest-agent ratio rho must be between 0 and 1")
    unknown = set(attacks) - set(ATTACK_NAMES)
    if unknown:
        raise ValueError(f"unknown attack type(s): {', '.join(sorted(unknown))}")
    n_bad = int(np.ceil(rho * cfg.N)) if attacks else 0
    n_blocks = cfg.T // cfg.W + 2
    ring_shock = rng.normal(0.0, cfg.ring_shock_sd, n_blocks)

    agents = []
    for i in range(cfg.N):
        if i < n_bad:
            kind = attacks[i % len(attacks)]
            if kind in ("twofaced", "sleeper", "drifter"):
                base = rng.uniform(0.82, 0.95)       # genuinely good until it strikes
            elif kind == "colluder":
                base = rng.uniform(0.55, 0.75)
            else:
                base = rng.uniform(0.30, 0.50)       # genuinely poor
        else:
            kind = HONEST
            base = rng.uniform(0.55, 0.92)
        q = np.clip(base + rng.normal(0.0, 0.06, cfg.K), 0.05, 0.99)
        agents.append(Agent(i, kind, q, rng, cfg))

    ring = [a for a in agents if a.kind == "colluder"]
    for a in ring:
        a.ring, a.shock = ring, ring_shock
    return agents
