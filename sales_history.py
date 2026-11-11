"""Sale-title, price and name helpers, plus the raw sales-CSV parser the pipeline uses.

The API reads sales from Postgres (crossr_core.sales); load_and_process_csv is
for turning raw sale files into sales_lots.
"""
from pathlib import Path
import csv
import re

# Month sorting weights to guarantee strict January to December chronological order
MONTH_ORDER = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12
}

def safe_str(val):
    """Safely converts any row object value to a string to prevent NoneType attribute crashes."""
    if val is None:
        return ""
    return str(val).strip()

def clean_title(filename):
    """Converts file names to clean titles."""
    name = filename.replace(".csv", "").replace("_", " ")
    name = re.sub(r'\d{4}', '', name)
    name = name.replace("()", "").replace("[]", "").replace("-", " ")
    return " ".join(name.split()).title()

def get_sale_month_index(sale_title):
    """Inspects a sale catalog title and maps its approximate month weight."""
    title_lower = sale_title.lower()
    for month_name, index in MONTH_ORDER.items():
        if month_name in title_lower:
            return index
    # Sale names that imply a month without naming it.
    for word, index in (("winter", 2), ("spring", 4), ("summer", 7), ("saratoga", 8), ("fall", 10), ("autumn", 10)):
        if word in title_lower:
            return index
    return 13  # unknown month 

def parse_price(val):
    """Cleans up raw prices into floats for accurate sorting."""
    val_str = safe_str(val).upper()
    if not val_str or "OUT" in val_str or val_str == "-2.00":
        return 0.0
    
    cleaned = "".join(c for c in val_str if c.isdigit() or c == '.')
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0

def clean_search_string(text):
    """Normalizes names and entries cleanly for precise lookup matching."""
    cleaned = re.sub(r'\(.*?\)', '', safe_str(text))
    return " ".join(cleaned.lower().split()).strip()

def load_and_process_csv(file_path: Path, gender_filter=None, sort_choice="3", horse_search=None, sire_search=None, dam_search=None, birth_year_search=None):
    """Reads CSV, parses dynamic headers, and applies filters safely across Keeneland and Fasig-Tipton structures."""
    results = []
    
    catalog_year_match = re.search(r'\d{4}', file_path.name)
    catalog_year = int(catalog_year_match.group(0)) if catalog_year_match else None
    file_name_lower = file_path.name.lower()

    try:
        with open(file_path, mode="r", encoding="utf-8-sig", errors="ignore") as f:
            sample = f.read(2048)
            dialect = csv.excel
            try:
                if csv.Sniffer().has_header(sample):
                    dialect = csv.excel
            except Exception:
                pass
            if '\t' in sample: 
                dialect = csv.excel_tab
                
            f.seek(0)
            
            reader = csv.DictReader(f, dialect=dialect)
            headers = {k.lower().strip(): k for k in reader.fieldnames} if reader.fieldnames else {}
            
            hip_key = next((headers[k] for k in ['hip number', 'hip', 'hip_no'] if k in headers), None)
            name_key = next((headers[k] for k in ['horse', 'horse name', 'name', 'description'] if k in headers), None)
            sex_key = next((headers[k] for k in ['sex', 'gender'] if k in headers), None)
            price_key = next((headers[k] for k in ['sale price', 'price', 'final bid', 'price_raw'] if k in headers), None)
            purchaser_key = next((headers[k] for k in ['purchaser', 'buyer', 'buyer name'] if k in headers), None)
            sire_key = next((headers[k] for k in ['sire'] if k in headers), None)
            dam_key = next((headers[k] for k in ['dam'] if k in headers), None)
            dob_key = next((headers[k] for k in ['year of birth', 'birth year', 'dob', 'date of birth'] if k in headers), None)
            color_key = next((headers[k] for k in ['color', 'colour'] if k in headers), None)
            consign_key = next((headers[k] for k in ['property line', 'consignor', 'consigned by', 'seller'] if k in headers), None)
            damsire_key = next((headers[k] for k in ['sire of dam', 'damsire', 'broodmare sire'] if k in headers), None)
            barn_key = next((headers[k] for k in ['barn'] if k in headers), None)
            foaled_loc_key = next((headers[k] for k in ['foaled', 'state foaled'] if k in headers), None)
            sold_as_key = next((headers[k] for k in ['sold as', 'sold as description'] if k in headers), None)

            is_fasig = "fasig" in file_name_lower or "sale title" in headers

            for row in reader:
                if not any(row.values()):
                    continue
                
                raw_sire = safe_str(row.get(sire_key, "")).strip() if sire_key else ""
                raw_dam = safe_str(row.get(dam_key, "")).strip() if dam_key else ""
                raw_sex = safe_str(row.get(sex_key, "")).strip().upper() if sex_key else ""
                hip_val = safe_str(row.get(hip_key, "N/A")).strip() if hip_key else "N/A"
                if hip_val.isdigit():
                    hip_val = hip_val.zfill(4)
                
                raw_dob = safe_str(row.get(dob_key, "")).strip() if dob_key else ""
                extracted_birth_year = ""
                if raw_dob and any(c.isdigit() for c in raw_dob):
                    year_match = re.search(r'\d{4}', raw_dob)
                    if year_match:
                        extracted_birth_year = year_match.group(0)
                
                if not extracted_birth_year and catalog_year:
                    if "yearling" in file_name_lower:
                        extracted_birth_year = str(catalog_year - 1)
                    elif "2yo" in file_name_lower or "2 year" in file_name_lower or "two-year" in file_name_lower:
                        extracted_birth_year = str(catalog_year - 2)
                    elif "weanling" in file_name_lower:
                        extracted_birth_year = str(catalog_year)
                    else:
                        sold_as = safe_str(row.get(sold_as_key, "")).strip().upper() if sold_as_key else ""
                        sex_upper = raw_sex.upper()
                        age_hint = f"{sold_as} {sex_upper}"
                        if "WEANLING" in age_hint:
                            extracted_birth_year = str(catalog_year)
                        elif "YEARLING" in age_hint:
                            extracted_birth_year = str(catalog_year - 1)
                        elif "2YO" in age_hint or "TWO-YEAR-OLD" in age_hint or "TWO YEAR OLD" in age_hint:
                            extracted_birth_year = str(catalog_year - 2)
                        elif sold_as or sex_upper in ("M", "H", "MARE", "HORSE", "STALLION"):
                            # Broodmares, prospects, stallions and horses of
                            # racing age: the file gives no age, so leave the
                            # year blank rather than guess a yearling's.
                            extracted_birth_year = ""
                        else:
                            extracted_birth_year = str(catalog_year - 1)

                raw_name = safe_str(row.get(name_key, "")).strip() if name_key else ""
                
                if not raw_name or is_fasig or re.match(r'^hip\s*\d+', raw_name.lower()):
                    short_yr = extracted_birth_year[-2:] if extracted_birth_year else ""
                    raw_name = f"{raw_sire} x {raw_dam} '{short_yr}".strip()

                if "-" in raw_name and (not raw_sire or raw_sire == "Unknown"):
                    parts = raw_name.split("-", 1)
                    raw_sire = parts[0].strip()
                    remainder = parts[1].strip()
                    if "," in remainder:
                        dam_part, sex_part = remainder.split(",", 1)
                        remainder = dam_part.strip()
                    raw_dam = remainder

                if not raw_sire: raw_sire = "Unknown"
                if not raw_dam: raw_dam = "Unknown"
                if not raw_sex: raw_sex = "Unknown"
                
                match_found = False
                clean_name_field = clean_search_string(raw_name)
                clean_sire_field = clean_search_string(raw_sire)
                clean_dam_field = clean_search_string(raw_dam)

                if horse_search and horse_search == clean_name_field:
                    match_found = True
                
                if sire_search and dam_search:
                    if sire_search == clean_sire_field and dam_search == clean_dam_field:
                        match_found = True
                elif sire_search and not dam_search:
                    if sire_search in clean_sire_field: 
                        match_found = True
                elif dam_search and not sire_search:
                    if dam_search in clean_dam_field:
                        match_found = True

                if (horse_search or sire_search or dam_search) and not match_found:
                    continue

                if birth_year_search and extracted_birth_year and birth_year_search != extracted_birth_year:
                    continue
                    
                sex_letter = raw_sex[0] if raw_sex else "U"
                if "GELDING" in raw_sex: sex_letter = "G"
                elif "MARE" in raw_sex: sex_letter = "M"
                elif "HORSE" in raw_sex: sex_letter = "H"
                elif "COLT" in raw_sex: sex_letter = "C"
                elif "FILLY" in raw_sex: sex_letter = "F"
                elif "RIDGLING" in raw_sex: sex_letter = "R"
                elif "WEANLING" in raw_sex: sex_letter = "W"
                
                raw_price = safe_str(row.get(price_key, "0")) if price_key else "0"
                raw_purchaser = safe_str(row.get(purchaser_key, "")).strip().upper() if purchaser_key else ""
                raw_price_upper = raw_price.upper()

                # Determine status flags
                is_out = "OUT" in raw_purchaser or "OUT" in raw_price_upper
                is_rna = ("NOT SOLD" in raw_purchaser or "RNA" in raw_purchaser or 
                          "R.N.A." in raw_purchaser or "RNA" in raw_price_upper or 
                          "R.N.A." in raw_price_upper or raw_price.strip() == "-2.00")
                
                numeric_price = parse_price(raw_price)
                
                if is_out:
                    clean_disp_price = "WITHDRAWN"
                    numeric_price = 0.0
                elif is_rna:
                    clean_disp_price = f"RNA (${int(numeric_price):,})" if numeric_price > 0 else "RNA"
                elif numeric_price == 0:
                    clean_disp_price = "N/A"
                else:
                    clean_disp_price = f"${int(numeric_price):,}"

                results.append({
                    'hip': hip_val,
                    'name': raw_name,
                    'sex': raw_sex,
                    'sex_letter': sex_letter,
                    'sire': raw_sire,
                    'dam': raw_dam,
                    'damsire': safe_str(row.get(damsire_key, "")).strip() if damsire_key else "",
                    'color': safe_str(row.get(color_key, "")).strip().upper() if color_key else "",
                    'consignor': safe_str(row.get(consign_key, "")).strip() if consign_key else "",
                    'purchaser': raw_purchaser,
                    'barn': safe_str(row.get(barn_key, "")).strip() if barn_key else "",
                    'foaled_location': safe_str(row.get(foaled_loc_key, "")).strip() if foaled_loc_key else "",
                    'birth_year': extracted_birth_year,
                    'price_raw': clean_disp_price,
                    'price_num': numeric_price,
                    'is_rna': is_rna,
                    'is_out': is_out,
                    'is_fasig': is_fasig
                })
    except FileNotFoundError:
        return []
        
    return results

def compute_sales_metrics(dataset):
    """JSON-serializable sales metrics for a list of lot records."""
    offered = [r for r in dataset if not r.get('is_out', False)]
    sold_records = [r for r in offered if not r.get('is_rna', False) and r.get('price_num', 0) > 0]
    rna_records = [r for r in offered if r.get('is_rna', False)]
    valid_sales = [r['price_num'] for r in sold_records]
    total_offered = len(offered)
    total_sold = len(valid_sales)
    rna_count = len(rna_records)
    gross = sum(valid_sales)
    avg = gross / total_sold if total_sold > 0 else 0
    sorted_sales = sorted(valid_sales)
    n = len(sorted_sales)
    if n == 0:
        median = 0
    elif n % 2 == 1:
        median = sorted_sales[n // 2]
    else:
        median = (sorted_sales[(n // 2) - 1] + sorted_sales[n // 2]) / 2.0
    sold_pct = (total_sold / total_offered * 100) if total_offered > 0 else 0
    million_plus = sum(1 for p in valid_sales if p >= 1000000)
    return {
        'horses': str(total_offered),
        'gross': f"${int(gross):,}",
        'avg': f"${int(avg):,}",
        'median': f"${int(median):,}",
        'sold_pct': f"{sold_pct:.1f}%",
        'rna': str(rna_count),
        'mil': str(million_plus),
        'horses_num': total_offered,
        'gross_num': gross,
        'avg_num': avg,
        'median_num': median,
        'sold_pct_num': sold_pct,
        'rna_num': rna_count,
        'mil_num': million_plus,
    }


def calculate_sales_stats(records, target_stallion=None):
    """Returns lifetime (and last-5-year when applicable) stats without printing."""
    if not records:
        return None
    sale_years = sorted(list(set(int(r['year']) for r in records if r.get('year') and str(r['year']).isdigit())))
    split_view = target_stallion is not None and len(sale_years) > 5
    last_5_years_cutoff = max(sale_years) - 4 if sale_years else 0
    if split_view:
        recent_dataset = [
            r for r in records
            if r.get('year') and str(r['year']).isdigit() and int(r['year']) >= last_5_years_cutoff
        ]
        return {
            "split": True,
            "lifetime": compute_sales_metrics(records),
            "last_5_years": compute_sales_metrics(recent_dataset),
            "last_5_cutoff": last_5_years_cutoff,
        }
    return {"split": False, "lifetime": compute_sales_metrics(records)}
