"""Non-invasive Canvas 2D text capture for the visible next-goal market."""
from dataclasses import dataclass
import re

ODDS = re.compile(r"^\d+(?:[.,]\d+)?$")
HOOK = r'''() => {
 if (window.__rushCanvas) return;
 const entries=[]; const patch=(name)=>{const native=CanvasRenderingContext2D.prototype[name];
 if(!native) return; CanvasRenderingContext2D.prototype[name]=function(...args){
   try { if(name==='fillText'||name==='strokeText') entries.push({text:String(args[0]),x:Number(args[1]),y:Number(args[2]),at:Date.now()}); } catch (_) {}
   return native.apply(this,args); };};
 patch('fillText'); patch('strokeText'); window.__rushCanvas={snapshot:()=>entries.slice(-300)};
}'''

@dataclass
class Odds:
    team1: float|None; team2: float|None; status: str; source: str; reason: str|None=None

async def read_next_goal_odds(page) -> Odds:
    """Read rendered numeric text only; missing data stays explicitly missing."""
    try:
        await page.evaluate(HOOK)
        rows = await page.evaluate("() => window.__rushCanvas ? window.__rushCanvas.snapshot() : []")
    except Exception as exc:
        return Odds(None, None, "UNAVAILABLE", "CANVAS_2D", type(exc).__name__)
    values=[]
    for row in rows:
        raw=str(row.get('text','')).strip().replace(',','.')
        if ODDS.fullmatch(raw) and 1.01 <= float(raw) <= 1000: values.append((row.get('x',0), float(raw)))
    if len(values) < 2: return Odds(None, None, "ABSENT", "CANVAS_2D", "Canvas не отдал две читаемые котировки")
    last=sorted(values[-2:], key=lambda pair: pair[0])
    return Odds(last[0][1], last[1][1], "AVAILABLE", "CANVAS_2D")
