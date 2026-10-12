from app.league_parser import select_match, parse_time_to_seconds


def fixture(ident, elapsed, live=True):
    return {"match_id": str(ident), "href": f"/ru/live/fifa/{ident}-rush",
            "team_1": "A", "team_2": "B", "game_time": elapsed,
            "is_live": live}


def test_never_select_expired_live_game():
    match, reason = select_match([fixture(1, "07:01"), fixture(2, "00:14")])
    assert match["match_id"] == "2"


def test_retired_game_never_reselected():
    match, reason = select_match([fixture(1, "06:42"), fixture(2, "01:10")], excluded_ids={"1"})
    assert match["match_id"] == "2"


def test_switch_to_nearest_upcoming():
    match, reason = select_match([fixture(1, "06:20"), fixture(2, "00:15", False),
                                  fixture(3, "00:42", False)])
    assert match["match_id"] == "2"


def test_no_fallback_to_old_match():
    match, reason = select_match([fixture(1, "07:01")])
    assert match is None
    assert parse_time_to_seconds("06:50") == 410
