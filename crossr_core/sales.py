"""Sales hub queries over the sales index in Postgres (sales_catalogs + sales_lot_index).

The index is a typed copy of sales_lots that railway_db rebuilds whenever
sales_lots changes, so every query here runs as SQL instead of in Python.
"""
import re

import sales_history
from crossr_core import railway_db

LOT_SELECT = """
    SELECT l.id, l.hip, l.name, l.sex, l.sex_letter, l.sire, l.dam, l.damsire,
           l.color, l.consignor, l.purchaser, l.barn, l.foaled_location, l.birth_year,
           l.price_raw, l.price_num, l.is_rna, l.is_out, l.is_fasig,
           c.year, c.title AS sale_title, c.month_index, l.catalog_id
    FROM sales_lot_index l
    JOIN sales_catalogs c ON c.id = l.catalog_id
"""

# Leading digits of the hip, like SQLite's CAST(hip AS INTEGER).
HIP_NUM = "COALESCE(substring(l.hip from '^\\s*([0-9]{1,9})')::bigint, 0)"


class _Conn:
    """One Postgres connection with the sqlite3-style calls this module uses.

    ``?`` placeholders become ``%s`` (literal ``%`` is escaped first) and rows
    come back as dicts.
    """

    def __init__(self):
        from psycopg.rows import dict_row

        self._cm = railway_db.connect(row_factory=dict_row)
        self._conn = self._cm.__enter__()

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("%", "%%").replace("?", "%s"), params)

    def close(self):
        self._cm.__exit__(None, None, None)


def connect():
    """Connection to the sales index, or None when sales_lots is empty."""
    if not railway_db.ensure_sales_index():
        return None
    return _Conn()


def index_ready():
    """True when the sales index is built and current (for /api/health)."""
    try:
        return railway_db.ensure_sales_index()
    except Exception:
        return False


def ensure_damsire_index():
    # The damsire index is part of the sales index (railway_db).
    return


def damsire_yearling_averages(stallion_name):
    """Catalog-year average sold yearling price for lots whose damsire is this stallion."""
    conn = connect()
    if not conn:
        return {}
    key = sales_history.clean_search_string(stallion_name)
    if not key:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT c.year AS year, COUNT(*) AS n, AVG(l.price_num) AS avg_price
            FROM sales_lot_index l
            JOIN sales_catalogs c ON c.id = l.catalog_id
            WHERE l.damsire_key = ?
              AND l.is_rna = 0 AND l.is_out = 0 AND l.price_num > 0
              AND (
                LOWER(c.title) LIKE '%yearling%'
                OR LOWER(COALESCE(c.source_file, '')) LIKE '%yearling%'
              )
            GROUP BY c.year
            ORDER BY c.year DESC
            """,
            (key,),
        ).fetchall()
        out = {}
        for row in rows:
            year = int(row["year"])
            avg = float(row["avg_price"] or 0)
            out[year] = {"n": int(row["n"]), "avg_price": int(round(avg))}
        return out
    finally:
        conn.close()


def _lot_from_row(row):
    d = dict(row)
    d["is_rna"] = bool(d.get("is_rna"))
    d["is_out"] = bool(d.get("is_out"))
    d["is_fasig"] = bool(d.get("is_fasig"))
    d["year"] = str(d.get("year") or "")
    d["sale"] = d.get("sale_title") or d.get("sale") or ""
    return d


def list_years():
    conn = connect()
    if not conn:
        return []
    try:
        rows = conn.execute("SELECT DISTINCT year FROM sales_catalogs ORDER BY year DESC").fetchall()
        return [r["year"] for r in rows]
    finally:
        conn.close()


def list_catalogs(year=None):
    conn = connect()
    if not conn:
        return []
    try:
        if year:
            rows = conn.execute(
                "SELECT id, year, title, month_index FROM sales_catalogs WHERE year = ? ORDER BY month_index, title COLLATE \"C\", id",
                (int(year),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, year, title, month_index FROM sales_catalogs ORDER BY year DESC, month_index, title COLLATE \"C\", id"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _query_lots(where_sql, params, sort="price_desc", sex=None, limit=100, offset=0, target_stallion=None):
    conn = connect()
    if not conn:
        return {"lots": [], "total": 0, "stats": None}
    clauses = [where_sql]
    args = list(params)
    if sex:
        clauses.append("l.sex_letter = ?")
        args.append(sex.upper()[:1])
    where = " AND ".join(clauses)
    order = {
        "price_desc": "l.price_num DESC",
        "price_asc": "l.price_num ASC",
        "hip": 'l.hip COLLATE "C" ASC',
        "year_desc": "c.year DESC, c.month_index DESC",
        "name": 'l.name COLLATE "C" ASC',
    }.get(sort, "l.price_num DESC") + ", l.id"
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM sales_lot_index l JOIN sales_catalogs c ON c.id = l.catalog_id WHERE {where}",
            args,
        ).fetchone()["n"]
        rows = conn.execute(
            f"{LOT_SELECT} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        stat_rows = conn.execute(
            f"""
            SELECT l.price_num, l.is_rna, l.is_out, c.year
            FROM sales_lot_index l
            JOIN sales_catalogs c ON c.id = l.catalog_id
            WHERE {where}
            """,
            args,
        ).fetchall()
        records = [_lot_from_row(r) for r in rows]
        stat_records = [_lot_from_row(r) for r in stat_rows]
        return {
            "lots": records,
            "total": total,
            "stats": sales_history.calculate_sales_stats(stat_records, target_stallion=target_stallion),
        }
    finally:
        conn.close()


def catalog_lots(catalog_id, sort="price_desc", sex=None, limit=80, offset=0):
    return _query_lots("l.catalog_id = ?", [int(catalog_id)], sort=sort, sex=sex, limit=limit, offset=offset)


def sire_lots(sire_name, sort="year_desc", sex=None, limit=80, offset=0):
    key = sales_history.clean_search_string(sire_name)
    return _query_lots(
        "l.sire_key = ?",
        [key],
        sort=sort,
        sex=sex,
        limit=limit,
        offset=offset,
        target_stallion=sire_name,
    )


def horse_lots(name="", year="", sire="", dam="", sort="year_desc", limit=80, offset=0):
    identity = []
    params = []
    if name:
        identity.append("l.name_key = ?")
        params.append(sales_history.clean_search_string(name))
    if sire and dam:
        identity.append("(l.sire_key = ? AND l.dam_key = ?)")
        params.append(sales_history.clean_search_string(sire))
        params.append(sales_history.clean_search_string(dam))
    elif sire:
        identity.append("l.sire_key = ?")
        params.append(sales_history.clean_search_string(sire))
    elif dam:
        identity.append("l.dam_key = ?")
        params.append(sales_history.clean_search_string(dam))
    if not identity:
        return {"lots": [], "total": 0, "stats": None}
    clauses = ["(" + " OR ".join(identity) + ")"]
    if year:
        clauses.append("l.birth_year = ?")
        params.append(str(year).strip())
    return _query_lots(" AND ".join(clauses), params, sort=sort, limit=limit, offset=offset)


def get_lot(lot_id):
    conn = connect()
    if not conn:
        return None
    try:
        row = conn.execute(f"{LOT_SELECT} WHERE l.id = ?", (int(lot_id),)).fetchone()
        if not row:
            return None
        lot = _lot_from_row(row)
        sire_key = sales_history.clean_search_string(lot.get("sire") or "")
        comparable = []
        if sire_key:
            comparable = conn.execute(
                f"""
                {LOT_SELECT}
                WHERE l.sire_key = ? AND l.id != ? AND l.is_out = 0 AND l.price_num > 0
                ORDER BY ABS(l.price_num - ?) ASC, l.id
                LIMIT 8
                """,
                (sire_key, int(lot_id), lot.get("price_num") or 0),
            ).fetchall()
        sire_stats = sire_lots(lot.get("sire") or "", limit=1) if lot.get("sire") else {"stats": None}
        return {
            "lot": lot,
            "comparables": [_lot_from_row(r) for r in comparable],
            "sire_stats": sire_stats.get("stats"),
        }
    finally:
        conn.close()


def catalog_nav(catalog_id):
    """Compact hip list for walking a past catalog. No price aggregates."""
    conn = connect()
    if not conn:
        return None
    try:
        cat = conn.execute(
            "SELECT id, year, title FROM sales_catalogs WHERE id = ?",
            (int(catalog_id),),
        ).fetchone()
        if not cat:
            return None
        rows = conn.execute(
            f"""
            {LOT_SELECT}
            WHERE l.catalog_id = ?
            ORDER BY {HIP_NUM}, l.hip COLLATE "C", l.id
            """,
            (int(catalog_id),),
        ).fetchall()
        lots = []
        for row in rows:
            lot = _lot_from_row(row)
            lots.append({
                "id": lot["id"],
                "hip": lot.get("hip"),
                "name": lot.get("name"),
                "sex": lot.get("sex"),
                "sex_letter": lot.get("sex_letter"),
                "color": lot.get("color"),
                "sire": lot.get("sire"),
                "dam": lot.get("dam"),
                "birth_year": lot.get("birth_year"),
                "consignor": lot.get("consignor"),
                "foaled": lot.get("foaled_location"),
                "price_raw": lot.get("price_raw"),
                "price_num": lot.get("price_num") or 0,
                "is_rna": lot.get("is_rna"),
            })
        return {"catalog": dict(cat), "lots": lots, "total": len(lots)}
    finally:
        conn.close()


def _sale_price_num(raw):
    try:
        return int(float(str(raw or "0").replace(",", "").replace("$", "") or 0))
    except (TypeError, ValueError):
        return 0


def _is_catalog_label(horse_name):
    """Unnamed hips are listed as 'SIRE x DAM' (sometimes with a crop year)."""
    text = str(horse_name or "").strip()
    return not text or bool(re.search(r"\s[xX]\s", text)) or text.upper().startswith(("UNNAMED", "NAME"))


def _auction_candidates(name_keys, pairs):
    """Sales-index rows whose name key or (sire, dam) keys match, in load order."""
    conn = connect()
    if not conn or not (name_keys or pairs):
        return []
    try:
        return conn.execute(
            """
            SELECT l.name AS horse_name, l.nk, l.sk, l.dk, l.is_out, l.is_rna,
                   l.price_num, l.price_raw, l.purchaser, l.birth_year, l.hip,
                   c.title AS sale_title, CASE WHEN c.year > 0 THEN c.year::text ELSE '' END AS sale_year
            FROM sales_lot_index l
            JOIN sales_catalogs c ON c.id = l.catalog_id
            WHERE l.nk = ANY(?) OR (l.sk, l.dk) IN (SELECT * FROM unnest(?::text[], ?::text[]))
            ORDER BY l.id
            """,
            (list(name_keys), [p[0] for p in pairs], [p[1] for p in pairs]),
        ).fetchall()
    finally:
        conn.close()


def cross_sales(sire, damsire, limit=10, exclude_name=""):
    """Lots sold by this sire out of mares by this damsire (the hip's exact cross), newest first."""
    sire_key = str(sire or "").strip().lower()
    damsire_key = re.sub(r"\s*\(.*?\)", "", str(damsire or "")).strip().lower()
    conn = connect()
    if not conn or not sire_key or not damsire_key:
        return []
    try:
        rows = conn.execute(
            """
            SELECT l.name, l.dam, l.hip, l.birth_year, l.price_num, l.price_raw, l.is_rna,
                   c.title AS sale_title, CASE WHEN c.year > 0 THEN c.year::text ELSE '' END AS sale_year
            FROM sales_lot_index l
            JOIN sales_catalogs c ON c.id = l.catalog_id
            WHERE l.sire_key = ? AND l.damsire_key = ? AND COALESCE(l.is_out, 0) = 0
            ORDER BY c.year DESC, l.price_num DESC NULLS LAST, l.id
            LIMIT ?
            """,
            (sire_key, damsire_key, int(limit) + 5),
        ).fetchall()
    finally:
        conn.close()
    exclude = str(exclude_name or "").strip().lower()
    out = [dict(r) for r in rows if str(r.get("name") or "").strip().lower() != exclude]
    return out[:limit]


def auctions_for_horses(crosses, limit=40):
    """Sale rows (sales index) for similar-cross horses.

    Catalog hips are stored as SIRE x DAM labels. Match each cross horse by:
    (1) cleaned horse_name, or (2) sire+dam pair from the pedigree DB / cross row.
    """
    if not crosses:
        return []
    import placeholder_store
    import main

    targets = []  # (display_name, similarity, name_key, sire_key, dam_key)
    seen = set()
    for row in crosses:
        name = row.get("name") or ""
        key = placeholder_store.name_key(name) or sales_history.clean_search_string(name)
        if not key or key in seen:
            continue
        seen.add(key)
        sire = row.get("sire") or ""
        dam = row.get("dam") or ""
        ped = main.PEDIGREE_DATABASE.get(main.clean_horse_name(name)) or {}
        year_match = re.search(r"\((\d{4})\)", str(ped.get("Horse") or ""))
        foal_year = row.get("year") or (year_match.group(1) if year_match else "")
        if not sire or not dam:
            sire_node = ped.get("Sire") or {}
            dam_node = ped.get("Dam") or {}
            if not sire:
                sire = sire_node.get("Horse", "") if isinstance(sire_node, dict) else str(sire_node or "")
            if not dam:
                dam = dam_node.get("Horse", "") if isinstance(dam_node, dict) else str(dam_node or "")
        targets.append({
            "name": name,
            "similarity": row.get("similarity"),
            "name_key": key,
            "sire_key": placeholder_store.name_key(sire),
            "dam_key": placeholder_store.name_key(dam),
            "foal_year": str(foal_year or "").strip()[:4],
        })
    if not targets:
        return []

    by_name = {t["name_key"]: t for t in targets}
    by_pair = {}
    for t in targets:
        if t["sire_key"] and t["dam_key"]:
            by_pair.setdefault((t["sire_key"], t["dam_key"]), t)

    out = []
    for sale in _auction_candidates(list(by_name), list(by_pair)):
        if sale["is_out"]:
            continue
        horse = sale.get("horse_name") or ""
        hk = sale["nk"]
        sk = sale["sk"]
        dk = sale["dk"]
        # Same name must also be the same parents (older horses reuse names), and a
        # sire/dam match only counts for an unnamed hip of the same foal crop, not a sibling.
        match = by_name.get(hk)
        if match and match["sire_key"] and match["dam_key"] and sk and dk and (sk, dk) != (match["sire_key"], match["dam_key"]):
            match = None
        if not match:
            pair = by_pair.get((sk, dk))
            named_other = hk and pair and hk != pair["name_key"] and not _is_catalog_label(horse)
            born = str(sale.get("birth_year") or "").strip()[:4]
            wrong_crop = pair and pair["foal_year"] and born and born != pair["foal_year"]
            if pair and not named_other and not wrong_crop:
                match = pair
        if not match:
            continue
        price_num = _sale_price_num(sale.get("price_num"))
        price_raw = sale.get("price_raw") or ""
        is_rna = bool(sale.get("is_rna")) or (
            price_raw and "RNA" in str(price_raw).upper()
        )
        out.append({
            "name": match["name"],
            "similarity": match["similarity"],
            "sale": sale.get("sale_title") or "",
            "year": sale.get("sale_year") or "",
            "hip": sale.get("hip") or "",
            "price_raw": price_raw,
            "price_num": price_num,
            "is_rna": is_rna,
            "purchaser": sale.get("purchaser") or "",
        })
    out.sort(key=lambda item: (
        -(item["similarity"] if item["similarity"] is not None else -1),
        -(int(str(item["year"] or "0") or 0) if str(item["year"] or "").isdigit() else 0),
        -(item["price_num"] or 0),
    ))
    return out[:limit]


# ---------------------------------------------------------------------------
# Stallion sales panel
# ---------------------------------------------------------------------------

def lot_type(sale_year, birth_year, sex_letter):
    """Horse type from age at sale: Weanling, Yearling, Two-Year-Old, Mare/Broodmare, Horse."""
    try:
        age = int(sale_year) - int(birth_year)
    except (TypeError, ValueError):
        return "Unknown"
    if age <= 0:
        return "Weanling"
    if age == 1:
        return "Yearling"
    if age == 2:
        return "Two-Year-Old"
    return "Mare / Broodmare" if (sex_letter or "").upper() in ("F", "M") else "Horse of Racing Age"


def lot_status(price, is_rna, is_out):
    if is_out:
        return "out"
    if is_rna or not price or price <= 0:
        return "not sold"
    return "sold"


def _price_stats(prices):
    prices = sorted(p for p in prices if p and p > 0)
    if not prices:
        return {"sold": 0, "average": None, "median": None, "high": None, "gross": 0}
    n = len(prices)
    mid = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2
    return {"sold": n, "average": round(sum(prices) / n), "median": round(mid), "high": round(prices[-1]),
            "gross": round(sum(prices))}


CATEGORIES = {"yearlings": ("Yearling",), "weanlings": ("Weanling",), "all": None}


def sire_sales_summary(sire_name, entered_stud=None, catalog_id=None, today=None):
    """Everything the stallion sales panel needs, from the sales index.

    - Periods: current year (to date), latest completed year (today.year - 1),
      last three completed years, lifetime. Averages use sold lots only; RNAs
      count as not sold and outs are excluded.
    - Lots foaled before the stallion entered stud belong to a namesake and are
      dropped.
    - ``catalog_id`` selects one sale for the per-sale stats and horse list
      (default: his most recent sale).
    """
    import datetime as _dt

    today = today or _dt.date.today()
    current_year = today.year
    latest_done = current_year - 1
    key = sales_history.clean_search_string(sire_name)
    conn = connect()
    if conn is None:
        return {"lots_total": 0, "sales": [], "by_year": [], "summary": {}}
    try:
        rows = conn.execute(
            LOT_SELECT + f" WHERE l.sire_key = ? ORDER BY c.year DESC, c.month_index DESC, {HIP_NUM}",
            [key],
        ).fetchall()
    finally:
        conn.close()

    lots = []
    for r in rows:
        try:
            born = int(str(r.get("birth_year") or "").strip())
        except ValueError:
            born = None
        if entered_stud and born and born <= int(entered_stud):
            continue  # conceived before he stood: a namesake's foal
        price = float(r.get("price_num") or 0)
        lots.append({
            "id": r["id"], "catalog_id": r["catalog_id"], "year": r["year"], "sale": r["sale_title"],
            "month_index": r.get("month_index") or 0, "hip": r.get("hip"), "name": r.get("name"),
            "sex": r.get("sex_letter") or r.get("sex"), "dam": r.get("dam"), "damsire": r.get("damsire"),
            "type": lot_type(r["year"], born, r.get("sex_letter")),
            "status": lot_status(price, r.get("is_rna"), r.get("is_out")),
            "price": round(price) if price > 0 else None, "price_raw": r.get("price_raw"),
            "purchaser": r.get("purchaser"), "consignor": r.get("consignor"),
        })

    def stats(pool):
        offered = [l for l in pool if l["status"] != "out"]
        out = _price_stats(l["price"] for l in offered if l["status"] == "sold")
        out["offered"] = len(offered)
        return out

    def by_category(pool):
        return {cat: stats([l for l in pool if types is None or l["type"] in types]) for cat, types in CATEGORIES.items()}

    periods = {
        "current": [l for l in lots if l["year"] == current_year],
        "latest": [l for l in lots if l["year"] == latest_done],
        "last3": [l for l in lots if latest_done - 2 <= l["year"] <= latest_done],
        "lifetime": lots,
    }
    summary = {cat: {p: by_category(pool)[cat] for p, pool in periods.items()} for cat in CATEGORIES}

    by_year = []
    if lots:
        for y in range(min(l["year"] for l in lots), max(l["year"] for l in lots) + 1):
            pool = [l for l in lots if l["year"] == y]
            cats = by_category(pool)
            by_year.append({"year": y, "complete": y <= latest_done,
                            **{f"{c}_avg": cats[c]["average"] for c in CATEGORIES},
                            **{f"{c}_sold": cats[c]["sold"] for c in CATEGORIES}})

    sales = {}
    for l in lots:
        s = sales.setdefault(l["catalog_id"], {"catalog_id": l["catalog_id"], "year": l["year"], "title": l["sale"],
                                                "month_index": l["month_index"], "pool": []})
        s["pool"].append(l)
    sale_list = []
    for s in sales.values():
        st = stats(s.pop("pool"))
        if st["offered"]:
            sale_list.append({**s, "progeny": st["offered"], "sold": st["sold"], "average": st["average"]})
    # month_index 13 means "unknown month" (sales_history): sort those last in their year.
    sale_list.sort(key=lambda s: (-s["year"], -(s["month_index"] if 0 < (s["month_index"] or 0) <= 12 else 0), s["title"]))

    # Price by cross: each broodmare sire's lots, per category (yearlings / weanlings / all).
    by_damsire = {}
    for l in lots:
        name = re.sub(r"\s*\(\d{4}\)\s*$", "", str(l.get("damsire") or "")).strip()
        if not name:
            continue
        by_damsire.setdefault(name.upper(), {"broodmare_sire": name, "pool": []})["pool"].append(l)
    damsire_rows = []
    for entry in by_damsire.values():
        pool = entry.pop("pool")
        cats = by_category(pool)
        if any(c["sold"] for c in cats.values()):
            # The horses behind the figures, newest sale first (outs dropped, like the stats).
            entry["lots"] = [{
                "name": l["name"], "year": l["year"], "sale": l["sale"], "hip": l["hip"],
                "type": l["type"], "sex": l["sex"], "status": l["status"], "price": l["price"],
            } for l in pool if l["status"] != "out"]
            damsire_rows.append({**entry, **cats})
    damsire_rows.sort(key=lambda r: -(r["all"]["average"] or 0))

    selected = catalog_id or (sale_list[0]["catalog_id"] if sale_list else None)
    sale_lots = [l for l in lots if l["catalog_id"] == selected]
    return {
        "current_year": current_year,
        "latest_completed_year": latest_done,
        "lots_total": len(lots),
        "summary": summary,
        "by_year": by_year,
        "sales": sale_list,
        "selected_catalog_id": selected,
        "sale_stats": by_category(sale_lots) if sale_lots else None,
        "sale_lots": sale_lots,
        "by_damsire": damsire_rows,
    }
