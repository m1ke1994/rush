import asyncio
from unittest.mock import MagicMock, patch
import pytest
from app.collector import Collector


@pytest.mark.asyncio
async def test_canvas_timeout_updates_live_state_and_preserves_collector():
    storage=MagicMock()
    collector=Collector(storage)
    collector.runtime.current_match={"id":7,"score":"0:0","market_status":"WAITING"}
    async def hanging_reader(*args, **kwargs):
        await asyncio.sleep(120)
    with patch("app.collector.asyncio.wait_for", side_effect=TimeoutError):
        await collector._read_odds_background(object(),7,(0,0),"Barcelona","Juventus",hanging_reader)
    assert collector.runtime.current_match["market_status"]=="NOT_FOUND"
    assert collector.runtime.current_match["odds_reason"]=="TIMEOUT_18S"
    storage.odds.assert_called_once()
    assert collector.runtime.errors==0


@pytest.mark.asyncio
async def test_canvas_timeout_does_not_overwrite_new_match():
    storage=MagicMock()
    collector=Collector(storage)
    collector.runtime.current_match={"id":8,"score":"0:0","market_status":"AVAILABLE"}
    with patch("app.collector.asyncio.wait_for", side_effect=TimeoutError):
        await collector._read_odds_background(object(),7,(0,0),"A","B",None)
    assert collector.runtime.current_match["market_status"]=="AVAILABLE"
