from app.storage import Storage

def test_storage_deduplicates_goal_and_odds(tmp_path):
    db = Storage(tmp_path / "history.sqlite")
    mid = db.upsert_match("external-1", "Rush", "A", "B")
    db.goal(mid, 1, "team_1", "0:0", "1:0", "1", None, "HIGH")
    db.goal(mid, 1, "team_1", "0:0", "1:0", "1", None, "HIGH")
    db.odds(mid, "0:0", 1.8, 2.0, "AVAILABLE", "TEST")
    db.odds(mid, "0:0", 1.8, 2.0, "AVAILABLE", "TEST")
    detail = db.detail(mid)
    assert len(detail["goals"]) == 1
    assert len(detail["odds"]) == 1
