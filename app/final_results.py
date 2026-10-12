"""Read-only delayed final-result reconciliation for Rush matches.

An abandoned match is never assumed finished merely because the clock reached
06:50 or 07:00. Only an explicit completion marker on its match page allows
the final score to be committed. Goals/odds are not synthesized from results.
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse


FINISH_MARKERS = ("игра завершена", "матч заверш", "окончен", "finished", "game over", "full time")


def is_final_status(text):
    return any(word in str(text or "").casefold() for word in FINISH_MARKERS)


def eligible_for_reconciliation(match, current_id=None, clock=None):
    if match.get("id")==current_id or match.get("final_score"):
        return False
    if match.get("status")=="INTERRUPTED":
        return True
    if match.get("status")!="LIVE":
        return False
    # Recover an orphaned LIVE match after a restart, but never revisit the
    # current LIVE page or a match that started just moments ago.
    try:
        started=datetime.fromisoformat(match["started_at"].replace("Z","+00:00"))
        if started.tzinfo is None:
            started=started.replace(tzinfo=timezone.utc)
        return ((clock or datetime.now(timezone.utc))-started).total_seconds() >= 600
    except (TypeError,ValueError,KeyError):
        return False


def safe_match_url(match, league_url):
    url=match.get("match_url")
    if not url:
        return None
    parsed=urlparse(url)
    base=urlparse(league_url)
    if parsed.scheme not in ("http","https") or parsed.netloc!=base.netloc:
        return None
    if not parsed.path.startswith(base.path.rstrip("/")+"/"):
        return None
    segment=parsed.path.rsplit("/",1)[-1]
    if not segment.startswith(str(match["external_match_id"])+"-"):
        return None
    return url


async def read_verified_final(page, selectors, scoreboard_reader):
    # The main game and the results view may use different layouts.
    status=""
    try:
        label=page.locator('[data-test="betting-scoreboard-status"]').first
        status=await label.inner_text(timeout=1500)
    except Exception:
        pass
    complete=is_final_status(status)
    try:
        game_over=page.locator(selectors.game_over_panel).first
        if await game_over.count() and await game_over.is_visible():
            complete=True
    except Exception:
        pass
    if not complete:
        return None
    board=await asyncio.wait_for(scoreboard_reader(page),timeout=5)
    a,b=board.score.team1,board.score.team2
    if not isinstance(a,int) or not isinstance(b,int) or min(a,b)<0:
        return None
    return f"{a}:{b}"


class FinalResultReconciler:
    """Uses a separate page, never navigating away from the active match."""
    def __init__(self, storage, manager, log, stop_event, current_match, *, league_url, selectors, scoreboard_reader):
        self.storage=storage
        self.manager=manager
        self.log=log
        self.stop_event=stop_event
        self.current_match=current_match
        self.league_url=league_url
        self.selectors=selectors
        self.scoreboard_reader=scoreboard_reader
        self._attempts={}

    async def run(self):
        tab=None
        try:
            # Wait until the main browser has settled; never steal the active tab.
            while not self.stop_event.is_set():
                pending=self.storage.pending_final_results()
                active_id=self.current_match()
                pending=[m for m in pending if eligible_for_reconciliation(m,active_id)]
                pending=[m for m in pending if safe_match_url(m,self.league_url)]
                if pending:
                    if tab is None or tab.is_closed():
                        context=await self.manager.ensure_browser()
                        tab=await context.new_page()
                        try:
                            active_page=self.manager.page
                            if active_page is not None and not active_page.is_closed():
                                await active_page.bring_to_front()
                        except Exception:
                            pass
                    for match in pending:
                        if self.stop_event.is_set():
                            break
                        identifier=match["id"]
                        # No fast retry storm if the bookmaker is slow or unavailable.
                        loop_now=asyncio.get_running_loop().time()
                        if loop_now-self._attempts.get(identifier,-1000)<20:
                            continue
                        self._attempts[identifier]=loop_now
                        url=safe_match_url(match,self.league_url)
                        try:
                            await tab.goto(url,wait_until="domcontentloaded",timeout=10000)
                            result=await read_verified_final(tab,self.selectors,self.scoreboard_reader)
                            if result and self.storage.finalize_observed_match(identifier,result,source="VERIFIED_MATCH_PAGE"):
                                self._attempts.pop(identifier,None)
                                self.log(f"Финальный результат подтверждён: {match['team_1']} — {match['team_2']} {result}.")
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            self.log(f"Фоновая проверка финала {match['external_match_id']}: {type(exc).__name__}.")
                        await asyncio.sleep(.1)
                try:
                    await asyncio.wait_for(self.stop_event.wait(),timeout=15)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log(f"Фоновая сверка финалов временно остановлена: {type(exc).__name__}: {exc}")
        finally:
            if tab is not None:
                try:
                    await asyncio.wait_for(tab.close(),timeout=3)
                except Exception:
                    pass
