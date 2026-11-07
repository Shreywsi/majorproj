"""Trusted simulator attestations and their verification.

The simulation's evaluator signs outcomes it observed. Ed25519 proves that a
record came from that evaluator and was not changed; it does not prove that the
outcome is objectively correct or that an agent's work was useful.
"""
import hashlib
import math
import os

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def _digest(uid, k, y, t, nonce) -> bytes:
    return hashlib.sha256(
        f"{uid}|{k}|{float(y).hex()}|{t}|{nonce}".encode()
    ).digest()


def _valid_payload(rec: dict) -> bool:
    try:
        y = float(rec["y"])
        t = int(rec["t"])
        return (
            math.isfinite(y)
            and 0.0 <= y <= 1.0
            and t >= 0
            and t == rec["t"]
            and bool(str(rec["nonce"]))
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class EvidenceAuthority:
    """Trusted evaluator-side key used only to attest simulator observations."""

    def __init__(self, seed: bytes | None = None):
        self.sk = SigningKey(seed) if seed else SigningKey.generate()
        self.vk = self.sk.verify_key

    def sign(self, uid, k, y, t, nonce: str | None = None) -> dict:
        y = float(y)
        if not math.isfinite(y) or not 0.0 <= y <= 1.0 or int(t) < 0:
            raise ValueError("attested outcome must be in [0, 1] with a non-negative timestamp")
        nonce = nonce or os.urandom(8).hex()
        sig = self.sk.sign(_digest(uid, k, y, t, nonce)).signature
        return {"uid": uid, "k": k, "y": y, "t": t, "nonce": nonce, "sig": sig}

    def attest(self, uid, k, y, t, nonce: str | None = None) -> dict:
        """Create an attestation after the trusted evaluator observes an outcome."""
        return self.sign(uid, k, y, t, nonce)

    def sign_payload(self, payload: bytes) -> bytes:
        """Sign canonical bytes for typed runtime evidence using this ledger key."""
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes.")
        return self.sk.sign(payload).signature


class EvidenceVerifier:
    """Public-key-only verifier for trusted evaluator outcome attestations."""

    def __init__(self, verify_key: VerifyKey | bytes):
        self.vk = verify_key if isinstance(verify_key, VerifyKey) else VerifyKey(verify_key)

    def verify_payload(self, payload: bytes, signature: bytes) -> bool:
        """Verify detached canonical bytes with the ledger's Ed25519 public key."""
        if not isinstance(payload, bytes) or not isinstance(signature, bytes):
            return False
        try:
            self.vk.verify(payload, signature)
        except (BadSignatureError, TypeError, ValueError):
            return False
        return True

    def verify(
        self,
        rec: dict,
        seen: set,
        t_now: int,
        tau_max: int | None = None,
        expected: tuple | None = None,
    ) -> bool:
        """Accept only valid, fresh, unused attestations matching the observation."""
        if not _valid_payload(rec):
            return False
        if rec["t"] > t_now:
            return False
        if tau_max is not None and t_now - rec["t"] > tau_max:
            return False
        if rec["nonce"] in seen:
            return False
        if expected is not None and (
            rec["uid"], rec["k"], float(rec["y"]), rec["t"]
        ) != (expected[0], expected[1], float(expected[2]), expected[3]):
            return False
        try:
            self.vk.verify(_digest(rec["uid"], rec["k"], rec["y"], rec["t"], rec["nonce"]),
                           rec["sig"])
        except (BadSignatureError, KeyError, TypeError, ValueError):
            return False
        seen.add(rec["nonce"])
        return True


class Signer(EvidenceAuthority):
    """Compatibility helper for direct ledger tests and local experiments."""

    def verify(self, rec: dict, seen: set, t_now: int, tau_max: int | None = None) -> bool:
        return EvidenceVerifier(self.vk).verify(rec, seen, t_now, tau_max)
