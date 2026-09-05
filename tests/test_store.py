from nju_join_verifier.store import ReviewStore


def test_store(tmp_path):
    s = ReviewStore(tmp_path / "r.sqlite3")
    s.record(flag="f", group_id="g", user_id="u", outcome="manual", detail="format")
    assert s.get("f").outcome == "manual"
    s.close()


def test_failure_notification_queue_is_idempotent(tmp_path):
    s = ReviewStore(tmp_path / "r.sqlite3")
    s.queue_failure_notification(flag="f", group_id="g", user_id="u", result="mismatch")
    s.queue_failure_notification(flag="f", group_id="g", user_id="u", result="mismatch")
    pending = s.pending_failure_notifications(retry_after_seconds=0)
    assert len(pending) == 1
    assert pending[0].flag == "f"
    assert pending[0].user_id == "u"
    assert pending[0].result == "mismatch"

    s.mark_failure_notification_attempt("f", sent=True)
    assert s.pending_failure_notifications(retry_after_seconds=0) == []
    s.close()
