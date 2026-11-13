"""Pure date arithmetic tests. No GenLayer import, so this runs with plain python."""

def _days_from_civil(y: int, m: int, d: int) -> int:
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    mp = (m + 9) % 12
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(z: int) -> tuple:
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return (y + (1 if m <= 2 else 0), m, d)


def _valid_date(y: int, m: int, d: int) -> bool:
    if not (1 <= m <= 12):
        return False
    if d < 1:
        return False
    leap = (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return d <= days[m - 1]


def _parse_ymd(text):
    s = text.strip()
    if len(s) < 10:
        return None
    for i in range(len(s)):
        if s[i] in "/.":
            s = s[:i] + "-" + s[i + 1:]
    parts = s[:10].split("-")
    if len(parts) != 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if not _valid_date(y, m, d):
        return None
    return (y, m, d)


# --- epoch anchors -------------------------------------------------------

assert _days_from_civil(1970, 1, 1) == 0
assert _days_from_civil(1970, 1, 2) == 1
assert _days_from_civil(1969, 12, 31) == -1
assert _days_from_civil(2000, 1, 1) == 10957
assert _days_from_civil(2024, 1, 1) == 19723
assert _days_from_civil(2026, 11, 14) == 20771
assert _days_from_civil(2000, 3, 1) == 11017, "day after leap 2000"

# --- round trip ----------------------------------------------------------

for z in range(-800, 21000, 37):
    y, m, d = _civil_from_days(z)
    assert _days_from_civil(y, m, d) == z, (z, y, m, d)

for packed in [(1970, 1, 1), (2000, 2, 29), (2024, 2, 29), (2026, 11, 14), (2100, 12, 31)]:
    y, m, d = packed
    assert _civil_from_days(_days_from_civil(y, m, d)) == (y, m, d)

# --- gaps ----------------------------------------------------------------

assert _days_from_civil(2024, 3, 1) - _days_from_civil(2024, 2, 28) == 2, "2024 leap"
assert _days_from_civil(2023, 3, 1) - _days_from_civil(2023, 2, 28) == 1, "2023 common"
assert _days_from_civil(2001, 1, 1) - _days_from_civil(2000, 1, 1) == 366, "2000 leap"
assert _days_from_civil(1901, 1, 1) - _days_from_civil(1900, 1, 1) == 365, "1900 not leap"

# --- validity ------------------------------------------------------------

assert _valid_date(2024, 2, 29)
assert not _valid_date(2023, 2, 29)
assert not _valid_date(1900, 2, 29)
assert _valid_date(2000, 2, 29)
assert not _valid_date(2026, 13, 1)
assert not _valid_date(2026, 4, 31)

# --- parsing -------------------------------------------------------------

assert _parse_ymd("2026-11-14") == (2026, 11, 14)
assert _parse_ymd("2026-11-14T10:08:57+08:00") == (2026, 11, 14)
assert _parse_ymd("2026/11/14") == (2026, 11, 14)
assert _parse_ymd("2026.11.14") == (2026, 11, 14)
assert _parse_ymd("DATE: 2026-11-14") is None, "prefix must be stripped by caller"
assert _parse_ymd("2026-02-30") is None, "impossible day"
assert _parse_ymd("unknown") is None
assert _parse_ymd("") is None

# --- cross-check against the standard library ----------------------------

from datetime import date as _date

_epoch = _date(1970, 1, 1)
for year in range(1960, 2101):
    for month in (1, 3, 5, 7, 9, 11, 12):
        for day in (1, 15, 28):
            expect = (_date(year, month, day) - _epoch).days
            got = _days_from_civil(year, month, day)
            assert got == expect, (year, month, day, got, expect)

print("all date tests passed")
