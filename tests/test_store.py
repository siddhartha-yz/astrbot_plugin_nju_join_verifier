from nju_join_verifier.store import ReviewStore


def test_store(tmp_path):
    s = ReviewStore(tmp_path / "r.sqlite3")
    s.record(flag="f", group_id="g", user_id="u", outcome="manual", detail="format")
    assert s.get("f").outcome == "manual"
    s.close()
