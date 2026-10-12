from pathlib import Path
from tempfile import TemporaryDirectory

from app.storage import Storage


def test_goal_uses_latest_recorded_odds_before_goal():
    with TemporaryDirectory() as tmp:
        db = Storage(Path(tmp) / "rush.sqlite3")
        mid = db.upsert_match("rush-001", "Rush", "A", "B")
        db.odds(mid, "0:0", 1.81, 2.05, "AVAILABLE", "CANVAS_2D")
        db.goal(mid, 1, "A", "0:0", "1:0", "1", "00:21", "HIGH")
        db.odds(mid, "0:0", 1.11, 9.99, "AVAILABLE", "CANVAS_2D")
        row = db.detail(mid)["goals"][0]
        assert row["team_1_odds"] == 1.81
        assert row["team_2_odds"] == 2.05


def test_absent_or_locked_odds_not_invented():
    with TemporaryDirectory() as tmp:
        db = Storage(Path(tmp) / "rush.sqlite3")
        mid = db.upsert_match("rush-002", "Rush", "A", "B")
        db.odds(mid, "0:0", 1.81, 2.05, "LOCKED", "CANVAS_2D")
        db.goal(mid, 1, "B", "0:0", "0:1", "1", "00:30", "HIGH")
        row = db.detail(mid)["goals"][0]
        assert row["team_1_odds"] is None
        assert row["team_2_odds"] is None


def test_clear_resets_related_data_but_keeps_database_reusable():
    with TemporaryDirectory() as tmp:
        db = Storage(Path(tmp) / "rush.sqlite3")
        mid = db.upsert_match("rush-003", "Rush", "A", "B")
        db.goal(mid, 1, "A", "0:0", "1:0", "1", "01:00", "HIGH")
        db.odds(mid, "0:0", 1.90, 1.99, "AVAILABLE", "CANVAS_2D")
        db.event(mid, "TEST", {"ok": True})
        deleted = db.clear_observations()
        assert deleted == {"matches": 1, "goal_events": 1, "odds_snapshots": 1, "match_events": 1}
        assert db.list_matches() == []
        assert db.all_goals() == []
        assert db.all_odds() == []
        assert db.calculated_standings()["table"] == []
        assert db.upsert_match("rush-003", "Rush", "A", "B") > 0
