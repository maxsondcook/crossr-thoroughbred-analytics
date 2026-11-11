import os
import pprint
import re

# File paths
STALLION_DB_FILE = "stallion_profiles.py"
CROSSES_DB_FILE = "successful_crosses.py"
PEDIGREE_DB_FILE = "pedigrees_database.py"

def load_databases():
    """Programmatically executes and loads lists/dicts from local DB files."""
    stallions = {}
    crosses = []
    pedigrees = {}

    if os.path.exists(STALLION_DB_FILE):
        with open(STALLION_DB_FILE, "r", encoding="utf-8") as f:
            exec(f.read(), globals())
            if 'stallion_profiles' in globals():
                stallions = globals()['stallion_profiles']

    if os.path.exists(CROSSES_DB_FILE):
        with open(CROSSES_DB_FILE, "r", encoding="utf-8") as f:
            exec(f.read(), globals())
            if 'SUCCESSFUL_CROSSES_LIST' in globals():
                crosses = globals()['SUCCESSFUL_CROSSES_LIST']

    if os.path.exists(PEDIGREE_DB_FILE):
        with open(PEDIGREE_DB_FILE, "r", encoding="utf-8") as f:
            exec(f.read(), globals())
            if 'pedigree_map' in globals():
                pedigrees = globals()['pedigree_map']

    return stallions, crosses, pedigrees

def clean_pedigree_name(raw_name):
    """
    Strips out birth years from pedigree node names (e.g. 'Storm Cat (1983)' -> 'Storm Cat').
    """
    if not raw_name:
        return "Unknown"
    # Matches everything before a opening parenthesis
    match = re.split(r'\s*\(', raw_name)
    return match[0].strip()

def find_lineage(horse_name, pedigree_db):
    """
    Looks up the Sire and Damsire from the pedigree_map structure.
    """
    lookup_key = horse_name.strip().lower()
    sire = "Unknown"
    damsire = "Unknown"
    
    if lookup_key in pedigree_db:
        profile = pedigree_db[lookup_key]
        
        # 1. Extract Sire
        if 'Sire' in profile and 'Horse' in profile['Sire']:
            sire = clean_pedigree_name(profile['Sire']['Horse'])
            
        # 2. Extract Damsire (Mother's Sire)
        if 'Dam' in profile and isinstance(profile['Dam'], dict):
            dam_node = profile['Dam']
            if 'Sire' in dam_node and 'Horse' in dam_node['Sire']:
                damsire = clean_pedigree_name(dam_node['Sire']['Horse'])
                
    return sire, damsire

def parse_highest_class(major_wins_str):
    if not major_wins_str or "None" in major_wins_str:
        return "SW"
    if "G1" in major_wins_str: return "G1"
    if "G2" in major_wins_str: return "G2"
    if "G3" in major_wins_str: return "G3"
    return "SW"

def parse_earnings(race_record):
    total_earnings = 0
    if not race_record: return 0
    for stats in race_record.values():
        earnings = stats.get("Earnings", 0)
        if isinstance(earnings, str):
            cleaned = re.sub(r'[^\d]', '', earnings)
            earnings = int(cleaned) if cleaned else 0
        total_earnings += earnings
    return total_earnings

def parse_races(major_wins_str):
    if not major_wins_str or "None" in major_wins_str: return []
    return [line.strip() for line in major_wins_str.split("\n") if line.strip()]

def main():
    print("🚀 Starting Crosses Database Builder with Pedigree Auto-Fill...")
    stallions, current_crosses, pedigree_db = load_databases()
    
    if not stallions:
        print("❌ Could not find stallion profiles data. Verify stallion_profiles.py exists.")
        return

    existing_names = {horse['name'].strip().upper() for horse in current_crosses}
    added_count = 0

    for name, data in stallions.items():
        upper_name = name.strip().upper()
        if upper_name in existing_names:
            print(f"⏩ Skipping {name} (Already exists)")
            continue
            
        # Dynamically discover Sire and Damsire from pedigree_map
        sire, damsire = find_lineage(name, pedigree_db)
        
        major_wins_str = data.get("Major Wins", "")
        races_list = parse_races(major_wins_str)
        class_tier = parse_highest_class(major_wins_str)
        total_earnings = parse_earnings(data.get("Race Record", {}))
        
        new_record = {
            'class': class_tier,
            'damsire': damsire,
            'earnings': total_earnings,
            'name': name.upper(),
            'races': races_list,
            'sex': 'c',
            'sire': sire,
            'winning surface': data.get("Surface", "Dirt"),
            'year': data.get("Year Born", 0)
        }
        
        current_crosses.append(new_record)
        existing_names.add(upper_name)
        added_count += 1
        print(f"✅ Formatted {name} -> Sire: {sire} | Damsire: {damsire}")

    if added_count == 0:
        print("ℹ️ No new stallions to add.")
        return

    try:
        with open(CROSSES_DB_FILE, "w", encoding="utf-8") as f:
            f.write("# Successful Historical Crosses Database\n")
            f.write("SUCCESSFUL_CROSSES_LIST = " + pprint.pformat(current_crosses, indent=4, width=120) + "\n")
        print(f"\n🎉 Successfully updated {CROSSES_DB_FILE} with {added_count} pedigree-complete records!")
    except Exception as e:
        print(f"\n❌ Error writing file updates: {e}")

if __name__ == "__main__":
    main()