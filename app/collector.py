"""Read-only Rush lifecycle using the copied new_bot browser/auth/league stack."""
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from dotenv import load_dotenv
from .config import ROOT, LEAGUE_NAME
from .goal_tracker import GoalTracker, Observation, score_text
from .league_parser import collect_league_matches, select_match
from .periods import determine_match_half

@dataclass
class Runtime:
    running: bool=False; stage: str="Ожидание запуска"; browser: str="Остановлен"
    auth: str="Не проверена"; league: str="Не подключена"; current_match: dict|None=None
    errors: int=0; logs: list[dict]=field(default_factory=list)

class Collector:
    def __init__(self, storage):
        self.storage=storage; self.runtime=Runtime(); self.stop_event=asyncio.Event(); self.task=None; self.manager=None; self.league_page=None; self.upcoming_ids=set(); self.pending_next_id=None
    def snapshot(self): return asdict(self.runtime)
    def log(self, text):
        item={"time":datetime.now().strftime("%H:%M:%S"),"message":text}; self.runtime.logs=(self.runtime.logs+[item])[-120:]; print(f"[{item['time']}] {text}")
    async def start(self):
        if self.task and not self.task.done(): return False
        self.stop_event=asyncio.Event(); self.task=asyncio.create_task(self.run()); return True
    async def stop(self):
        self.stop_event.set(); self.runtime.stage="Остановка наблюдения"
        if self.task and not self.task.done():
            try: await asyncio.wait_for(self.task,12)
            except TimeoutError: self.task.cancel()
    async def run(self):
        self.runtime.running=True
        try:
            # new_bot's modules use the same .env selector model. The example is safe
            # fallback for manual login; a real .env overrides it.
            load_dotenv(ROOT/'.env',override=False); load_dotenv(ROOT/'.env.example',override=False)
            from auth import authorize
            from backend.app.browser.manager import BROWSER_MANAGER
            from backend.app.browser.scoreboard import read_scoreboard, ScoreReadError
            from backend.app.browser.canvas_2d_adapter import read_next_goal_odds
            from xbet_config import SELECTORS, URL_CONFIG
            self.manager=BROWSER_MANAGER; self.runtime.stage="Запуск Chromium"; self.log("Запуск Chromium.")
            page=await self.manager.start(); self.runtime.browser="Chromium запущен"
            self.runtime.stage="Проверка авторизации"; self.log("Проверка авторизации. Войдите вручную в Chromium при необходимости.")
            while not self.stop_event.is_set():
                result=await authorize(page,self.stop_event)
                if result.get('status')=='AUTHORIZED': self.runtime.auth="Авторизация подтверждена"; self.log("Авторизация подтверждена."); break
                self.runtime.auth="Требуется ручная авторизация"; self.runtime.stage="Ожидание ручной авторизации"; self.log("Маркер RUB не найден; ожидаю ручной вход."); await self.wait(2)
            while not self.stop_event.is_set():
                self.runtime.stage="Поиск ближайшего матча"; self.runtime.league="Переход в FC 26. 5x5 Rush"
                await page.goto(URL_CONFIG.next_goal_league_url,wait_until='domcontentloaded',timeout=30000); self.runtime.league="Лига подключена"
                matches=await collect_league_matches(page,self.log)
                target=self.pending_next_id
                item=next((m for m in matches if str(m.get('match_id'))==target and m.get('url')),None) if target else None
                self.pending_next_id=None
                self.upcoming_ids.update(str(m['match_id']) for m in matches if not m['is_live'] and m.get('match_id'))
                if item: reason='следующий матч начал игру; приоритет немедленного переключения'
                else: item,reason=select_match(matches) self.log(f"Причина выбора: {reason}.")
                if not item: self.runtime.stage="Ожидание ближайшего матча"; await self.wait(3); continue
                self.log(f"Выбран матч: {item.get('team_1')} — {item.get('team_2')}.")
                try:
                    self.runtime.stage="Открытие матча"; self.log(f"Открываем: {item['url']}")
                    await page.goto(item['url'],wait_until='domcontentloaded',timeout=30000); self.log("Переход выполнен; ожидаем табло.")
                    await self.observe(page,item,read_scoreboard,ScoreReadError,read_next_goal_odds,SELECTORS)
                except Exception as exc: self.runtime.errors+=1; self.log(f"Ошибка перехода/наблюдения: {type(exc).__name__}: {exc}"); await self.wait(2)
        except asyncio.CancelledError: raise
        except Exception as exc: self.runtime.errors+=1; self.runtime.stage="Ошибка жизненного цикла"; self.log(f"Ошибка жизненного цикла: {type(exc).__name__}: {exc}")
        finally:
            if self.manager:
                try: await self.manager.stop()
                except Exception: pass
            self.runtime.running=False; self.runtime.browser="Остановлен"
            if self.runtime.stage!="Ошибка жизненного цикла": self.runtime.stage="Остановлено"
            self.log("Наблюдение остановлено.")
    async def observe(self,page,item,read_scoreboard,score_error,read_odds,selectors):
        external=str(item.get('match_id') or item.get('href') or page.url)
        mid=self.storage.upsert_match(external,LEAGUE_NAME,item.get('team_1') or 'Не определено',item.get('team_2') or 'Не определено','LIVE' if item.get('is_live') else 'UPCOMING',item.get('game_time'))
        tracker=None; first_half=None; self.runtime.stage="Ожидание начала матча"
        # A separate tab monitors the league while the main tab reads the active scoreboard.
        # We never wait for the previous match to finish once a previously upcoming match starts.
        monitor=await page.context.new_page()
        last_monitor=0.0
        last_odds_score=None
        try:
            await monitor.goto(__import__('xbet_config').URL_CONFIG.next_goal_league_url,wait_until='domcontentloaded',timeout=20000)
        except Exception as exc:
            self.log(f"Фоновый монитор лиги недоступен: {exc}")
        while not self.stop_event.is_set():
            try: board=await read_scoreboard(page)
            except score_error as exc: self.runtime.stage="Ожидание данных scoreboard"; self.log(f"Scoreboard временно недоступен: {exc}"); await self.wait(.7); continue
            # The status block contains both text (e.g. "Игра завершена") and
            # the clock. Read them separately: treating the status as a timer
            # caused completed matches to remain in the observer forever.
            try:
                scoreboard_status=(await page.locator('[data-test="betting-scoreboard-status"]').first.inner_text()).strip()
            except Exception:
                scoreboard_status=board.period or ''
            try:
                game_time=(await page.locator('[data-test="betting-scoreboard-status-time"] .ui-game-timer__time .ui-game-timer__label').first.inner_text()).strip()
            except Exception:
                game_time=board.timer
            score=(board.score.team1,board.score.team2); period,period_reason=determine_match_half(game_time,scoreboard_status)
            if tracker is None:
                tracker=GoalTracker(score); complete=score==(0,0)
                self.storage.set_match(mid,status='LIVE',started_at=datetime.utcnow().isoformat(),data_complete=int(complete))
                self.storage.event(mid,'MATCH_STARTED',{'score':score_text(score),'period':period,'complete':complete})
                self.log(f"Матч начался/подключён: {board.team1} — {board.team2}. Счёт {score_text(score)}.")
            self.runtime.stage="Наблюдение выполняется"; self.runtime.current_match={'id':mid,'team1':board.team1,'team2':board.team2,'score':score_text(score),'half':period,'half_reason':period_reason,'time':game_time,'next_goal':sum(score)+1}
            self.storage.set_match(mid,team_1=board.team1,team_2=board.team2)
            # Read odds once per score change, not every 0.5 seconds.
            if last_odds_score != score:
                last_odds_score=score
                await self.store_odds(page,mid,score,board.team1,board.team2,read_odds)
            for kind,team,confidence,before in tracker.transition(Observation(score,period,board.timer)):
                if kind=='GOAL':
                    scorer=board.team1 if team==1 else board.team2; self.storage.goal(mid,sum(score),scorer,score_text(before),score_text(score),self.half(period),game_time or None,confidence); self.log(f"Гол команды {scorer}. Счёт {score_text(score)}.")
                else:
                    self.storage.event(mid,'MISSED_SCORE_INTERVAL' if kind=='GAP' else kind,{'before':score_text(before),'after':score_text(score),'period':period}); self.storage.set_match(mid,data_complete=0); self.log(f"Недостоверный переход {score_text(before)} → {score_text(score)} сохранён.")
            low=period.lower()
            if first_half is None and any(x in low for x in ('перерыв','half time','тайм заверш')):
                first_half=score; self.storage.set_match(mid,first_half_score=score_text(score)); self.log(f"Первый тайм завершён: {score_text(score)}.")
            if await self.finished(page,scoreboard_status,selectors):
                self.finish(mid,score,first_half,board.team1,board.team2); self.log(f"Матч завершён. Итоговый счёт {score_text(score)}."); self.runtime.current_match=None; await monitor.close(); return
            # Timer/score on the league cards is a fallback; never treat a completed
            # old match as a newly started match.
            loop_time=asyncio.get_running_loop().time()
            if loop_time-last_monitor>=2.0:
                last_monitor=loop_time
                try:
                    await monitor.reload(wait_until='domcontentloaded',timeout=6500)
                    all_matches=await collect_league_matches(monitor)
                    for candidate in all_matches:
                        cid=str(candidate.get('match_id') or '')
                        if cid and not candidate.get('is_live'):
                            self.upcoming_ids.add(cid)
                    newly_live=[m for m in all_matches if str(m.get('match_id') or '') in self.upcoming_ids
                                and str(m.get('match_id'))!=str(item.get('match_id'))
                                and m.get('is_live') and m.get('score_1') in (None,0)
                                and m.get('score_2') in (None,0) and m.get('url')]
                    if newly_live:
                        next_match=newly_live[0]
                        self.pending_next_id=str(next_match['match_id'])
                        self.storage.event(mid,'SWITCH_TO_NEXT_MATCH',{'next_id':next_match['match_id'],'last_score':score_text(score)})
                        self.storage.set_match(mid,status='INTERRUPTED',data_complete=0)
                        self.log(f"Новый матч начался: {next_match['team_1']} — {next_match['team_2']}; переключение без ожидания финального свистка.")
                        self.runtime.current_match=None
                        await monitor.close()
                        return
                except Exception as exc:
                    self.log(f"Проверка следующего матча: {type(exc).__name__}: {exc}")
            await self.wait(.5)
    async def store_odds(self,page,mid,score,t1,t2,reader):
        try:
            self.log(f'Поиск рынка «Следующий гол №{sum(score)+1}» и чтение Canvas 2D.')
            async def market_log(event,message): self.log(f'{event}: {message}')
            odds=await reader(page,t1,t2,score[0],score[1],logger=market_log); locked=bool(getattr(odds,'locked_sides',()))
            status='LOCKED' if locked else 'AVAILABLE'
            self.storage.odds(mid,score_text(score),float(odds.team1),float(odds.team2),status,getattr(odds,'source','CANVAS_2D'))
            if self.runtime.current_match is not None:
                self.runtime.current_match['odds_1']=float(odds.team1); self.runtime.current_match['odds_2']=float(odds.team2)
                self.runtime.current_match['market_status']=status; self.runtime.current_match['canvas_status']='Активен'
                self.runtime.current_match['odds_source']=getattr(odds,'source','CANVAS_2D')
            self.log(f'Коэффициенты сохранены: {t1}={odds.team1}; {t2}={odds.team2}.')
        except Exception as exc:
            reason=type(exc).__name__; self.storage.odds(mid,score_text(score),None,None,'NOT_FOUND','CANVAS_2D',reason)
            if self.runtime.current_match is not None:
                self.runtime.current_match['odds_1']=None; self.runtime.current_match['odds_2']=None
                self.runtime.current_match['market_status']='NOT_FOUND'; self.runtime.current_match['canvas_status']='Ожидание/нет данных'; self.runtime.current_match['odds_reason']=reason
            self.log(f'Рынок «Следующий гол» не прочитан: {reason}: {exc}')
    async def finished(self,page,period,selectors):
        if any(x in period.lower() for x in ('заверш','окончен','game over','finished')): return True
        try:
            p=page.locator(selectors.game_over_panel).first; return bool(await p.count() and await p.is_visible())
        except Exception: return False
    @staticmethod
    def half(period): return period if period in ('1','2') else 'UNKNOWN'
    def finish(self,mid,score,first,t1,t2):
        second=None if first is None else (score[0]-first[0],score[1]-first[1]); winner='Ничья' if score[0]==score[1] else (t1 if score[0]>score[1] else t2)
        self.storage.set_match(mid,status='FINISHED',finished_at=datetime.utcnow().isoformat(),final_score=score_text(score),first_half_score=score_text(first) if first else None,second_half_score=score_text(second) if second else None,winner=winner,total_goals=sum(score))
    async def wait(self,seconds):
        try: await asyncio.wait_for(self.stop_event.wait(),seconds)
        except TimeoutError: pass
