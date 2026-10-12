"""Reliable Rush card parser: countdown and elapsed game clocks are distinct."""
import re
from urllib.parse import urljoin

MATCH_ID = re.compile(r"/(\\d+)-[^/?#]+(?:[?#].*)?$")
CLOCK = re.compile(r"(?<!\\d)(\\d{1,3}):(\\d{2})(?!\\d)")
FINISHED = ("заверш", "окончен", "finished", "full time", "game over")
LIVE_PERIOD = ("1-й тайм", "2-й тайм", "первый тайм", "второй тайм",
               "1st half", "2nd half", "перерыв", "half time", "break")


def parse_time_to_seconds(value):
    if not value:
        return None
    match = CLOCK.search(str(value))
    if not match:
        return None
    mins, secs = map(int, match.groups())
    return mins * 60 + secs if secs < 60 else None


def classify_match(period, time, score_1, score_2, live_marker=False):
    """Never mistake a nonzero score for a confirmed LIVE timer."""
    period = (period or "").strip().lower()
    if any(mark in period for mark in FINISHED):
        return "FINISHED"
    if any(mark in period for mark in LIVE_PERIOD) or live_marker:
        return "LIVE"
    # Score proves that play started, not that it is still running.
    if (score_1 is not None and score_2 is not None and score_1 + score_2 > 0):
        return "STARTED_UNKNOWN"
    # 00:00 with no explicit period is ambiguous; subsequent scans can clarify.
    if parse_time_to_seconds(time) is not None and not period:
        return "UPCOMING"
    return "UNKNOWN"


async def _text(card, selector):
    if not selector:
        return None
    try:
        locator = card.locator(selector).first
        if not await locator.count():
            return None
        value = (await locator.inner_text(timeout=1250)).strip()
        return value or None
    except Exception:
        return None


async def _first_text(card, selectors):
    for selector in selectors:
        value = await _text(card, selector)
        if value:
            return value
    return None


async def collect_league_matches(page, logger=None):
    from xbet_config import SELECTORS
    cards = page.locator(".dashboard-game-block")
    total = await cards.count()
    result = []
    if logger:
        logger(f"Найдены карточки матчей: {total}")
    for index in range(total):
        try:
            card = cards.nth(index)
            link = card.locator("a.dashboard-game-block__link").first
            href = await link.get_attribute("href") if await link.count() else None
            team_1 = await _text(card, '[data-test="betting-dashboard-game-first-team"] .dashboard-game-team-info__name')
            team_2 = await _text(card, '[data-test="betting-dashboard-game-second-team"] .dashboard-game-team-info__name')
            scores = card.locator(".ui-game-scores__item--total .ui-game-scores__num")
            raw = [(await scores.nth(i).inner_text()).strip() for i in range(min(await scores.count(), 2))]
            score_1, score_2 = (int(raw[0]), int(raw[1])) if len(raw) == 2 and all(s.isdigit() for s in raw) else (None, None)
            # The selectors from new_bot were implemented for the real dashboard.
            time = await _first_text(card, (
                SELECTORS.match_time,
                '[data-test="betting-dashboard-game-info"] .dashboard-game-info__time',
                '.dashboard-game-info__time',
                '.ui-game-timer__time .ui-game-timer__label',
                '.ui-game-timer__label',
            ))
            period = await _first_text(card, (
                SELECTORS.match_period,
                '.dashboard-game-info__period',
                '[data-test="betting-dashboard-game-info"] .ui-caption',
            ))
            live = bool(await card.locator('.dashboard-game-action-bar[islive="true"], [islive="true"]').count())
            state = classify_match(period, time, score_1, score_2, live)
            match = MATCH_ID.search(href or "")
            info = {
                "match_id": match.group(1) if match else None,
                "team_1": team_1, "team_2": team_2,
                "score_1": score_1, "score_2": score_2,
                "game_time": time, "period": period,
                "is_live": state == "LIVE", "is_upcoming": state == "UPCOMING",
                "status": state, "href": href,
                "url": urljoin(page.url, href) if href else None,
            }
            result.append(info)
            if logger:
                logger(f"[{index+1}] {team_1 or '?'} — {team_2 or '?'}; "
                       f"счёт={score_1}:{score_2}; время={time or '?'}; "
                       f"период={period or '?'}; статус={state}; ID={info['match_id'] or '?'}")
        except Exception as exc:
            if logger:
                logger(f"Карточка {index+1} не разобрана: {type(exc).__name__}: {exc}")
    return result


def select_match(matches, excluded_ids=()):
    """Prefer nearest countdown; never pick a stale match due to a missing clock."""
    excluded = {str(x) for x in excluded_ids}
    valid = [m for m in matches if m.get("href") and m.get("team_1") and m.get("team_2")
             and str(m.get("match_id") or m.get("href")) not in excluded]
    # A truly unknown card may be the nearest: wait for hydration instead of guessing.
    unknown = [m for m in valid if m.get("status") in ("UNKNOWN", "STARTED_UNKNOWN")]
    upcoming = [(m, parse_time_to_seconds(m.get("game_time"))) for m in valid
                if m.get("is_upcoming") or m.get("status") == "UPCOMING"]
    upcoming = [(m, sec) for m, sec in upcoming if sec is not None]
    if upcoming:
        candidate, seconds = min(upcoming, key=lambda x: x[1])
        # A card missing its countdown could start earlier; do not select blindly.
        if unknown and not any(m.get("is_live") for m in unknown):
            return None, "карточки без времени/статуса: ожидаем уточнения DOM"
        return candidate, f"ближайший матч до начала: {seconds} сек"
    live = [(m, parse_time_to_seconds(m.get("game_time"))) for m in valid if m.get("is_live")]
    live = [(m, sec) for m, sec in live if sec is not None and sec < 410]
    if live and not unknown:
        chosen, seconds = min(live, key=lambda x: x[1])
        return chosen, f"резерв: подтверждённый LIVE, сыграно {seconds} сек"
    return None, "время/период не подтверждены; продолжаем наблюдать лигу"
