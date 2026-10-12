"""Regression checks for live-odds freshness metadata and refresh cycle."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime
from app.collector import Collector


def test_successful_read_updates_odds_timestamp_and_goal_number():
    store=MagicMock()
    monitor=Collector(store)
    monitor.runtime.current_match={"id":4,"score":"3:2","market_status":"WAITING"}
    async def fake_reader(*args,**kwargs):
        return SimpleNamespace(team1=1.66,team2=2.215,source="CANVAS_2D",locked_sides=())
    asyncio.run(monitor.store_odds(None,4,(3,2),"Barcelona","Juventus",fake_reader))
    live=monitor.runtime.current_match
    assert live["odds_1"]==1.66
    assert live["odds_2"]==2.215
    assert live["odds_goal_number"]==6
    assert live["odds_updated_at"].endswith("Z")
    assert datetime.fromisoformat(live["odds_updated_at"].replace("Z","+00:00"))


def test_failed_read_clears_stale_odds():
    store=MagicMock()
    monitor=Collector(store)
    monitor.runtime.current_match={"id":4,"score":"3:2","market_status":"AVAILABLE",
                                   "odds_1":1.83,"odds_2":1.99,"odds_updated_at":"2026-10-12T08:00:00Z"}
    async def unavailable(*args,**kwargs):
        raise RuntimeError("market not ready")
    asyncio.run(monitor.store_odds(None,4,(3,2),"Barcelona","Juventus",unavailable))
    live=monitor.runtime.current_match
    assert live["market_status"]=="NOT_FOUND"
    assert live["odds_1"] is None
    assert live["odds_2"] is None
    assert live["odds_updated_at"] is None
