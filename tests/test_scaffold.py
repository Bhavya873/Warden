def test_warden_core_importable():
    import warden_core

    assert warden_core.ping() == "pong"
