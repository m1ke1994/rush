import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.migrate()

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def migrate(self):
        with self.connection() as con:
            con.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS matches (
              id INTEGER PRIMARY KEY, external_match_id TEXT NOT NULL UNIQUE,
              league TEXT NOT NULL, team_1 TEXT NOT NULL, team_2 TEXT NOT NULL,
              scheduled_at TEXT, started_at TEXT, finished_at TEXT,
              first_half_score TEXT, second_half_score TEXT, final_score TEXT,
              winner TEXT, total_goals INTEGER, status TEXT NOT NULL,
              data_complete INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goal_events (
              id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, goal_number INTEGER NOT NULL,
              team TEXT, score_before TEXT NOT NULL, score_after TEXT NOT NULL,
              half TEXT NOT NULL, game_time TEXT, detected_at TEXT NOT NULL,
              sequence_confidence TEXT NOT NULL, UNIQUE(match_id, goal_number, score_after)
            );
            CREATE TABLE IF NOT EXISTS odds_snapshots (
              id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, next_goal_number INTEGER NOT NULL,
              score TEXT NOT NULL, team_1_odds REAL, team_2_odds REAL,
              market_status TEXT NOT NULL, unavailable_reason TEXT, captured_at TEXT NOT NULL,
              source TEXT NOT NULL, signature TEXT NOT NULL, UNIQUE(match_id, signature)
            );
            CREATE TABLE IF NOT EXISTS match_events (
              id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, event_type TEXT NOT NULL,
              event_data TEXT NOT NULL, detected_at TEXT NOT NULL,
              UNIQUE(match_id, event_type, event_data)
            );
            CREATE INDEX IF NOT EXISTS idx_matches_created ON matches(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
            CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(team_1, team_2);
            CREATE INDEX IF NOT EXISTS idx_goals_match ON goal_events(match_id, goal_number);
            CREATE INDEX IF NOT EXISTS idx_odds_match ON odds_snapshots(match_id, id);
            ''')
            # Additive migration: existing Rush databases and all observations stay intact.
            columns={row["name"] for row in con.execute("PRAGMA table_info(matches)")}
            if "match_url" not in columns:
                con.execute("ALTER TABLE matches ADD COLUMN match_url TEXT")

    def upsert_match(self, external_id, league, team1, team2, status="UPCOMING", scheduled_at=None, match_url=None):
        with self.connection() as con:
            con.execute('''INSERT INTO matches(external_match_id,league,team_1,team_2,scheduled_at,status,created_at,match_url)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(external_match_id) DO UPDATE SET
            team_1=excluded.team_1, team_2=excluded.team_2,
            match_url=COALESCE(excluded.match_url,matches.match_url),
            status=CASE WHEN matches.status='FINISHED' THEN 'FINISHED'
                        WHEN excluded.status='UPCOMING' AND matches.status IN ('LIVE','INTERRUPTED') THEN matches.status
                        ELSE excluded.status END''',
            (external_id, league, team1, team2, scheduled_at, status, now(),match_url))
            return con.execute("SELECT id FROM matches WHERE external_match_id=?", (external_id,)).fetchone()[0]

    def set_match(self, match_id, **fields):
        if not fields: return
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields: return
        with self.connection() as con:
            con.execute("UPDATE matches SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", (*fields.values(), match_id))

    def event(self, match_id, kind, data):
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
        with self.connection() as con:
            con.execute("INSERT OR IGNORE INTO match_events(match_id,event_type,event_data,detected_at) VALUES(?,?,?,?)", (match_id, kind, encoded, now()))

    def goal(self, match_id, number, team, before, after, half, game_time, confidence):
        with self.connection() as con:
            con.execute('''INSERT OR IGNORE INTO goal_events(match_id,goal_number,team,score_before,score_after,half,game_time,detected_at,sequence_confidence)
            VALUES(?,?,?,?,?,?,?,?,?)''', (match_id, number, team, before, after, half, game_time, now(), confidence))

    def odds(self, match_id, score, a, b, status, source, reason=None):
        number = sum(map(int, score.split(':'))) + 1
        sig = json.dumps([number, score, a, b, status, source, reason], ensure_ascii=False)
        with self.connection() as con:
            con.execute('''INSERT OR IGNORE INTO odds_snapshots(match_id,next_goal_number,score,team_1_odds,team_2_odds,market_status,unavailable_reason,captured_at,source,signature)
            VALUES(?,?,?,?,?,?,?,?,?,?)''', (match_id, number, score, a, b, status, reason, now(), source, sig))

    def overview(self):
        with self.connection() as con:
            row = con.execute("SELECT count(*) total, sum(status='FINISHED') finished FROM matches").fetchone()
            goals = con.execute("SELECT count(*) FROM goal_events").fetchone()[0]
            return dict(matches=row['total'], finished=row['finished'] or 0, goals=goals)

    def all_goals(self):
        with self.connection() as con:
            return [dict(r) for r in con.execute('''SELECT g.*, (SELECT o.team_1_odds FROM odds_snapshots o
               WHERE o.match_id=g.match_id AND o.score=g.score_before
                 AND o.next_goal_number=g.goal_number
                 AND o.market_status='AVAILABLE'
                 AND o.team_1_odds IS NOT NULL AND o.team_2_odds IS NOT NULL
                 AND o.captured_at<=g.detected_at
               ORDER BY o.captured_at DESC,o.id DESC LIMIT 1) AS team_1_odds,
              (SELECT o.team_2_odds FROM odds_snapshots o
               WHERE o.match_id=g.match_id AND o.score=g.score_before
                 AND o.next_goal_number=g.goal_number
                 AND o.market_status='AVAILABLE'
                 AND o.team_1_odds IS NOT NULL AND o.team_2_odds IS NOT NULL
                 AND o.captured_at<=g.detected_at
               ORDER BY o.captured_at DESC,o.id DESC LIMIT 1) AS team_2_odds
              FROM goal_events g ORDER BY g.match_id, g.goal_number''')]

    def all_odds(self):
        with self.connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM odds_snapshots ORDER BY match_id,id")]

    def export_bundle(self):
        """All rows, deliberately without UI pagination limits."""
        with self.connection() as con:
            matches=[dict(r) for r in con.execute('SELECT * FROM matches ORDER BY created_at DESC')]
            goals=[dict(r) for r in con.execute('''SELECT g.*,m.external_match_id,m.team_1,m.team_2,m.started_at,
                (SELECT o.team_1_odds FROM odds_snapshots o
               WHERE o.match_id=g.match_id AND o.score=g.score_before
                 AND o.next_goal_number=g.goal_number
                 AND o.market_status='AVAILABLE'
                 AND o.team_1_odds IS NOT NULL AND o.team_2_odds IS NOT NULL
                 AND o.captured_at<=g.detected_at
               ORDER BY o.captured_at DESC,o.id DESC LIMIT 1) AS team_1_odds,
              (SELECT o.team_2_odds FROM odds_snapshots o
               WHERE o.match_id=g.match_id AND o.score=g.score_before
                 AND o.next_goal_number=g.goal_number
                 AND o.market_status='AVAILABLE'
                 AND o.team_1_odds IS NOT NULL AND o.team_2_odds IS NOT NULL
                 AND o.captured_at<=g.detected_at
               ORDER BY o.captured_at DESC,o.id DESC LIMIT 1) AS team_2_odds
                FROM goal_events g JOIN matches m ON m.id=g.match_id
                ORDER BY g.match_id,g.goal_number''')]
            odds=[dict(r) for r in con.execute('''SELECT o.*,m.external_match_id,m.team_1,m.team_2,m.started_at
                FROM odds_snapshots o JOIN matches m ON m.id=o.match_id ORDER BY o.match_id,o.id''')]
            events=[dict(r) for r in con.execute('SELECT * FROM match_events ORDER BY match_id,id')]
        return matches,goals,odds,events

    def list_matches(self):
        with self.connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM matches ORDER BY id DESC")]

    def detail(self, match_id):
        with self.connection() as con:
            match = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
            if not match: return None
            return {"match":dict(match), "goals":[dict(x) for x in con.execute("""SELECT g.*, (SELECT o.team_1_odds FROM odds_snapshots o
               WHERE o.match_id=g.match_id AND o.score=g.score_before
                 AND o.next_goal_number=g.goal_number
                 AND o.market_status='AVAILABLE'
                 AND o.team_1_odds IS NOT NULL AND o.team_2_odds IS NOT NULL
                 AND o.captured_at<=g.detected_at
               ORDER BY o.captured_at DESC,o.id DESC LIMIT 1) AS team_1_odds,
              (SELECT o.team_2_odds FROM odds_snapshots o
               WHERE o.match_id=g.match_id AND o.score=g.score_before
                 AND o.next_goal_number=g.goal_number
                 AND o.market_status='AVAILABLE'
                 AND o.team_1_odds IS NOT NULL AND o.team_2_odds IS NOT NULL
                 AND o.captured_at<=g.detected_at
               ORDER BY o.captured_at DESC,o.id DESC LIMIT 1) AS team_2_odds
                FROM goal_events g WHERE g.match_id=? ORDER BY g.goal_number""",(match_id,))], "odds":[dict(x) for x in con.execute("SELECT * FROM odds_snapshots WHERE match_id=? ORDER BY id",(match_id,))]}


    def clear_observations(self):
        """Delete only Rush observations; preserve browser credentials and profile."""
        with self.connection() as con:
            counts = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("matches", "goal_events", "odds_snapshots", "match_events")}
            for table in ("goal_events", "odds_snapshots", "match_events", "matches"):
                con.execute(f"DELETE FROM {table}")
            return counts

    def pending_final_results(self, limit=100):
        """Matches observed live but without a verified final score."""
        with self.connection() as con:
            return [dict(row) for row in con.execute(
                """SELECT * FROM matches
                   WHERE status IN ('LIVE','INTERRUPTED')
                     AND final_score IS NULL AND started_at IS NOT NULL
                   ORDER BY id DESC LIMIT ?""", (limit,))]

    def finalize_observed_match(self, match_id, score, *, first_half=None, source="MATCH_PAGE"):
        """Confirm final score without altering original goal/odds observations.

        A complete final result is not proof that the goal sequence was complete.
        """
        import re
        if not re.fullmatch(r"\\d+:\\d+", str(score)):
            raise ValueError("Недопустимый итоговый счёт")
        a,b=(int(v) for v in score.split(":"))
        with self.connection() as con:
            match=con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
            if match is None:
                return False
            if match["status"]=="FINISHED":
                return False
            first=first_half or match["first_half_score"]
            second=None
            if first and re.fullmatch(r"\\d+:\\d+",first):
                x,y=(int(v) for v in first.split(":"))
                if a>=x and b>=y:
                    second=f"{a-x}:{b-y}"
            winner="Ничья" if a==b else (match["team_1"] if a>b else match["team_2"])
            con.execute("""UPDATE matches SET status='FINISHED',final_score=?,
                finished_at=?,winner=?,total_goals=?,second_half_score=?
                WHERE id=? AND status!='FINISHED'""",
                (score,now(),winner,a+b,second,match_id))
            encoded=json.dumps({"final_score":score,"source":source,"second_half_score":second},ensure_ascii=False,sort_keys=True)
            con.execute("""INSERT OR IGNORE INTO match_events
                (match_id,event_type,event_data,detected_at) VALUES(?,?,?,?)""",
                (match_id,"FINAL_RESULT_CONFIRMED",encoded,now()))
        return True

    def calculated_standings(self):
        """Derived standings from verified final scores, regardless of goal-event coverage.
        This is not the official bookmaker standings."""
        with self.connection() as con:
            fixtures=[dict(r) for r in con.execute(
                "SELECT id,team_1,team_2,final_score,finished_at FROM matches "
                "WHERE status='FINISHED' AND final_score IS NOT NULL "
                "ORDER BY COALESCE(finished_at,created_at),id")]
        stats={}
        for m in fixtures:
            try:
                a,b=(int(x) for x in m['final_score'].split(':'))
            except (ValueError,AttributeError):
                continue
            for team,goals,against in ((m['team_1'],a,b),(m['team_2'],b,a)):
                row=stats.setdefault(team,{'team':team,'played':0,'wins':0,'draws':0,'losses':0,'goals_for':0,'goals_against':0,'points':0})
                row['played']+=1; row['goals_for']+=goals; row['goals_against']+=against
                if goals>against: row['wins']+=1; row['points']+=3
                elif goals==against: row['draws']+=1; row['points']+=1
                else: row['losses']+=1
        table=sorted(stats.values(),key=lambda r:(-r['points'],-(r['goals_for']-r['goals_against']),-r['goals_for'],r['team']))
        for index,row in enumerate(table,1):
            row['position']=index
            row['goal_difference']=row['goals_for']-row['goals_against']
        return {'source':'LOCAL_VERIFIED_FINAL_RESULTS','matches_count':len(fixtures),'table':table}
