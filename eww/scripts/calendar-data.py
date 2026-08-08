#!/usr/bin/env python3
"""
calendar-data.py
──────────────────────────────────────────────────────────────────────────
Genera el grid de un mes en JSON para el control-center de eww.

Reusa exactamente el mismo algoritmo de calendar-popup.py (festivos de
Colombia vía Ley Emiliani + Pascua de Gauss/Meeus, y el armado de
semanas con días grises del mes anterior/siguiente) — la única
diferencia es que en vez de dibujar Gtk.Grid/Gtk.Label, imprime un
JSON que control-center.sh reenvía a eww por deflisten.

Uso:
  calendar-data.py               -> mes/año actual (America/Bogota)
  calendar-data.py <year> <month>
──────────────────────────────────────────────────────────────────────────
"""
import sys
import json
import datetime
import calendar as _cal

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Bogota")
except Exception:
    TZ = None


def now_local():
    if TZ is not None:
        return datetime.datetime.now(TZ)
    return datetime.datetime.now()


# ── Festivos de Colombia (idéntico a calendar-popup.py) ────────────────

def _easter_sunday(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)


def _next_monday(d):
    days_ahead = (7 - d.weekday()) % 7
    return d + datetime.timedelta(days=days_ahead)


_CO_FIXED = [(1, 1), (5, 1), (7, 20), (8, 7), (12, 8), (12, 25)]
_CO_MOVED_TO_MONDAY = [(1, 6), (3, 19), (6, 29), (8, 15), (10, 12), (11, 1), (11, 11)]


def colombia_holidays(year):
    holidays = set()
    for m, d in _CO_FIXED:
        holidays.add(datetime.date(year, m, d))
    for m, d in _CO_MOVED_TO_MONDAY:
        holidays.add(_next_monday(datetime.date(year, m, d)))

    easter = _easter_sunday(year)
    holidays.add(easter - datetime.timedelta(days=3))
    holidays.add(easter - datetime.timedelta(days=2))
    holidays.add(_next_monday(easter + datetime.timedelta(days=39)))
    holidays.add(_next_monday(easter + datetime.timedelta(days=60)))
    holidays.add(_next_monday(easter + datetime.timedelta(days=68)))
    return holidays


# ── Armado de semanas (idéntico a _build_weeks de calendar-popup.py) ───

def build_weeks(year, month):
    first_weekday, days_in_month = _cal.monthrange(year, month)
    start_offset = (first_weekday + 1) % 7  # queremos empezar en domingo

    prev_month = 12 if month == 1 else month - 1
    prev_year = year - 1 if month == 1 else year
    days_in_prev = _cal.monthrange(prev_year, prev_month)[1]

    cells = []
    for i in range(start_offset):
        day_num = days_in_prev - start_offset + i + 1
        cells.append((day_num, False))
    for d in range(1, days_in_month + 1):
        cells.append((d, True))
    while len(cells) % 7 != 0:
        next_day_index = len(cells) - (start_offset + days_in_month)
        cells.append((next_day_index + 1, False))

    return [cells[i:i + 7] for i in range(0, len(cells), 7)]


def main():
    today = now_local()
    try:
        year = int(sys.argv[1]) if len(sys.argv) > 1 else today.year
        month = int(sys.argv[2]) if len(sys.argv) > 2 else today.month
    except ValueError:
        year, month = today.year, today.month

    holidays = colombia_holidays(year)
    weeks = build_weeks(year, month)

    out_weeks = []
    for week in weeks:
        row = []
        for day_num, in_month in week:
            is_today = (
                in_month
                and day_num == today.day
                and month == today.month
                and year == today.year
            )
            is_holiday = False
            if in_month:
                this_date = datetime.date(year, month, day_num)
                is_sunday = this_date.weekday() == 6  # lunes=0 ... domingo=6
                is_holiday = (this_date in holidays) or is_sunday
            row.append({
                "day": day_num,
                "in_month": in_month,
                "today": is_today,
                "holiday": is_holiday,
            })
        out_weeks.append(row)

    print(json.dumps({
        "year": year,
        "month": month,
        "month_name": datetime.date(year, month, 1).strftime("%B").capitalize(),
        "weeks": out_weeks,
    }))


if __name__ == "__main__":
    main()
