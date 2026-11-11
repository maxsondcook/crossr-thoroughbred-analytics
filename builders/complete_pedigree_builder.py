import pprint
import pedigrees_database

# Access your current database map
db = getattr(pedigrees_database, "pedigree_map", {})

def clean_name(name):
    """Normalizes horse names to match dictionary keys."""
    if not name:
        return ""
    # Strip everything inside parentheses (like years or country codes)
    import re
    cleaned = re.sub(r'\(.*?\)', '', name)
    return cleaned.strip().lower()

def fill_pedigree(horse_dict, current_depth=0, max_depth=5):
    """
    Recursively look up and populate missing ancestors up to generation 5 (31 total horses).
    """
    if not horse_dict or current_depth >= max_depth:
        return horse_dict

    horse_name = horse_dict.get("Horse", "")
    lookup_key = clean_name(horse_name)

    # If the current branch is missing details, check if we have them elsewhere in the master db
    if ("Sire" not in horse_dict or "Dam" not in horse_dict) and lookup_key in db:
        master_record = db[lookup_key]
        if "Sire" in master_record and "Sire" not in horse_dict:
            horse_dict["Sire"] = master_record["Sire"]
        if "Dam" in master_record and "Dam" not in horse_dict:
            horse_dict["Dam"] = master_record["Dam"]

    # Recursively fill out the Sire line
    if "Sire" in horse_dict:
        horse_dict["Sire"] = fill_pedigree(horse_dict["Sire"], current_depth + 1, max_depth)
    else:
        # Create placeholders if no sire info exists anywhere to preserve the 5-gen tree size
        horse_dict["Sire"] = {"Horse": f"Unknown Sire of {horse_name}"}
        horse_dict["Sire"] = fill_pedigree(horse_dict["Sire"], current_depth + 1, max_depth)

    # Recursively fill out the Dam line
    if "Dam" in horse_dict:
        horse_dict["Dam"] = fill_pedigree(horse_dict["Dam"], current_depth + 1, max_depth)
    else:
        # Create placeholders if no dam info exists anywhere
        horse_dict["Dam"] = {"Horse": f"Unknown Dam of {horse_name}"}
        horse_dict["Dam"] = fill_pedigree(horse_dict["Dam"], current_depth + 1, max_depth)

    return horse_dict

# Process every single record in your database
completed_database = {}
print("Processing 2,319 horses and generating 5-generation trees...")

for key, partial_tree in db.items():
    # Make a copy and fill it completely up to 5 generations deep (31 horses per entry)
    import copy
    full_tree = copy.deepcopy(partial_tree)
    completed_database[key] = fill_pedigree(full_tree, current_depth=0, max_depth=5)

# Write the completed database directly back out to a new Python file
print("Writing completely autofilled database to 'completed_dirt_pedigrees.py'...")
with open("completed_dirt_pedigrees.py", "w", encoding="utf-8") as f:
    f.write('"""\nCompletely populated 5-Generation Pedigree Database (31 Horses Per Line)\n"""\n\n')
    f.write('pedigree_map = {\n')
    for key, tree in completed_database.items():
        # Formats each line cleanly as valid executable Python syntax
        f.write(f"    {repr(key)}: {repr(tree)},\n")
    f.write('}\n')

print("✅ Done! 'completed_dirt_pedigrees.py' generated successfully.")