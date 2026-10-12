"""Regression for observed scores, halftime subtraction and full Excel."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from openpyxl import load_workbook
from app.storage import Storage
from app.excel_export import workbook


def test_handover_0650_keeps_partial_second_half_without_inventing_final():
    with TemporaryDirectory() as temp:
        db=Storage(Path(temp)/"rush.sqlite3")
        mid=db.upsert_match("match-1","rush","A","B")
        db.set_match(mid,status="LIVE",data_complete=1)
        assert db.record_score_snapshot(mid,"4:1","03:30")
        assert db.set_first_half_score(mid,"4:1")
        assert db.record_score_snapshot(mid,"6:2","06:50")
        db.set_match(mid,status="INTERRUPTED")
        match=db.detail(mid)["match"]
        assert match["first_half_score"]=="4:1"
        assert match["last_observed_score"]=="6:2"
        assert match["second_half_observed_score"]=="2:1"
        assert match["last_observed_time"]=="06:50"
        assert match["final_score"] is None
        assert match["second_half_score"] is None
        assert match["data_complete"]==1

        book=load_workbook(BytesIO(workbook(db)),read_only=True,data_only=True)
        try:
            original=list(book["Матчи_исходные"].values)
            headers=original[0]
            data=dict(zip(headers,original[1]))
            assert data["last_observed_score"]=="6:2"
            assert data["second_half_observed_score"]=="2:1"
            assert data["final_score"] is None
            analysis=list(book["Матчи_для_анализа"].values)
            adata=dict(zip(analysis[0],analysis[1]))
            assert adata["Счёт на выходе"]=="6:2"
            assert adata["2-й тайм на выходе"]=="2:1"
            assert adata["Подтверждённый итог"] is None
        finally:
            book.close()


def test_final_result_updates_confirmed_second_half_but_preserves_observation():
    with TemporaryDirectory() as temp:
        db=Storage(Path(temp)/"rush.sqlite3")
        mid=db.upsert_match("match-2","rush","A","B")
        db.set_match(mid,status="INTERRUPTED")
        db.record_score_snapshot(mid,"6:2","06:50")
        db.set_first_half_score(mid,"4:1")
        assert db.finalize_observed_match(mid,"7:3")
        result=db.detail(mid)["match"]
        assert result["second_half_observed_score"]=="2:1"
        assert result["second_half_score"]=="3:2"
        assert result["last_observed_score"]=="6:2"
        assert result["final_score"]=="7:3"


def test_missing_halftime_is_not_guessed_and_invalid_subtraction_is_rejected():
    with TemporaryDirectory() as temp:
        db=Storage(Path(temp)/"rush.sqlite3")
        mid=db.upsert_match("match-3","rush","A","B")
        db.record_score_snapshot(mid,"3:2","06:50")
        assert db.detail(mid)["match"]["second_half_observed_score"] is None
        assert db.set_first_half_score(mid,"4:2")
        assert db.detail(mid)["match"]["second_half_observed_score"] is None
        assert Storage.half_difference("4:1","6:2")=="2:1"
        assert Storage.half_difference("4:1","3:2") is None
