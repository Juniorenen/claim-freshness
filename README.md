# ClaimFreshness

An Intelligent Contract that decides whether the evidence behind a claim is
still current at the moment someone relies on it.

Claims age. A statement backed by a survey from two years ago is not wrong, but
it is no longer the same fact: the source may have been updated, or it may have
quietly rotted while the claim kept circulating. Nothing on-chain records that
distinction today, so a claim either exists or does not.

This contract stamps each claim with the date its evidence was published and
the window its publisher says that evidence stays valid for, then grades it
whenever a dependent decision asks.

## Verdicts

| Verdict | Meaning |
|---|---|
| `FRESH` | still inside the declared window |
| `AGING` | past the window, inside a widening grace band |
| `SUPERSEDED` | past the band, and the source itself carries a later date |
| `EXPIRED` | past the band, and the source has not moved |
| `UNVERIFIABLE` | past the band, and the source could not be read |

`AGING` exists because a claim one day past its window is not the same finding
as one three years past it. The grace band is `window * 5/4`.

`SUPERSEDED` is the interesting one: the claim's own date is old, but the page
it points at has been updated since. That is not an expiry, it is a signal that
the claim should be re-read against the new version rather than trusted or
dropped.

## Determinism

All date arithmetic is integer. `_days_from_civil` is Howard Hinnant's
algorithm, and no float ever touches a comparison, so a day count cannot differ
between validators by rounding.

The current date comes from `gl.message_raw["datetime"]`, which the VM pins to
the transaction. Every validator re-executing the transaction sees the same
value.

The network is touched only when a claim is past its grace band. In that case
the contract fetches the source, asks a model to read the page's own
publication or update date, and runs that step under `prompt_comparative` so
validators must agree on the date or on `unknown`. A `FRESH` or `AGING` verdict
never leaves the VM.

A date read that lands before the claim's own publication date is treated as a
bad read and clamped, not as movement.

## API

Write methods:

```python
register(claim_id: u32, statement: str, source_url: str,
         published_on: u32, window_days: u32) -> str
check(claim_id: u32) -> str
close(claim_id: u32, note: str) -> str
```

Read methods:

```python
get_record(claim_id: u32) -> str
count_records() -> u32
is_current(claim_id: u32) -> bool
```

`published_on` is `YYYYMMDD` as an integer, for example `20260115`. The
contract rejects an impossible date, a future date, and a window outside
`1..40000`, and it names which check failed.

`check` returns the verdict plus the numbers behind it, so a caller can show
why a claim aged out instead of only that it did:

```json
{"age_days": 412, "claim_id": 1, "grace_days": 37,
 "source_date": 20241201, "verdict": "EXPIRED", "window_days": 30}
```

## Tests

```bash
python tests/test_dates.py     # date arithmetic, no dependencies
.venv/Scripts/python -m pytest tests/ -q
```

The first suite cross-checks the civil-date conversion against Python's
`datetime` for every year from 1960 to 2100, so a broken leap-year rule cannot
slip through. The second drives the contract in direct mode and asserts the
band boundaries, which are the places a verdict can flip.

## Try it in a browser

Live page: **https://juniorenen.github.io/claim-freshness/**

Two transactions: `register` writes the claim, then `check` grades it. The page
is static — `genlayer-js` comes from a CDN and talks to Studionet directly.

## Deployment

Reference instance on Studionet:

- contract `0x9405f294C1c12e073b875137D5adb31C4a817d46`
- chain 61999, gasless
- explorer `https://explorer-studio.genlayer.com/address/0x9405f294C1c12e073b875137D5adb31C4a817d46`

