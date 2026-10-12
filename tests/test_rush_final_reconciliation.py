"""Final-score reconciliation must never fabricate goals or overwrite history."""
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.final_results import (
    eligible_for_reconciliation, is_final_status, read_verified_final, safe_match_url,
)
from app.storage import Storage

LEAGUE="https://example.test/ru/live/fifa/2986291-fc-26-5x5-rush-superleague"
MATCH_URL=LEAGUE+"/760881234-home-away"


def test_final_results_and_goals_are_independent():
    with TemporaryDirectory() as folder:
        db=Storage(Path(folder)/"rush.sqlite3")
        mid=db.upsert_match("760881234","rush","A","B",status="UPCOMING",match_url=MATCH_URL)
        db.set_match(mid,status="INTERRUPTED",started_at="2026-10-12T08:00:00+00:00",
                     first_half_score="2:1",data_complete=0)
        db.goal(mid,1,"A","0:0","1:0","1","01:22","HIGH")
        assert db.finalize_observed_match(mid,"3:4") is True
        m=db.detail(mid)["match"]
        assert (m["status"],m["final_score"],m["second_half_score"],m["winner"])==("FINISHED","3:4","1:3","B")
        assert m["data_complete"]==0
        assert len(db.detail(mid)["goals"])==1
        assert db.calculated_standings()["matches_count"]==1
        assert db.finalize_observed_match(mid,"9:0") is False
        db.upsert_match("760881234","rush","A","B",status="UPCOMING")
        assert db.detail(mid)["match"]["status"]=="FINISHED"
        assert db.detail(mid)["match"]["match_url"]==MATCH_URL


def test_legacy_db_migrates_without_losing_existing_rows():
    import sqlite3
    with TemporaryDirectory() as folder:
        path=Path(folder)/"legacy.sqlite"
        with closing(sqlite3.connect(path)) as con:
            con.execute("""CREATE TABLE matches (
                id INTEGER PRIMARY KEY, external_match_id TEXT NOT NULL UNIQUE,
                league TEXT NOT NULL, team_1 TEXT NOT NULL, team_2 TEXT NOT NULL,
                scheduled_at TEXT,started_at TEXT,finished_at TEXT,first_half_score TEXT,
                second_half_score TEXT,final_score TEXT,winner TEXT,total_goals INTEGER,
                status TEXT NOT NULL,data_complete INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL)""")
            con.execute("INSERT INTO matches(external_match_id,league,team_1,team_2,status,created_at) VALUES('old','rush','A','B','LIVE','2026-10-12')")
            con.commit()
        db=Storage(path)
        assert db.list_matches()[0]["external_match_id"]=="old"
        assert "match_url" in db.list_matches()[0]


def test_only_explicit_finished_text_is_accepted():
    assert is_final_status("Игра завершена 07:03")
    assert is_final_status("Матч завершён")
    assert not is_final_status("Второй тайм 07:04")
    assert not is_final_status("Ставки до начала игры")


def test_reconcile_only_known_match_links_and_old_or_interrupted():
    record={"id":1,"external_match_id":"760881234","status":"INTERRUPTED","final_score":None,"match_url":MATCH_URL}
    assert safe_match_url(record,LEAGUE)==MATCH_URL
    assert eligible_for_reconciliation(record)
    assert not eligible_for_reconciliation(record,current_id=1)
    assert safe_match_url({**record,"match_url":"https://evil.test/match"},LEAGUE) is None
    assert safe_match_url({**record,"match_url":LEAGUE+"/999-other"},LEAGUE) is None
    assert not eligible_for_reconciliation({**record,"status":"UPCOMING"})


def test_read_final_requires_explicit_marker():
    def locator(text):
        leaf=MagicMock()
        leaf.first=leaf
        leaf.inner_text=AsyncMock(return_value=text)
        leaf.count=AsyncMock(return_value=0)
        leaf.is_visible=AsyncMock(return_value=False)
        return leaf
    page=MagicMock()
    page.locator=lambda selector: locator("Игра завершена" if selector=="[data-test=\"betting-scoreboard-status\"]" else "")
    async def board_reader(page):
        return SimpleNamespace(score=SimpleNamespace(team1=4,team2=2))
    result=asyncio.run(read_verified_final(page,SimpleNamespace(game_over_panel=".finish"),board_reader))
    assert result=="4:2"
