from datetime import datetime, timezone

from app.services.automation_tasks import next_slot
from app.services.enterprise_reports import csv_bytes, pdf_bytes


def test_schedule_is_utc_and_exclusive():
    now = datetime(2026, 11, 16, 7, tzinfo=timezone.utc).timestamp()
    assert datetime.fromtimestamp(next_slot(now, 7), timezone.utc).isoformat() == "2026-11-17T07:00:00+00:00"
    assert datetime.fromtimestamp(next_slot(now, 7, 0), timezone.utc).isoformat() == "2026-11-23T07:00:00+00:00"


def test_export_escapes_sheet_formulas_and_pdf_literals():
    report = {"dataset": "=SUM(A1:A2)", "@formula": " \t=HYPERLINK()", "category": "(Café) \\ ventas", "units": -2}
    csv = csv_bytes(report).decode("utf-8-sig")
    assert "dataset,'=SUM" in csv and "'@formula,' " in csv and "units,-2" in csv
    pdf = pdf_bytes(report)
    assert b"\\050Caf\\351\\051 \\134 ventas" in pdf
    start = int(pdf.split(b"startxref\n")[1].splitlines()[0])
    assert pdf[start:start + 4] == b"xref"
