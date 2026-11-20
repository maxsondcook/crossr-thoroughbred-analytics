"""Session (sale day), consignor and foaling date of every lot, read from the raw sale files.

    ../.venv/bin/python sessions.py      # prints coverage

Keeneland files: most lost their line breaks, so each record's session number is glued to
the end of the previous record's price ("100000" + "2" = "1000002"). Records have a fixed
number of fields, and sessions only stay the same or rise by one from hip to hip, so the
next session is whichever of (current, current + 1) the glued field ends with.
Fasig-Tipton files: the session is a date (or a number); sessions are ranked in order.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "sales_data"


def _keeneland(text: str) -> list[tuple[int, int]]:
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) > 3 and len(rows[1]) == len(rows[0]):  # proper line breaks
        h = [x.strip().lower() for x in rows[0]]
        si, hi, pi, di = h.index("session"), h.index("hip"), h.index("propertyline1"), h.index("dob")
        return [(int(r[hi]), int(r[si]), r[pi].strip(), r[di].strip()) for r in rows[1:]
                if len(r) > max(si, hi, pi, di) and r[hi].strip().isdigit() and r[si].strip().isdigit()]
    f = [x for r in rows for x in r]
    head = [x.strip().lower() for x in f[:16]]
    if head[:2] != ["session", "hip"]:
        return []
    period = 16  # Hip .. Purchaser (15 fields) + glued Price|next Session
    m = re.search(r"(\d+)$", f[16])
    if not m:
        return []
    cur, out, i = int(m.group(1)), [], 17
    while i + 15 <= len(f):
        hip = f[i].strip()
        if not hip.isdigit():
            break
        out.append((int(hip), cur, f[i + 1].strip(), f[i + 4].strip()))
        glued = f[i + 15].strip() if i + 15 < len(f) else ""
        if glued.endswith(str(cur + 1)):
            cur += 1
        elif not glued.endswith(str(cur)):
            break  # can't tell: stop rather than guess
        i += period
    return out


def _fasig(df: pd.DataFrame) -> list[tuple]:
    cols = {c.strip().lower(): c for c in df.columns}
    if "session" not in cols or "hip" not in cols:
        return []
    cons = df[cols["property line"]] if "property line" in cols else pd.Series(np.nan, index=df.index)
    dob = df[cols["year of birth"]] if "year of birth" in cols else pd.Series(np.nan, index=df.index)
    ses, hip = df[cols["session"]], pd.to_numeric(df[cols["hip"]], errors="coerce")
    key = pd.to_datetime(ses, errors="coerce", format="mixed")
    if key.isna().mean() > 0.5:
        key = pd.to_numeric(ses, errors="coerce")
    rank = key.rank(method="dense")
    ok = hip.notna() & rank.notna()
    return list(zip(hip[ok].astype(int), rank[ok].astype(int), cons[ok], dob[ok]))


def sessions() -> pd.DataFrame:
    rows = []
    for p in sorted(RAW.glob("*.csv")):
        try:
            if p.name.lower().startswith("keeneland"):
                pairs = _keeneland(p.read_text(encoding="utf-8", errors="replace"))
            else:
                pairs = _fasig(pd.read_csv(p, dtype=str, encoding="utf-8-sig", on_bad_lines="skip"))
        except Exception:  # noqa: BLE001 - a malformed file just gets no sessions
            pairs = []
        rows += [(p.name, *x) for x in pairs]
    d = pd.DataFrame(rows, columns=["source_file", "hip_i", "session", "consignor_raw", "dob"]).drop_duplicates(["source_file", "hip_i"])
    d["consignor_raw"] = d["consignor_raw"].astype(str).str.strip().replace({"": None, "nan": None})
    dob = pd.to_datetime(d["dob"], errors="coerce", format="mixed")
    d["dob"] = dob.where(dob.dt.year >= 1990)
    d["n_sessions"] = d.groupby("source_file")["session"].transform("max")
    return d


def add_raw_fields(s: pd.DataFrame) -> pd.DataFrame:
    """Before the pipeline's features: fill the consignor where sales_lots.csv has none (Keeneland
    lists it as the property line) and add the foaling date (day of year; earlier = more mature)."""
    d = sessions()[["source_file", "hip_i", "consignor_raw", "dob"]]
    s = s.copy()
    s["hip_i"] = pd.to_numeric(s["hip"], errors="coerce").astype("Int64")
    s = s.merge(d.astype({"hip_i": "Int64"}), on=["source_file", "hip_i"], how="left")
    s["consignor"] = s["consignor"].where(s["consignor"].notna(), s["consignor_raw"])
    s["foal_doy"] = s["dob"].dt.dayofyear.where(s["dob"].dt.year == s["birth_year"])
    s["dob_known"] = s["foal_doy"].notna().astype(int)
    return s.drop(columns=["hip_i", "consignor_raw", "dob"])


def add_sessions(s: pd.DataFrame) -> pd.DataFrame:
    """session number, its position in the sale (0 = first day, 1 = last) and whether it's known."""
    d = sessions()[["source_file", "hip_i", "session", "n_sessions"]]
    s = s.copy()
    s["hip_i"] = pd.to_numeric(s["hip"], errors="coerce").astype("Int64")
    s = s.merge(d.astype({"hip_i": "Int64"}), on=["source_file", "hip_i"], how="left")
    s["session_pct"] = np.where(s["n_sessions"] > 1, (s["session"] - 1) / (s["n_sessions"] - 1), 0.0)
    s.loc[s["session"].isna(), "session_pct"] = np.nan
    s["session_known"] = s["session"].notna().astype(int)
    return s.drop(columns="hip_i")


if __name__ == "__main__":
    d = sessions()
    print(f"{len(d):,} lots with a session from {d['source_file'].nunique()} of {len(list(RAW.glob('*.csv')))} files")
    k = d[d["source_file"].str.lower().str.contains("keeneland_20.._september")]
    print(k.groupby("source_file")["session"].agg(["min", "max", "size"]).tail(8).to_string())
