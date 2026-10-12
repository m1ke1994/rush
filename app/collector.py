"""Read-only Rush lifecycle using the copied new_bot browser/auth/league stack."""
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from dotenv import load_dotenv
from .config import ROOT, LEAGUE_NAME
from .goal_tracker import GoalTracker, Observation, score_text
from .league_parser import collect_league_matches, select_match, parse_time_to_seconds
from .periods import determine_match_half
from .final_results import FinalResultReconciler

@dataclass
class Runtime:
    running: bool=False; stage: str="Ожидание запуска"; browser: str="Остановлен"
    auth: str="Не проверена"; league: str="Не подключена"; current_match: dict|None=None
    errors: int=0; logs: list[dict]=field(default_factory=list)

class Collector:
    def __init__(self, storage):
        self.storage=storage; self.runtime=Runtime(); self.stop_event=asyncio.Event(); self.task=None; self.manager=None; self.league_page=None; self.upcoming_ids=set(); self.pending_next_id=None; self.retired_match_ids=set(); self._odds_task=None; self.finalizer_task=None
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
            finalizer=FinalResultReconciler(
                self.storage,self.manager,self.log,self.stop_event,
                lambda: (self.runtime.current_match or {}).get("id"),
                league_url=URL_CONFIG.next_goal_league_url,
                selectors=SELECTORS,scoreboard_reader=read_scoreboard)
            self.finalizer_task=asyncio.create_task(finalizer.run())
            while not self.stop_event.is_set():
                self.runtime.stage="Поиск ближайшего матча"; self.runtime.league="Переход в FC 26. 5x5 Rush"
                try:
                    await page.goto(URL_CONFIG.next_goal_league_url,wait_until='domcontentloaded',timeout=12000)
                    self.runtime.league="Лига подключена"
                except Exception as exc:
                    self.runtime.errors+=1
                    self.log(f"Лига не загрузилась ({type(exc).__name__}): {exc}; восстанавливаем вкладку.")
                    self.runtime.stage="Восстановление страницы лиги"
                    try:
                        page=await asyncio.wait_for(self.manager.recover_navigation(exc),timeout=20)
                    except Exception as recovery_error:
                        self.log(f"Восстановление навигации отложено: {type(recovery_error).__name__}: {recovery_error}")
                    await self.wait(3)
                    continue
                # Wait for SPA countdown labels instead of reloading the site.
                matches=[]
                for attempt in range(4):
                    try:
                        matches=await asyncio.wait_for(collect_league_matches(page, self.log if attempt==3 else None), timeout=8)
                    except TimeoutError:
                        self.log('Тайм-аут сканирования карточек лиги: повторим поиск.')
                        matches=[]
                    if any(m.get('is_upcoming') for m in matches):
                        break
                    if attempt<3:
                        await self.wait(.4)
                for candidate in matches:
                    if candidate.get('match_id') and candidate.get('url'):
                        self.storage.attach_match_url(candidate['match_id'],candidate['url'])
                    if candidate.get('match_id') and candidate.get('is_upcoming'):
                        self.storage.upsert_match(
                            str(candidate['match_id']), LEAGUE_NAME,
                            candidate.get('team_1') or 'Не определено',
                            candidate.get('team_2') or 'Не определено',
                            'UPCOMING', candidate.get('game_time'),candidate.get('url'))
                self.log(f"Сканирование: {len(matches)} карточек; "
                         f"{sum(bool(m.get('is_upcoming')) for m in matches)} с обратным отсчётом.")
                target=self.pending_next_id
                item=next((m for m in matches if str(m.get('match_id'))==target and m.get('url')),None) if target else None
                self.pending_next_id=None
                self.upcoming_ids.update(str(m['match_id']) for m in matches if m.get('is_upcoming') and m.get('match_id'))
                if item and (str(item.get('match_id')) in self.retired_match_ids or item.get('status') in ('FINISHED','STARTED_UNKNOWN')):
                    item=None
                if item: reason='следующий матч начал игру; приоритет немедленного переключения'
                else: item,reason=select_match(matches, excluded_ids=self.retired_match_ids)
                self.log(f"Причина выбора: {reason}.")
                if not item: self.runtime.stage="Ожидание ближайшего матча"; await self.wait(3); continue
                self.log(f"Выбран матч: {item.get('team_1')} — {item.get('team_2')}.")
                try:
                    self.runtime.stage="Открытие матча"; self.log(f"Открываем: {item['url']}")
                    await page.goto(item['url'],wait_until='domcontentloaded',timeout=30000); self.log("Переход выполнен; ожидаем табло.")
                    await asyncio.wait_for(self.observe(page,item,read_scoreboard,ScoreReadError,read_next_goal_odds,SELECTORS), timeout=540)
                except Exception as exc:
                    self.runtime.errors+=1
                    self.log(f"Ошибка перехода/наблюдения: {type(exc).__name__}: {exc}")
                    await self.wait(2)
                finally:
                    task=self._odds_task
                    self._odds_task=None
                    if task is not None and not task.done():
                        task.cancel()
                    aux=self.league_page
                    self.league_page=None
                    if aux is not None:
                        try:
                            await asyncio.wait_for(aux.close(),timeout=3)
                        except Exception:
                            pass
                    self.runtime.current_match=None
        except asyncio.CancelledError: raise
        except Exception as exc: self.runtime.errors+=1; self.runtime.stage="Ошибка жизненного цикла"; self.log(f"Ошибка жизненного цикла: {type(exc).__name__}: {exc}")
        finally:
            if self.finalizer_task is not None:
                self.finalizer_task.cancel()
                try: await asyncio.wait_for(self.finalizer_task,timeout=4)
                except (asyncio.CancelledError,TimeoutError): pass
                self.finalizer_task=None
            if self.manager:
                try: await self.manager.stop()
                except Exception: pass
            self.runtime.running=False; self.runtime.browser="Остановлен"
            if self.runtime.stage!="Ошибка жизненного цикла": self.runtime.stage="Остановлено"
            self.log("Наблюдение остановлено.")
    async def observe(self,page,item,read_scoreboard,score_error,read_odds,selectors):
        external=str(item.get('match_id') or item.get('href') or page.url)
        mid=self.storage.upsert_match(external,LEAGUE_NAME,item.get('team_1') or 'Не определено',item.get('team_2') or 'Не определено','LIVE' if item.get('is_live') else 'UPCOMING',item.get('game_time'),item.get('url'))
        tracker=None; first_half=None; last_first_half_score=None; self.runtime.stage="Ожидание начала матча"
        # A separate tab monitors the league while the main tab reads the active scoreboard.
        # We never wait for the previous match to finish once a previously upcoming match starts.
        monitor=await page.context.new_page()
        self.league_page=monitor
        last_monitor=0.0
        last_odds_score=None; scoreboard_failures=0; odds_task=None; last_odds_attempt=0.0
        try:
            await monitor.goto(__import__('xbet_config').URL_CONFIG.next_goal_league_url,wait_until='domcontentloaded',timeout=20000)
        except Exception as exc:
            self.log(f"Фоновый монитор лиги недоступен: {exc}")
        # new_page() focuses the observer tab; always show the actual game.
        await page.bring_to_front()
        while not self.stop_event.is_set():
            try: board=await asyncio.wait_for(read_scoreboard(page), timeout=4)
            except (score_error, TimeoutError) as exc:
                scoreboard_failures+=1
                self.runtime.stage="Ожидание данных scoreboard"
                if scoreboard_failures in (1,5,15):
                    self.log(f"Scoreboard ещё не готов ({scoreboard_failures}): {exc}")
                if scoreboard_failures>=15:
                    self.retired_match_ids.add(external)
                    self.storage.set_match(mid,status='INTERRUPTED',data_complete=0)
                    self.storage.event(mid,'SCOREBOARD_UNAVAILABLE',{'reason':str(exc)})
                    self.log("Табло недоступно: выбираем другой матч, без бесконечного ожидания.")
                    await monitor.close()
                    self.runtime.current_match=None
                    return
                await self.wait(.5)
                continue
            scoreboard_failures=0
            # The status block contains both text (e.g. "Игра завершена") and
            # the clock. Read them separately: treating the status as a timer
            # caused completed matches to remain in the observer forever.
            try:
                scoreboard_status=(await page.locator('[data-test="betting-scoreboard-status"]').first.inner_text(timeout=1200)).strip()
            except Exception:
                scoreboard_status=board.period or ''
            try:
                game_time=(await page.locator('[data-test="betting-scoreboard-status-time"] .ui-game-timer__time .ui-game-timer__label').first.inner_text(timeout=1200)).strip()
            except Exception:
                game_time=board.timer
            score=(board.score.team1,board.score.team2); period,period_reason=determine_match_half(game_time,scoreboard_status)
            if tracker is None:
                tracker=GoalTracker(score); complete=score==(0,0)
                self.storage.set_match(mid,status='LIVE',started_at=datetime.utcnow().isoformat(),data_complete=int(complete))
                self.storage.event(mid,'MATCH_STARTED',{'score':score_text(score),'period':period,'complete':complete})
                self.log(f"Матч начался/подключён: {board.team1} — {board.team2}. Счёт {score_text(score)}.")
            self.runtime.stage="Наблюдение выполняется"
            previous=self.runtime.current_match or {}
            current={'id':mid,'team1':board.team1,'team2':board.team2,'score':score_text(score),'half':period,'half_reason':period_reason,'time':game_time,'next_goal':sum(score)+1}
            if previous.get('id')==mid and previous.get('score')==score_text(score):
                current.update({k:previous[k] for k in ('odds_1','odds_2','market_status','canvas_status','odds_source','odds_reason','odds_updated_at','odds_goal_number') if k in previous})
            self.runtime.current_match=current
            self.storage.set_match(mid,team_1=board.team1,team_2=board.team2)
            # 06:50 is the handover deadline: do not wait for 07:00 or for
            # a potentially stale bookmaker completion banner. Retire this ID
            # before navigating, so the same match cannot be picked again.
            elapsed=parse_time_to_seconds(game_time)
            if await self.finished(page,scoreboard_status,selectors):
                self.retired_match_ids.add(external)
                self.finish(mid,score,first_half,board.team1,board.team2)
                self.log(f"Матч завершён. Итоговый счёт {score_text(score)}.")
                self.runtime.current_match=None
                await monitor.close()
                return
            if elapsed is not None and elapsed>=410:
                self.retired_match_ids.add(external)
                self.storage.set_match(mid,status='INTERRUPTED',data_complete=0)
                self.storage.event(mid,'HANDOVER_0650',{'score':score_text(score),'time':game_time})
                self.log(f"Достигнуто {game_time} (порог 06:50): немедленный переход к поиску следующего матча.")
                self.runtime.current_match=None
                await monitor.close()
                return
            # Reading Canvas/search may take >5s. It must not block scoreboard
            # observations or the 06:50 handover.
            current_time=asyncio.get_running_loop().time()
            # Odds vary during the SAME score. Refresh every 3 seconds when
            # available; retry failed reads after 6 seconds. Never overlap
            # Canvas reads on the same page.
            market_state=self.runtime.current_match.get('market_status')
            refresh_period=3.0 if market_state=='AVAILABLE' else 6.0
            score_changed=last_odds_score != score
            task_idle=odds_task is None or odds_task.done()
            if score_changed and odds_task is not None and not odds_task.done():
                odds_task.cancel()
                task_idle=False
            if task_idle and (score_changed or current_time-last_odds_attempt>=refresh_period):
                last_odds_score=score
                last_odds_attempt=current_time
                odds_task=asyncio.create_task(
                    self._read_odds_background(page,mid,score,board.team1,board.team2,read_odds)
                )
                self._odds_task=odds_task
            for kind,team,confidence,before in tracker.transition(Observation(score,period,board.timer)):
                if kind=='GOAL':
                    scorer=board.team1 if team==1 else board.team2; self.storage.goal(mid,sum(score),scorer,score_text(before),score_text(score),self.half(period),game_time or None,confidence); self.log(f"Гол команды {scorer}. Счёт {score_text(score)}.")
                else:
                    self.storage.event(mid,'MISSED_SCORE_INTERVAL' if kind=='GAP' else kind,{'before':score_text(before),'after':score_text(score),'period':period}); self.storage.set_match(mid,data_complete=0); self.log(f"Недостоверный переход {score_text(before)} → {score_text(score)} сохранён.")
            if period=='1':
                last_first_half_score=score
            elif period=='2' and first_half is None and last_first_half_score is not None:
                first_half=last_first_half_score
                self.storage.set_match(mid,first_half_score=score_text(first_half))
                self.log(f"Первый тайм зафиксирован при переходе ко второму: {score_text(first_half)}.")
            low=period.lower()
            if first_half is None and any(x in low for x in ('перерыв','half time','тайм заверш')):
                first_half=score; self.storage.set_match(mid,first_half_score=score_text(score)); self.log(f"Первый тайм завершён: {score_text(score)}.")
            # Timer/score on the league cards is a fallback; never treat a completed
            # old match as a newly started match.
            loop_time=asyncio.get_running_loop().time()
            if loop_time-last_monitor>=2.0:
                last_monitor=loop_time
                try:
                    all_matches=await asyncio.wait_for(collect_league_matches(monitor),timeout=5)
                    for candidate in all_matches:
                        cid=str(candidate.get('match_id') or '')
                        if cid and candidate.get('is_upcoming'):
                            self.upcoming_ids.add(cid)
                    newly_live=[m for m in all_matches if str(m.get('match_id') or '') in self.upcoming_ids
                                and str(m.get('match_id'))!=str(item.get('match_id'))
                                and m.get('is_live') and parse_time_to_seconds(m.get('game_time')) is not None
                                and parse_time_to_seconds(m.get('game_time'))<410
                                and m.get('score_1') in (None,0)
                                and m.get('score_2') in (None,0) and m.get('url')]
                    if newly_live:
                        next_match=newly_live[0]
                        self.retired_match_ids.add(external)
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
    async def _read_odds_background(self,page,mid,score,t1,t2,reader):
        try:
            await asyncio.wait_for(self.store_odds(page,mid,score,t1,t2,reader), timeout=18)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self.storage.odds(mid,score_text(score),None,None,'NOT_FOUND','CANVAS_2D','TIMEOUT_18S')
            current=self.runtime.current_match
            if current is not None and current.get('id')==mid and current.get('score')==score_text(score):
                current.update(market_status='NOT_FOUND',canvas_status='Повторное чтение',odds_reason='TIMEOUT_18S',odds_1=None,odds_2=None,odds_updated_at=None)
            self.log(f"Canvas: рынок ещё не готов при {score_text(score)}; повторим чтение без остановки наблюдения.")
        except Exception as exc:
            self.log(f"Ошибка фонового чтения Canvas: {type(exc).__name__}: {exc}")

    async def store_odds(self,page,mid,score,t1,t2,reader):
        try:
            self.log(f'Поиск рынка «Следующий гол №{sum(score)+1}» и чтение Canvas 2D.')
            async def market_log(event,message): self.log(f'{event}: {message}')
            odds=await reader(page,t1,t2,score[0],score[1],logger=market_log); locked=bool(getattr(odds,'locked_sides',()))
            status='LOCKED' if locked else 'AVAILABLE'
            self.storage.odds(mid,score_text(score),float(odds.team1),float(odds.team2),status,getattr(odds,'source','CANVAS_2D'))
            if self.runtime.current_match is not None and self.runtime.current_match.get('id')==mid and self.runtime.current_match.get('score')==score_text(score):
                self.runtime.current_match['odds_1']=float(odds.team1); self.runtime.current_match['odds_2']=float(odds.team2)
                self.runtime.current_match['market_status']=status; self.runtime.current_match['canvas_status']='Активен'
                self.runtime.current_match['odds_source']=getattr(odds,'source','CANVAS_2D')
                self.runtime.current_match['odds_updated_at']=datetime.utcnow().isoformat()+'Z'
                self.runtime.current_match['odds_goal_number']=sum(score)+1
                self.runtime.current_match.pop('odds_reason',None)
            self.log(f'Коэффициенты сохранены: {t1}={odds.team1}; {t2}={odds.team2}.')
        except Exception as exc:
            reason=type(exc).__name__; self.storage.odds(mid,score_text(score),None,None,'NOT_FOUND','CANVAS_2D',reason)
            if self.runtime.current_match is not None and self.runtime.current_match.get('id')==mid and self.runtime.current_match.get('score')==score_text(score):
                self.runtime.current_match['odds_1']=None; self.runtime.current_match['odds_2']=None
                self.runtime.current_match['market_status']='NOT_FOUND'; self.runtime.current_match['canvas_status']='Ожидание/нет данных'; self.runtime.current_match['odds_reason']=reason
                self.runtime.current_match['odds_updated_at']=None
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
