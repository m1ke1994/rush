from .league_parser import parse_time_to_seconds

def determine_match_half(game_time: str | None, scoreboard_status: str | None = None):
    """Rush periods are three minutes; status wins over the clock at 03:00."""
    status=(scoreboard_status or '').lower()
    if any(x in status for x in ('перерыв','break','half time')): return 'BREAK','Перерыв подтверждён статусом'
    if any(x in status for x in ('втор','2-й тайм','second half')): return '2','Второй тайм подтверждён статусом'
    seconds=parse_time_to_seconds(game_time)
    if seconds is None:return 'UNKNOWN','Игровой таймер недоступен или некорректен'
    if seconds < 180:return '1','По накопительному игровому времени < 03:00'
    if seconds == 180:return 'UNKNOWN','Граница 03:00: ожидается подтверждение статуса'
    return '2','По накопительному игровому времени > 03:00'
