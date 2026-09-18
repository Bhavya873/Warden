from warden_core import RingBuffer


def test_claim_and_is_claimed():
    rb = RingBuffer(capacity=4)
    assert rb.is_claimed((1, 1)) is False
    rb.claim((1, 1), 7, 10)
    assert rb.is_claimed((1, 1)) is True
    assert rb.is_claimed((2, 2)) is False


def test_release_clears_claim():
    rb = RingBuffer(capacity=4)
    rb.claim((1, 1), 7, 10)
    rb.release((1, 1))
    assert rb.is_claimed((1, 1)) is False


def test_ttl_expiry_via_tick():
    rb = RingBuffer(capacity=4)
    rb.claim((1, 1), 7, 5)
    rb.tick(4)
    assert rb.is_claimed((1, 1)) is True
    rb.tick(5)
    assert rb.is_claimed((1, 1)) is False
