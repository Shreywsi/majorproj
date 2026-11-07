"""Router ladder R0..R8 (+ oracle).  Each version adds exactly one defence.

  R0 random                       R5 + lower credible bound
  R1 trust self-reports           R6 + identity / sybil / collusion checks
  R2 signed verified proof        R7 + CUSUM quarantine and hidden probes
  R3 + time decay                 R8 + injection hardening (ignore profile text)
  R4 + per-task-type record       ORACLE (100): knows true quality (upper bound)

Evidence is kept as exponentially-decayed sufficient statistics, so the Beta
posterior of Section "Scoring" is computed in O(1) per (agent, task type).
"""
import math
from collections import defaultdict, deque

import numpy as np
import pandas as pd
from scipy.special import betaincinv

from config import Config, INJECT_MARKER, ORACLE
from crypto_ledger import EvidenceVerifier, Signer


class Router:
    def __init__(self, version: int, cfg: Config, rng, evidence_verifier=None):
        self.v, self.cfg, self.rng = version, cfg, rng
        if version not in (*range(9), ORACLE):
            raise ValueError(f"unsupported router version: {version}")
        # R2 uses a plain (undecayed) mean of the verified ledger; R3+ decays.
        self.lam = 0.0 if version == 2 else cfg.lam

        signer = Signer() if evidence_verifier is None else None
        self.signer = signer
        self.evidence_verifier = (
            evidence_verifier
            if evidence_verifier is not None
            else EvidenceVerifier(signer.vk)
        )
        self.seen = set()
        self.rejected = 0

        self.pool = {}                       # uid -> [sw, sy, t_last, n]   (all types)
        self.typ = {}                        # (uid, k) -> same
        self.records = defaultdict(list)     # uid -> [(k, y, t)]

        self.first_seen = {}
        self.key_of = {}
        self.endorses = {}                   # uid -> set(uid it endorses)
        self.n_end = defaultdict(int)

        # R6
        self.susp = {}                       # uid -> cluster id
        self.clusters = {}                   # cluster id -> set(uids)
        self.beh_links = set()
        self.ever_susp = set()

        # R7
        self.S = defaultdict(float)
        self.alert = {}
        self.alert_ref = {}
        self.cleared_at = {}
        self.recent = defaultdict(lambda: deque(maxlen=cfg.W_rec))
        self.probe_queue = []
        self.alert_times = defaultdict(list)  # uid -> [t, ...]
        self.ever_alert = set()

    # ------------------------------------------------------------------
    # evidence bookkeeping
    # ------------------------------------------------------------------
    def _dec(self, acc, t):
        if acc is None:
            return 0.0, 0.0
        f = math.exp(-self.lam * (t - acc[2]))
        return acc[0] * f, acc[1] * f

    def _add(self, d, key, y, t):
        acc = d.get(key)
        if acc is None:
            d[key] = [1.0, y, t, 1]
        else:
            f = math.exp(-self.lam * (t - acc[2]))
            acc[0] = acc[0] * f + 1.0
            acc[1] = acc[1] * f + y
            acc[2] = t
            acc[3] += 1

    def _post(self, uid, k, t):
        """Weighted Beta posterior (alpha, beta) for identity uid on type k."""
        c = self.cfg
        psw, psy = self._dec(self.pool.get(uid), t)
        if self.v <= 3:                                   # pooled over task types
            return c.a0 + psy, c.b0 + psw - psy
        acc = self.typ.get((uid, k))                      # R4+: per-type
        sw, sy = self._dec(acc, t)
        n = acc[3] if acc else 0
        if n < c.n_min:                                   # thin evidence: pooled prior
            m = (c.a0 + psy) / (c.a0 + c.b0 + psw)
            s = c.prior_strength
            return s * m + sy, s * (1.0 - m) + sw - sy
        return c.a0 + sy, c.b0 + sw - sy

    def _pooled_cluster_post(self, members, k, t):
        sw = sy = 0.0
        for m in members:
            acc = self.typ.get((m, k)) if self.v >= 4 else self.pool.get(m)
            w, yv = self._dec(acc, t)
            sw += w
            sy += yv
        return self.cfg.a0 + sy, self.cfg.b0 + sw - sy

    def _utility(self, al, be):
        if self.v >= 5:
            return betaincinv(al, be, self.cfg.cred_q)
        return al / (al + be)

    # ------------------------------------------------------------------
    # per-round interface
    # ------------------------------------------------------------------
    def observe_profiles(self, uids, profiles, t):
        new_key_seen = False
        keys_known = set(self.key_of.values()) if self.v >= 6 else None
        self.n_end = defaultdict(int)
        for uid, p in zip(uids, profiles):
            if uid not in self.first_seen:
                self.first_seen[uid] = t
                if keys_known is not None and p.key in keys_known:
                    new_key_seen = True
            self.key_of[uid] = p.key
            self.endorses[uid] = set(p.endorse)
            for tgt in p.endorse:
                self.n_end[tgt] += 1
        if self.v >= 6 and (new_key_seen or t == 0):
            self._recluster(t, behaviour=False)

    def score_all(self, uids, k, t, profiles, true_q=None):
        v, c = self.v, self.cfg
        n = len(uids)
        if v == 0:
            return self.rng.random(n)
        if v == ORACLE:
            if true_q is None:
                raise ValueError("oracle routing requires explicit evaluator-only quality values")
            return np.asarray(true_q, float)

        if v == 1:
            u = np.array([p.claim for p in profiles], float)
        else:
            al = np.empty(n)
            be = np.empty(n)
            for i, uid in enumerate(uids):
                al[i], be[i] = self._post(uid, k, t)
            u = np.asarray(self._utility(al, be), float)

            for i, uid in enumerate(uids):
                flagged = v >= 6 and uid in self.susp
                if not flagged:                    # social-proof bonus (see README)
                    u[i] += c.endorse_w * self.n_end.get(uid, 0) / (c.N - 1)
                if v >= 6:
                    if self.first_seen[uid] > 0 and t - self.first_seen[uid] < c.A_prob:
                        u[i] = min(u[i], c.u_cap)  # probation for late joiners
                    if flagged:
                        a2, b2 = self._pooled_cluster_post(self.clusters[self.susp[uid]], k, t)
                        u[i] = min(u[i], float(self._utility(a2, b2)))
                if v >= 7:
                    u[i] *= self._quarantine_factor(uid, t)

        if 1 <= v <= 7:                            # naive routers read profile text
            for i, p in enumerate(profiles):
                if INJECT_MARKER in p.text and self.rng.random() < c.p_inject:
                    u[i] = 2.0
        return u

    def select(self, u, uids, exclude=()):
        """eps-greedy; exploration doubles as a hidden probe. Returns (index, is_probe)."""
        u = np.array(u, float)
        for e in exclude:
            u[e] = -np.inf
        if self.v >= 7 and self.probe_queue and self.rng.random() < self.cfg.probe_rate:
            uid = self.probe_queue.pop(0)
            if uid in uids:
                idx = uids.index(uid)
                if idx not in exclude:
                    return idx, True
        if self.rng.random() < self.cfg.eps:
            cand = np.flatnonzero(np.isfinite(u))
            return int(self.rng.choice(cand)), True
        best = np.flatnonzero(u == u.max())
        return int(self.rng.choice(best)), False

    def update(self, uid, k, y, t, evidence=None):
        """Record an outcome. Returns False if the evidence was rejected."""
        v, c = self.v, self.cfg
        if v < 2:
            return True
        if c.real_crypto:
            if evidence is None:
                if self.signer is None:
                    self.rejected += 1
                    return False
                evidence = self.signer.attest(uid, k, y, t)
            if not self.evidence_verifier.verify(
                evidence, self.seen, t, c.tau_max, expected=(uid, k, y, t)
            ):
                self.rejected += 1
                return False

        mu = None
        if v >= 7:
            if c.cusum_pooled:                    # agent-level expectation mu_a
                psw, psy = self._dec(self.pool.get(uid), t)
                mu = (c.a0 + psy) / (c.a0 + c.b0 + psw)
            else:                                 # per-type expectation mu_{a,k}
                a, b = self._post(uid, k, t)
                mu = a / (a + b)

        self._add(self.pool, uid, y, t)
        self._add(self.typ, (uid, k), y, t)
        self.records[uid].append((k, y, t))

        if v >= 7:
            self.S[uid] = max(0.0, self.S[uid] + (mu - y - c.kappa))
            if not self.alert.get(uid, False) and self.S[uid] > c.h:
                self.alert[uid] = True
                self.alert_ref[uid] = mu
                self.cleared_at.pop(uid, None)
                self.alert_times[uid].append(t)
                self.ever_alert.add(uid)
                self.probe_queue.extend([uid] * c.m_probe)
            elif self.alert.get(uid, False):
                rec_q = self.recent[uid]
                rec_q.append(y)
                ref = self.alert_ref[uid] - 2 * c.kappa
                if len(rec_q) == c.W_rec and min(rec_q) >= ref:
                    self.alert[uid] = False       # clear alert, restore trust gradually
                    self.S[uid] = 0.0
                    self.cleared_at[uid] = t
                    rec_q.clear()

        if v >= 6 and t > 0 and t % c.M == 0:
            self._recluster(t, behaviour=True)
        return True

    # ------------------------------------------------------------------
    def _quarantine_factor(self, uid, t):
        c = self.cfg
        if self.alert.get(uid, False):
            return c.gamma
        ct = self.cleared_at.get(uid)
        if ct is not None:
            return c.gamma + (1 - c.gamma) * min(1.0, (t - ct) / c.restore_T)
        return 1.0

    def is_flagged(self, uid) -> bool:
        """Visible to attackers: quarantined or capped as part of a cluster."""
        return bool(self.alert.get(uid, False)) or uid in self.susp

    # ------------------------------------------------------------------
    # R6: identity / collusion clustering
    # ------------------------------------------------------------------
    def _behaviour_links(self, t):
        c = self.cfg
        cols = {}
        for uid, recs in self.records.items():
            recs = [r for r in recs if r[2] > t - c.corr_window]
            if len(recs) < 2 * c.min_overlap:
                continue
            mk = defaultdict(list)
            for k, y, _ in recs:
                mk[k].append(y)
            mk = {k: float(np.mean(v)) for k, v in mk.items()}
            blk = defaultdict(list)
            for k, y, tt in recs:
                blk[tt // c.W].append(y - mk[k])
            cols[uid] = pd.Series({b: float(np.mean(v)) for b, v in blk.items()})
        links = set()
        if len(cols) >= 2:
            corr = pd.DataFrame(cols).corr(min_periods=c.min_overlap)
            ids = list(corr.columns)
            vals = corr.to_numpy()
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if vals[i, j] > c.theta_sim:
                        links.add((ids[i], ids[j]))
        return links

    def _recluster(self, t, behaviour):
        if behaviour:
            self.beh_links = self._behaviour_links(t)
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        by_key = defaultdict(list)
        for uid, key in self.key_of.items():
            by_key[key].append(uid)
        for grp in by_key.values():                       # shared key / claim signature
            for other in grp[1:]:
                union(grp[0], other)
        for a, tg in self.endorses.items():               # mutual endorsement
            for b in tg:
                if a in self.endorses.get(b, ()):
                    union(a, b)
        for a, b in self.beh_links:                       # behavioural similarity
            union(a, b)

        groups = defaultdict(set)
        for uid in list(parent):
            groups[find(uid)].add(uid)
        self.susp, self.clusters = {}, {}
        for cid, (_, members) in enumerate(groups.items()):
            if len(members) >= 2:
                self.clusters[cid] = members
                for m in members:
                    self.susp[m] = cid
                    self.ever_susp.add(m)
