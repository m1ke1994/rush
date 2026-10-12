from app.league_parser import classify_match, select_match, parse_time_to_seconds


def card(mid, time, score=(0, 0), status=None, upcoming=False, live=False):
    return {
        'match_id': str(mid), 'href': f'/ru/live/fifa/{mid}-match',
        'team_1': f'Team {mid}', 'team_2': 'Opponent',
        'game_time': time, 'score_1': score[0], 'score_2': score[1],
        'status': status, 'is_upcoming': upcoming, 'is_live': live,
    }


def test_regression_liverpool_2_1_without_time_is_not_selected():
    matches = [
        card(760868896, None, status='UNKNOWN'),
        card(760867192, None, score=(2, 1), status='STARTED_UNKNOWN'),
        card(760870440, None, status='UNKNOWN'),
    ]
    chosen, reason = select_match(matches)
    assert chosen is None
    assert 'время' in reason


def test_nearest_countdown_wins_despite_stale_started_card():
    matches = [
        card(760868896, '02:15', status='UPCOMING', upcoming=True),
        card(760867192, None, score=(2, 1), status='STARTED_UNKNOWN'),
        card(760870440, '00:31', status='UPCOMING', upcoming=True),
    ]
    chosen, _ = select_match(matches)
    assert chosen['match_id'] == '760870440'


def test_score_is_not_proof_of_live_status():
    assert classify_match(None, None, 2, 1) == 'STARTED_UNKNOWN'
    assert classify_match(None, None, 0, 0) == 'UNKNOWN'
    assert classify_match(None, '00:28', 0, 0) == 'UPCOMING'
    assert classify_match('Игра завершена', '07:00', 2, 1) == 'FINISHED'
    assert classify_match('2-й тайм', '06:51', 2, 1) == 'LIVE'


def test_live_0650_never_selected_again():
    matches = [card(1, '07:01', status='LIVE', live=True),
               card(2, '01:30', status='UPCOMING', upcoming=True)]
    assert select_match(matches)[0]['match_id'] == '2'
    assert parse_time_to_seconds('06:50') == 410


def test_retired_match_does_not_repeat():
    matches = [card(1, '00:12', status='UPCOMING', upcoming=True),
               card(2, '00:48', status='UPCOMING', upcoming=True)]
    assert select_match(matches, excluded_ids={'1'})[0]['match_id'] == '2'
