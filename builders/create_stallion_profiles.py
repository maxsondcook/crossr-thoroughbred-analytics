import datetime
import os
import ast

def get_parenthesized_multiline(prompt):
    print(prompt + " (Type 'DONE' on a new line when finished):")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "DONE":
            break
        cleaned_line = line.replace('"', '\\"')
        lines.append(f"{cleaned_line}\n")
    return "".join(lines)

def clean_numeric_input(val_str):
    """Strips out commas, dollar signs, and whitespace so int parsing doesn't fail."""
    return val_str.replace(",", "").replace("$", "").strip()

def input_stallion_stats():
    print("=== Centralized Stallion Profile Entry Wizard ===")
    
    farm_modules = [
        "airdrie_stud", "claiborne_farm", "coolmore_ashford_stud", "darley_stud",
        "gainesway_farm", "hill_n_dale", "lane's_end_farm", "spendthrift_farm",
        "taylor_made_farm", "three_chimneys_farm", "winstar_farm"
    ]
    
    print("\nSelect the destination Farm:")
    for idx, farm in enumerate(farm_modules, 1):
        print(f" [{idx}] {farm.replace('_', ' ').title()}")
        
    try:
        farm_choice = int(input("\nEnter farm number: ").strip())
        selected_farm_file = farm_modules[farm_choice - 1]
    except (ValueError, IndexError):
        print("❌ Invalid selection.")
        return

    horse_name = input("\nHorse Name (e.g., War Front): ").strip()
    
    try:
        birth_year = int(clean_numeric_input(input(f"What year was {horse_name} born? (e.g., 2002): ")))
    except ValueError:
        birth_year = 0
        
    try:
        stud_year = int(clean_numeric_input(input(f"What year did {horse_name} start stud duty? (e.g., 2007): ")))
    except ValueError:
        stud_year = 0
        
    try:
        stud_fee = int(clean_numeric_input(input(f"What is the current Stud Fee for {horse_name}? (e.g., 75000): ")))
    except ValueError:
        stud_fee = 0

    status = input("Status (e.g., active, retired, pensioned): ").strip() or "active"
    surface = input("Surface (e.g., Dirt, Turf, Both): ").strip() or "Dirt"
    
    # Height Field
    height = input("Height (e.g., 15.3 hh): ").strip() or "N/A"
    
    # Conformation & Physical Profile
    conformation = get_parenthesized_multiline(f"Enter Conformation & Physical Profile for {horse_name}")

    major_wins = get_parenthesized_multiline(f"Enter Major Wins for {horse_name}")
    highlight = get_parenthesized_multiline(f"Enter Career Highlight for {horse_name}")
    
    # Streamlined Race Record Collection
    race_record = {}
    print("\n--- Enter Race Record (Press Enter to Finish or type 'retire' to signal career end) ---")
    while True:
        age_input = input("Age (e.g., 2, 3, 4 or type 'retire'): ").strip()
        if not age_input:
            break
            
        if age_input.lower() == "retire":
            print(f"🏁 Career end recorded for {horse_name}.")
            break
            
        try:
            age_num = int(clean_numeric_input(age_input))
            age_key = f"Age {age_num}"
            # Automatically calculate Season based on birth year
            calculated_season = str(birth_year + age_num) if birth_year > 0 else "N/A"
        except ValueError:
            print("  ❌ Please enter a valid number for age, leave blank to finish, or type 'retire'.")
            continue
        
        print(f"  Season / Year for {age_key}: {calculated_season}")
        try:
            starts = int(clean_numeric_input(input(f"  Starts at {age_key}: ")) or 0)
            firsts = int(clean_numeric_input(input(f"  1sts at {age_key}: ")) or 0)
            seconds = int(clean_numeric_input(input(f"  2nds at {age_key}: ")) or 0)
            thirds = int(clean_numeric_input(input(f"  3rds at {age_key}: ")) or 0)
            earnings = int(clean_numeric_input(input(f"  On-Track Earnings ($) at {age_key}: ")) or 0)
        except ValueError:
            print("  ❌ Invalid number input. Defaulting values to 0.")
            starts, firsts, seconds, thirds, earnings = 0, 0, 0, 0, 0
            
        accomplishments = input(f"  Major Accomplishments & Key Races for {age_key}: ").strip() or "N/A"
        
        race_record[age_key] = {
            "Season": calculated_season,
            "Starts": starts,
            "1sts": firsts,
            "2nds": seconds,
            "3rds": thirds,
            "Earnings": earnings,
            "Major Accomplishments": accomplishments
        }
        print()

    # Dynamic Progeny Stat Checking
    current_year = datetime.datetime.now().year
    years_since_stud = current_year - stud_year

    if years_since_stud <= 3:
        if years_since_stud == 3:
            print(f"\n📢 {horse_name} is a sophomore stallion whose first runners are two-year-olds.")
        else:
            print(f"\n📢 {horse_name} has been at stud for {years_since_stud} year(s) and has no foals of racing age.")
        
        combined_earnings_str = "n/a (n/a)"
        progeny_awd = "n/a"
        gsw_g1w = "n/a"
    else:
        print("\n--- Legacy Progeny Stats ---")
        progeny_earnings = input("Total Progeny Earnings (e.g., $98,450,115): ").strip() or "n/a"
        earnings_per_runner = input("Average Earnings per Runner (e.g., $67,896): ").strip() or "n/a"
        progeny_awd = input("Average Progeny Win Distance (e.g., 7.04): ").strip() or "n/a"
        gsw_g1w = input("GSW / G1W Ratio (e.g., 62/24): ").strip() or "n/a"
        
        if progeny_earnings != "n/a" and earnings_per_runner != "n/a":
            combined_earnings_str = f"{progeny_earnings} ({earnings_per_runner})"
        else:
            combined_earnings_str = "n/a (n/a)"
        
    lookup_key = horse_name.lower().replace(" ", "_")
    pedigree_var = f"{lookup_key}_pedigree"

    new_profile_data = {
        "Farm": selected_farm_file,
        "Year Born": birth_year,
        "Stud Duty Year": stud_year,
        "Stud Fee": stud_fee,
        "Status": status,
        "Surface": surface,
        "Height": height,
        "Conformation & Physical Profile": conformation,
        "Major Wins": major_wins,
        "Career Highlight": highlight,
        "Race Record": race_record,
        "Legacy Progeny Stats": {
            "Total Earnings": combined_earnings_str,
            "Average Progeny Win Distance": progeny_awd,
            "GSW / G1W Ratio": gsw_g1w
        },
        "Pedigree": pedigree_var
    }
    
    # Load Master Database
    profiles_file = "stallion_profiles.py"
    master_profiles = {}
    
    if os.path.exists(profiles_file):
        with open(profiles_file, "r", encoding="utf-8") as f:
            content = f.read()
            if "stallion_profiles = {" in content:
                try:
                    dict_start = content.find("stallion_profiles = {") + len("stallion_profiles = ")
                    dict_str = content[dict_start:].strip()
                    master_profiles = ast.literal_eval(dict_str)
                except Exception:
                    master_profiles = {}

    # Update entry
    master_profiles[horse_name] = new_profile_data
    
    # Save formatted python output back to disk
    fallback_str = '            "\\n"\n'
    with open(profiles_file, "w", encoding="utf-8") as f:
        f.write('"""\nMaster Centralized Stallion Profiles Database\n"""\n\n')
        f.write('stallion_profiles = {\n')
        
        for k_idx, (horse, data) in enumerate(master_profiles.items()):
            f.write(f'    "{horse}": {{\n')
            f.write(f'        "Farm": "{data["Farm"]}",\n')
            f.write(f'        "Year Born": {data["Year Born"]},\n')
            f.write(f'        "Stud Duty Year": {data["Stud Duty Year"]},\n')
            f.write(f'        "Stud Fee": {data["Stud Fee"]},\n')
            f.write(f'        "Status": "{data["Status"]}",\n')
            f.write(f'        "Surface": "{data["Surface"]}",\n')
            f.write(f'        "Height": "{data.get("Height", "N/A")}",\n')
            
            # Conformation Layout
            conf_lines = "".join([f'            "{line.strip()}\\n"\n' for line in data.get("Conformation & Physical Profile", "").split('\n') if line.strip()])
            if not conf_lines:
                conf_lines = fallback_str
            f.write(f'        "Conformation & Physical Profile": (\n{conf_lines}        ),\n')
            
            # Major Wins Layout
            mw_lines = "".join([f'            "{line.strip()}\\n"\n' for line in data["Major Wins"].split('\n') if line.strip()])
            if not mw_lines:
                mw_lines = fallback_str
            f.write(f'        "Major Wins": (\n{mw_lines}        ),\n')
            
            # Career Highlight Layout
            ch_lines = "".join([f'            "{line.strip()}\\n"\n' for line in data["Career Highlight"].split('\n') if line.strip()])
            if not ch_lines:
                ch_lines = fallback_str
            f.write(f'        "Career Highlight": (\n{ch_lines}        ),\n')
            
            # Record Layout
            f.write('        "Race Record": {\n')
            rr_items = []
            for age, s in data["Race Record"].items():
                escaped_acc = s.get("Major Accomplishments", "N/A").replace('"', '\\"')
                item_str = (
                    f'            "{age}": {{'
                    f'"Season": "{s.get("Season", "N/A")}", '
                    f'"Starts": {s.get("Starts", 0)}, '
                    f'"1sts": {s.get("1sts", 0)}, '
                    f'"2nds": {s.get("2nds", 0)}, '
                    f'"3rds": {s.get("3rds", 0)}, '
                    f'"Earnings": {s.get("Earnings", 0)}, '
                    f'"Major Accomplishments": "{escaped_acc}"}}'
                )
                rr_items.append(item_str)
            f.write(",\n".join(rr_items) + "\n        },\n")
            
            # Progeny Stats Layout
            f.write('        "Legacy Progeny Stats": {\n')
            f.write(f'            "Total Earnings": "{data["Legacy Progeny Stats"]["Total Earnings"]}",\n')
            f.write(f'            "Average Progeny Win Distance": "{data["Legacy Progeny Stats"]["Average Progeny Win Distance"]}",\n')
            f.write(f'            "GSW / G1W Ratio": "{data["Legacy Progeny Stats"]["GSW / G1W Ratio"]}"\n')
            f.write('        },\n')
            
            f.write(f'        "Pedigree": "{data["Pedigree"]}"\n')
            
            comma = "," if k_idx < len(master_profiles) - 1 else ""
            f.write(f"    }}{comma}\n")
            
        f.write('}\n')
    print(f"✨ Successfully added/updated '{horse_name}' inside {profiles_file}!")

if __name__ == "__main__":
    input_stallion_stats()