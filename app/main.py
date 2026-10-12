import asyncio, csv, io, json, zipfile
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from .config import DATABASE, HOST, PORT
from .storage import Storage
from .collector import Collector
from .excel_export import workbook as excel_workbook
from datetime import datetime

storage = Storage(DATABASE); collector = Collector(storage)
@asynccontextmanager
async def lifespan(app):
    yield
    await collector.stop()
app = FastAPI(title="Rush — сборщик данных", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
@app.get("/api/status")
def status(): return {**collector.snapshot(), **storage.overview()}
@app.post("/api/control/start")
async def start():
    started = await collector.start()
    return {"started": started, "message": "Наблюдение запущено" if started else "Наблюдение уже выполняется"}
@app.post("/api/control/stop")
async def stop():
    await collector.stop()
    return {"stopped": True, "message": "Наблюдение остановлено"}
@app.get("/api/standings")
def standings(): return storage.calculated_standings()
@app.get("/api/matches")
def matches(): return storage.list_matches()
@app.get("/api/matches/{match_id}")
def match(match_id:int):
    value=storage.detail(match_id)
    if not value: raise HTTPException(404, "Матч не найден")
    return value
@app.get("/api/export/goals.csv")
def export():
    return StreamingResponse(iter([csv_text(storage.all_goals())]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename=rush-goals.csv'})
@app.get('/api/export/matches.csv')
def export_matches(): return StreamingResponse(iter([csv_text(storage.list_matches())]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename=rush-matches.csv'})
@app.get('/api/export/odds.csv')
def export_odds(): return StreamingResponse(iter([csv_text(storage.all_odds())]), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename=rush-odds.csv'})
@app.get('/api/export/all.zip')
def export_all():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('rush-matches.csv',csv_text(storage.list_matches()))
        archive.writestr('rush-goals.csv',csv_text(storage.all_goals()))
        archive.writestr('rush-odds.csv',csv_text(storage.all_odds()))
    return StreamingResponse(iter([out.getvalue()]),media_type='application/zip',headers={'Content-Disposition':'attachment; filename=rush-all-data.zip'})
@app.get('/api/export/full-history.xlsx')
def export_xlsx():
    name=datetime.now().strftime('rush_full_history_%Y-%m-%d_%H-%M.xlsx')
    return StreamingResponse(iter([excel_workbook(storage)]),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename={name}'})
def csv_text(rows):
    out=io.StringIO(); keys=list(rows[0]) if rows else []
    writer=csv.DictWriter(out,fieldnames=keys,extrasaction='ignore'); writer.writeheader()
    writer.writerows(rows)
    return '\ufeff'+out.getvalue()
@app.get("/", response_class=HTMLResponse)
def home(): return '''<meta charset="utf-8"><h1>Rush — сборщик данных</h1><p>Только наблюдение: ставки и кнопки рынка не используются.</p><pre id="x">Загрузка…</pre><script>async function f(){let s=await (await fetch('/api/status')).json();let m=await (await fetch('/api/matches')).json();x.textContent=JSON.stringify({статус:s,матчи:m},null,2)}f();setInterval(f,3000)</script>'''
if __name__ == '__main__':
    import uvicorn; uvicorn.run(app,host=HOST,port=PORT)
