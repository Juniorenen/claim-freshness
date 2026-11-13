# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }


from genlayer import *

import json
import typing


# ---------------------------------------------------------------- constants

# Grace band past the declared window before the source is re-inspected.
# A claim that is one day past its window is not the same finding as one that
# is three years past it, so the engine keeps a widening band instead of a
# single cut-off.
GRACE_NUM = 5          # GRACE_NUM / GRACE_DEN of the window counts as AGING
GRACE_DEN = 4
MAX_TEXT = 600
MAX_URL = 400
MAX_AGE_DAYS = 40000   # ~110 years; guards against nonsense input

STATE_SUBMITTED = "SUBMITTED"
STATE_CHECKED = "CHECKED"
STATE_CLOSED = "CLOSED"

VERDICT_FRESH = "FRESH"
VERDICT_AGING = "AGING"
VERDICT_EXPIRED = "EXPIRED"
VERDICT_SUPERSEDED = "SUPERSEDED"
VERDICT_UNVERIFIABLE = "UNVERIFIABLE"


# ------------------------------------------------------------- pure helpers
# Everything below is integer arithmetic only. Validators must reach the same
# answer byte for byte, so no float ever touches a date comparison.

def _days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 1970-01-01 for a Gregorian date (Howard Hinnant's algorithm)."""
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    mp = (m + 9) % 12
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(z: int) -> tuple:
    """Inverse of _days_from_civil."""
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


def _pack_date(y: int, m: int, d: int) -> int:
    return y * 10000 + m * 100 + d


def _split_packed(n: int) -> tuple:
    return (n // 10000, (n // 100) % 100, n % 100)


def _valid_date(y: int, m: int, d: int) -> bool:
    if not (1 <= m <= 12):
        return False
    if d < 1:
        return False
    leap = (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return d <= days[m - 1]


def _parse_ymd(text: str):
    """Parse YYYY-MM-DD (or YYYY/MM/DD, or YYYY-MM-DDTHH:MM) into (y, m, d)."""
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
        y = int(parts[0])
        m = int(parts[1])
        d = int(parts[2])
    except ValueError:
        return None
    if not _valid_date(y, m, d):
        return None
    return (y, m, d)


def _today_packed() -> int:
    """Transaction date as YYYYMMDD. gl.message_raw['datetime'] is an ISO 8601
    string pinned to the transaction, so every validator reads the same value."""
    raw = gl.message_raw.get("datetime") if hasattr(gl, "message_raw") else None
    if not raw:
        return 0
    got = _parse_ymd(str(raw)[:10])
    if got is None:
        return 0
    return _pack_date(*got)


# ---------------------------------------------------------------- the engine

class ClaimFreshness(gl.Contract):
    """Decides whether the evidence behind a claim is still current.

    A claim carries the date its evidence was published and the window the
    publisher says that evidence stays valid for. The contract turns that into
    a graded state at the moment of the call:

      FRESH         still inside the declared window
      AGING         just past the window, inside a widening grace band
      SUPERSEDED    past the band, and the source itself has been updated since
      EXPIRED       past the band, and the source has not moved
      UNVERIFIABLE  past the band, and the source could not be read

    Only the last three states need the network; the first two are pure
    arithmetic on the transaction date.
    """

    records: TreeMap[str, str]

    def __init__(self):
        self.records = TreeMap[str, str]()

    # ------------------------------------------------------------- internal

    def _load(self, claim_id) -> dict:
        key = str(claim_id)
        if key not in self.records:
            raise gl.vm.UserError(f"claim {key} not found")
        return json.loads(self.records[key])

    def _store(self, claim_id, rec: dict) -> None:
        self.records[str(claim_id)] = json.dumps(rec, sort_keys=True)

    def _probe_source(self, url: str, published_packed: int) -> tuple:
        """Ask the page itself when it was last updated.

        Returns (date_packed_or_0, raw_answer). Non-determinism lives entirely
        here: the fetch and the extraction are re-run by every validator and
        must agree on the date, or on 'unknown'.
        """
        target = url

        def run():
            page = gl.nondet.web.get(target)
            body = page.body.decode("utf-8", "ignore")
            text = body[:12000]
            prompt = (
                "The text below is the beginning of a web page that a claim "
                "points at as evidence.\n\n"
                "Find the date the page's content was last published or "
                "updated. Look for explicit markers such as 'last updated', "
                "'published on', 'modified', a date near the title, or a "
                "version stamp. Do not guess from the URL or from copyright "
                "years in a footer.\n\n"
                "Answer with exactly one line, either:\n"
                "  DATE: YYYY-MM-DD\n"
                "or\n"
                "  DATE: unknown\n\n"
                f"PAGE TEXT:\n{text}"
            )
            return gl.nondet.exec_prompt(prompt)

        principle = (
            "Both answers name the same calendar date, or both say unknown. "
            "Ignore surrounding words and formatting."
        )
        for _ in range(2):
            try:
                answer = gl.eq_principle.prompt_comparative(run, principle)
                return self._read_date(answer, published_packed), str(answer)[:200]
            except Exception:
                continue
        return (0, "fetch failed")

    def _read_date(self, answer, fallback_packed: int) -> int:
        text = str(answer)
        marker = text.upper().find("DATE:")
        tail = text[marker + 5:] if marker >= 0 else text
        got = _parse_ymd(tail.strip().replace("*", "").replace("`", ""))
        if got is None:
            return 0
        packed = _pack_date(*got)
        # A source cannot have been updated before the claim's own publication
        # date; treat that as a bad read rather than a real signal.
        if packed < fallback_packed:
            return fallback_packed
        return packed

    # ---------------------------------------------------------------- public

    @gl.public.write
    def register(
        self,
        claim_id: u32,
        statement: str,
        source_url: str,
        published_on: u32,
        window_days: u32,
    ) -> str:
        key = str(claim_id)
        if key in self.records:
            raise gl.vm.UserError(f"claim {key} already registered")

        text = statement.strip()
        if not text:
            raise gl.vm.UserError("statement is empty")
        if len(text) > MAX_TEXT:
            raise gl.vm.UserError(f"statement exceeds {MAX_TEXT} chars")

        url = source_url.strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            raise gl.vm.UserError("source_url must be http(s)")
        if len(url) > MAX_URL:
            raise gl.vm.UserError(f"source_url exceeds {MAX_URL} chars")

        y, m, d = _split_packed(int(published_on))
        if not _valid_date(y, m, d):
            raise gl.vm.UserError("published_on must be YYYYMMDD and a real date")

        window = int(window_days)
        if window < 1 or window > MAX_AGE_DAYS:
            raise gl.vm.UserError(f"window_days must be 1..{MAX_AGE_DAYS}")

        today = _today_packed()
        if today == 0:
            raise gl.vm.UserError("transaction date unavailable")
        published_packed = _pack_date(y, m, d)
        if _days_from_civil(y, m, d) > _days_from_civil(*_split_packed(today)):
            raise gl.vm.UserError("published_on is in the future")

        rec = {
            "claim_id": int(claim_id),
            "statement": text,
            "source_url": url,
            "published_on": published_packed,
            "window_days": window,
            "state": STATE_SUBMITTED,
            "verdict": "",
            "age_days": 0,
            "source_date": 0,
            "probe": "",
            "checked_at": 0,
            "history": [],
        }
        self._store(claim_id, rec)
        return json.dumps({"claim_id": int(claim_id), "state": STATE_SUBMITTED})

    @gl.public.write
    def check(self, claim_id: u32) -> str:
        rec = self._load(claim_id)

        today = _today_packed()
        if today == 0:
            raise gl.vm.UserError("transaction date unavailable")

        py, pm, pd = _split_packed(rec["published_on"])
        ty, tm, td = _split_packed(today)
        age = _days_from_civil(ty, tm, td) - _days_from_civil(py, pm, pd)
        if age < 0:
            raise gl.vm.UserError("clock moved before the publication date")
        if age > MAX_AGE_DAYS:
            raise gl.vm.UserError(f"age exceeds {MAX_AGE_DAYS} days")

        window = rec["window_days"]
        grace = (window * GRACE_NUM) // GRACE_DEN

        source_date = 0
        probe = ""

        if age <= window:
            verdict = VERDICT_FRESH
        elif age <= grace:
            verdict = VERDICT_AGING
        else:
            # Past the band: the only thing that can save the claim is the
            # source itself having moved.
            source_date, probe = self._probe_source(rec["source_url"], rec["published_on"])
            if source_date == 0:
                verdict = VERDICT_UNVERIFIABLE
            elif source_date > rec["published_on"]:
                verdict = VERDICT_SUPERSEDED
            else:
                verdict = VERDICT_EXPIRED

        rec["state"] = STATE_CHECKED
        rec["verdict"] = verdict
        rec["age_days"] = age
        rec["source_date"] = source_date
        rec["probe"] = probe
        rec["checked_at"] = today

        history = rec.get("history") or []
        history.append({"at": today, "age_days": age, "verdict": verdict})
        rec["history"] = history[-12:]

        self._store(claim_id, rec)
        return json.dumps(
            {
                "claim_id": int(claim_id),
                "verdict": verdict,
                "age_days": age,
                "window_days": window,
                "grace_days": grace,
                "source_date": source_date,
            },
            sort_keys=True,
        )

    @gl.public.write
    def close(self, claim_id: u32, note: str) -> str:
        rec = self._load(claim_id)
        if rec["state"] == STATE_CLOSED:
            raise gl.vm.UserError("claim already closed")
        rec["state"] = STATE_CLOSED
        rec["close_note"] = note.strip()[:MAX_TEXT]
        self._store(claim_id, rec)
        return json.dumps({"claim_id": int(claim_id), "state": STATE_CLOSED})

    @gl.public.view
    def get_record(self, claim_id: u32) -> str:
        return json.dumps(self._load(claim_id), sort_keys=True)

    @gl.public.view
    def count_records(self) -> u32:
        return u32(len(self.records))

    @gl.public.view
    def is_current(self, claim_id: u32) -> bool:
        rec = self._load(claim_id)
        return rec.get("verdict") in (VERDICT_FRESH, VERDICT_AGING)
