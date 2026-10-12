from app.league_parser import classify_match, select_match, parse_time_to_seconds


def card(match_id, clock, period, score, state):
    return {"match_id":str(match_id), "href":f"/rush/{match_id}-game",
            "team_1":"A", "team_2":"B", "game_time":clock,
            "period":period, "score_1":score[0], "score_2":score[1],
            "is_live":state=="LIVE", "is_upcoming":state=="UPCOMING",
            "status":state}


def test_numeric_period_is_not_a_live_indicator():
    assert classify_match("03:04", "03:04", 0, 0) == "UPCOMING"
    assert classify_match("23:02", "23:02", 0, 0) == "UPCOMING"
    assert classify_match("04:43", "04:43", 1, 1) == "STARTED_UNKNOWN"


def test_real_four_card_fixture_prefers_manchester_city():
    fixtures=[
        card(760868896,"04:43",None,(1,1),"STARTED_UNKNOWN"),
        card(760873886,"23:02",None,(0,0),"UPCOMING"),
        card(760872016,"13:04",None,(0,0),"UPCOMING"),
        card(760870440,"03:04",None,(0,0),"UPCOMING"),
    ]
    chosen, reason = select_match(fixtures)
    assert chosen["match_id"]=="760870440"
    assert parse_time_to_seconds(chosen["game_time"]) == 184


def test_0650_handover_excludes_old_game():
    fixtures=[card("old","06:55","2-й тайм",(5,3),"LIVE"),
              card("new","00:20",None,(0,0),"UPCOMING")]
    selected,_=select_match(fixtures,excluded_ids={"old"})
    assert selected["match_id"]=="new"
