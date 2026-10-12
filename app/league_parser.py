"""Rush league card parser.  A card is the whole dashboard-game-block, not its row."""
import re
from urllib.parse import urljoin

MATCH_ID = re.compile(r"/(\d+)-[^/?#]+(?:[?#].*)?$")

def parse_time_to_seconds(value: str) -> int | None:
    try:
        minutes, seconds = value.strip().split(':')
        minutes, seconds = int(minutes), int(seconds)
        return minutes * 60 + seconds if minutes >= 0 and 0 <= seconds < 60 else None
    except (ValueError, AttributeError):
        return None

async def _text(card, selector):
    item=card.locator(selector).first
    if not await item.count(): return None
    value=(await item.inner_text()).strip()
    return value or None

async def _first_text(card, selectors):
    for selector in selectors:
        value=await _text(card,selector)
        if value:return value
    return None

async def collect_league_matches(page, logger=None):
    cards=page.locator('.dashboard-game-block'); total=await cards.count(); result=[]
    if logger: logger(f'Найдены карточки матчей: {total}')
    for index in range(total):
        try:
            card=cards.nth(index); link=card.locator('a.dashboard-game-block__link').first
            href=await link.get_attribute('href') if await link.count() else None
            t1=await _text(card,'[data-test="betting-dashboard-game-first-team"] .dashboard-game-team-info__name')
            t2=await _text(card,'[data-test="betting-dashboard-game-second-team"] .dashboard-game-team-info__name')
            scores=card.locator('.ui-game-scores__item--total .ui-game-scores__num')
            raw=[(await scores.nth(n).inner_text()).strip() for n in range(await scores.count())]
            s1,s2=(int(raw[0]),int(raw[1])) if len(raw)>=2 and all(x.isdigit() for x in raw[:2]) else (None,None)
            game_time=await _first_text(card,(
                '[data-test="betting-dashboard-game-info"] .dashboard-game-info__time',
                '.dashboard-game-block__info .dashboard-game-info__time',
                '.dashboard-game-info__time',
            ))
            live=card.locator('.dashboard-game-action-bar[islive="true"], [islive="true"]'); is_live=bool(await live.count())
            match=MATCH_ID.search(href or '')
            # A nonzero displayed score is only a fallback signal when the site
            # omits islive; it avoids silently discarding the only usable card.
            inferred_live=not is_live and s1 is not None and s2 is not None and (s1+s2)>0
            data={'match_id':match.group(1) if match else None,'team_1':t1,'team_2':t2,'score_1':s1,'score_2':s2,'game_time':game_time,'is_live':is_live or inferred_live,'href':href,'url':urljoin(page.url,href) if href else None,'status':'LIVE' if (is_live or inferred_live) else 'UNKNOWN','live_source':'attribute' if is_live else ('nonzero_score_fallback' if inferred_live else 'unknown')}
            result.append(data)
            if logger: logger(f'[{index+1}] {t1 or "?"} — {t2 or "?"}; счёт={s1}:{s2}; время={game_time or "?"}; LIVE={is_live}; ID={data["match_id"] or "?"}; URL={href or "?"}')
        except Exception as error:
            if logger: logger(f'Карточка {index+1} не разобрана: {type(error).__name__}: {error}')
    return result

def select_match(matches):
    valid=[m for m in matches if m.get('href') and m.get('team_1') and m.get('team_2')]
    live=[m for m in valid if m['is_live']]
    upcoming=[m for m in valid if not m['is_live'] and parse_time_to_seconds(m.get('game_time')) is not None]
    if upcoming:
        return min(upcoming,key=lambda x:parse_time_to_seconds(x['game_time'])),'предстоящий матч с минимальным временем до начала'
    if live: return min(live,key=lambda x:parse_time_to_seconds(x.get('game_time')) or 10**9),'предстоящих нет; LIVE с минимальным игровым временем'
    # No status signal must be diagnostic, not a deadlock.  Open a concrete
    # card and mark its collected history incomplete until scoreboard confirms it.
    if valid:return valid[0],'статус/таймер недоступны; выбран доступный матч с неполной историей'
    return None,'нет карточек с командами и настоящей ссылкой'
