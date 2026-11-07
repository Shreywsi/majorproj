from crypto_ledger import Signer


def test_valid_record_accepted_once_then_replay_rejected():
    s, seen = Signer(), set()
    r = s.sign(3, 1, 0.8, 10)
    assert s.verify(r, seen, t_now=10)
    assert not s.verify(r, seen, t_now=11)       # replayed nonce


def test_tampered_outcome_rejected():
    s, seen = Signer(), set()
    r = s.sign(3, 1, 0.2, 10)
    r["y"] = 0.99                                # delegate edits its own score
    assert not s.verify(r, seen, t_now=10)


def test_forged_signature_from_other_key_rejected():
    s, attacker, seen = Signer(), Signer(), set()
    r = attacker.sign(3, 1, 0.99, 10)
    assert not s.verify(r, seen, t_now=10)


def test_stale_record_rejected():
    s, seen = Signer(), set()
    r = s.sign(3, 1, 0.8, 10)
    assert not s.verify(r, seen, t_now=10 + 2000, tau_max=1500)
