from pathlib import Path
from tempfile import TemporaryDirectory

from app.goal_tracker import GoalTracker, Observation
from app.periods import determine_match_half
from app.storage import Storage


def test_rush_period_boundaries():
    assert determine_match_half("03:29")[0] == "1"
    assert determine_match_half("03:30")[0] == "UNKNOWN"
    assert determine_match_half("03:31")[0] == "2"
    assert determine_match_half("03:30", "Перерыв")[0] == "BREAK"
    assert determine_match_half("03:30", "2-й тайм")[0] == "2"


def test_goals_do_not_invent_missing_events():
    tracker = GoalTracker((0, 0))
    assert tracker.transition(Observation((1, 0), "1", "00:45"))[0][0] == "GOAL"
    assert tracker.transition(Observation((3, 1), "2", "04:20"))[0][0] == "GAP"


def test_standings_exclude_incomplete_matches():
    with TemporaryDirectory() as temp:
        db = Storage(Path(temp) / "rush.sqlite3")
        first = db.upsert_match("100", "rush", "A", "B")
        db.set_match(first, status="FINISHED", final_score="2:1", data_complete=1)
        second = db.upsert_match("101", "rush", "B", "C")
        db.set_match(second, status="INTERRUPTED", final_score="7:0", data_complete=0)
        result = db.calculated_standings()
        assert result["matches_count"] == 1
        assert result["table"][0]["team"] == "A"
        assert result["table"][0]["points"] == 3
