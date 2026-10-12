"""Lossless Rush observation export. Never deduplicate fixtures or snapshots."""
from collections import defaultdict
from datetime import datetime
from io import BytesIO
import json
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="17354B")
EXCEL_TEXT_LIMIT = 32767
INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def clean(value):
    if value is None or isinstance(value, (int, float, bool, datetime)):
        return value
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    value = INVALID_XML_CHARS.sub("", str(value))
    # Excel has a hard limit, while original observations stay intact in SQLite/CSV.
    return value[:EXCEL_TEXT_LIMIT]


def add_sheet(book, name, headers, rows):
    ws = book.create_sheet(name)
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(wrap_text=True)
    for row in rows:
        ws.append([clean(value) for value in row])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col, title in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(col)].width = min(44, max(12, len(title) + 3))
    return ws


def raw_rows(items, fields):
    return ([row.get(field) for field in fields] for row in items)


def workbook(storage):
    # All raw database rows: includes LIVE, INTERRUPTED, incomplete and duplicate
    # fixture names. No pagination, no filtering, no deduplication.
    matches, goals, odds, events = storage.export_bundle()
    book = Workbook()
    book.remove(book.active)

    add_sheet(book, "О выгрузке", ["Параметр", "Значение"], [
        ["Создано UTC", datetime.utcnow().isoformat() + "Z"],
        ["Матчей (все статусы)", len(matches)],
        ["Наблюдений голов", len(goals)],
        ["Снимков коэффициентов", len(odds)],
        ["Системных событий", len(events)],
        ["Источник", "Локальная SQLite база Rush"],
        ["Принцип", "Каждая исходная запись сохраняется без дедупликации"],
        ["Ограничение", "Пропущенные события/коэффициенты не восстанавливаются искусственно"],
        ["Турнирная таблица", "Вычислена из подтверждённых итогов независимо от полноты голов; не официальная таблица БК"],
        ["Дата и время", "Все исходные временные метки сохранены строками, как в SQLite"],
        ["Ограничение Excel", "Текст в одной ячейке ограничен 32767 символами; полная запись сохраняется в SQLite"],
    ])

    match_columns = [
        "id", "external_match_id", "league", "team_1", "team_2",
        "scheduled_at", "started_at", "finished_at", "match_url", "first_half_score",
        "second_half_score", "final_score", "winner", "total_goals",
        "status", "data_complete", "created_at",
    ]
    goal_columns = [
        "id", "match_id", "external_match_id", "started_at",
        "team_1", "team_2", "goal_number", "half", "game_time",
        "team", "score_before", "score_after", "team_1_odds",
        "team_2_odds", "detected_at", "sequence_confidence",
    ]
    odd_columns = [
        "id", "match_id", "external_match_id", "team_1", "team_2",
        "started_at", "next_goal_number", "score", "team_1_odds",
        "team_2_odds", "market_status", "unavailable_reason",
        "captured_at", "source", "signature",
    ]
    event_columns = ["id", "match_id", "event_type", "event_data", "detected_at"]
    add_sheet(book, "Матчи_исходные", match_columns, raw_rows(matches, match_columns))
    add_sheet(book, "Голы_исходные", goal_columns, raw_rows(goals, goal_columns))
    add_sheet(book, "КФ_все_снимки", odd_columns, raw_rows(odds, odd_columns))
    add_sheet(book, "События_все", event_columns, raw_rows(events, event_columns))

    goals_by = defaultdict(list)
    odds_by = defaultdict(list)
    events_by = defaultdict(list)
    for g in goals: goals_by[g["match_id"]].append(g)
    for o in odds: odds_by[o["match_id"]].append(o)
    for e in events: events_by[e["match_id"]].append(e)

    def scorer_odds(g):
        if g.get("team") == g.get("team_1"):
            return g.get("team_1_odds"), g.get("team_2_odds")
        if g.get("team") == g.get("team_2"):
            return g.get("team_2_odds"), g.get("team_1_odds")
        return None, None

    add_sheet(book, "Голы_для_анализа", [
        "ID матча", "ID гола", "Дата начала матча", "Команда 1", "Команда 2",
        "№ гола", "Тайм", "Время гола", "Забила", "Счёт до", "Счёт после",
        "КФ команды 1 до гола", "КФ команды 2 до гола",
        "КФ забившей до гола", "КФ соперника до гола", "Достоверность",
        "Время фиксации UTC",
    ], (
        [
            g.get("external_match_id"), g.get("id"), g.get("started_at"),
            g.get("team_1"), g.get("team_2"), g.get("goal_number"),
            g.get("half"), g.get("game_time"), g.get("team"),
            g.get("score_before"), g.get("score_after"),
            g.get("team_1_odds"), g.get("team_2_odds"),
            *scorer_odds(g), g.get("sequence_confidence"), g.get("detected_at"),
        ] for g in goals
    ))

    add_sheet(book, "Матчи_для_анализа", [
        "ID матча", "Начало UTC", "Команда 1", "Команда 2",
        "1-й тайм", "2-й тайм", "Итог", "Победитель", "Статус",
        "Полная история", "Голы по порядку", "Счёт по порядку",
        "Записанных голов", "Снимков КФ", "Событий",
    ], (
        [
            m["external_match_id"], m.get("started_at"), m["team_1"], m["team_2"],
            m.get("first_half_score"), m.get("second_half_score"),
            m.get("final_score"), m.get("winner"), m["status"],
            bool(m.get("data_complete")),
            " → ".join(str(g.get("team") or "?") for g in goals_by[m["id"]]),
            " → ".join(str(g.get("score_after") or "?") for g in goals_by[m["id"]]),
            len(goals_by[m["id"]]), len(odds_by[m["id"]]), len(events_by[m["id"]]),
        ] for m in matches
    ))

    standings = storage.calculated_standings()
    standings_columns = [
        "position", "team", "played", "wins", "draws", "losses",
        "goals_for", "goals_against", "goal_difference", "points",
    ]
    add_sheet(book, "Таблица_расчётная", standings_columns,
              raw_rows(standings["table"], standings_columns))

    out = BytesIO()
    book.save(out)
    return out.getvalue()
