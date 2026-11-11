import importlib
import pprint
import re
import os
import sys

# FIX: Get the absolute path of the root 'Pedigrees' folder and add it to python path
# This assumes 'american_stallion_farms' is in the parent directory of the 'builders' folder
current_script_dir = os.path.dirname(os.path.abspath(__file__))
project_root_dir = os.path.dirname(current_script_dir) 

if project_root_dir not in sys.path:
    sys.path.insert(0, project_root_dir)

# The farm files inside the 'american_stallion_farms' subfolder
farm_files = [
    "airdrie_stud",
    "claiborne_farm",
    "coolmore_ashford_stud",
    "gainesway_farm",
    "lane's_end_farm",
    "spendthrift_farm",
    "taylor_made_farm",
    "three_chimneys_farm",
    "winstar_farm",
    "darley_stud",
    "hill_n_dale"
]

compiled_map = {}

def clean_horse_name(name: str) -> str:
    """Matches the exact cleaning logic used in testing2.py"""
    if not name or not isinstance(name, str):
        return ""
    cleaned = re.sub(r'\(.*?\)', '', name)
    cleaned = cleaned.replace(".", "")
    cleaned = " ".join(cleaned.split())
    return cleaned.strip().lower()

def extract_all_horses(pedigree_dict):
    """Recursively walks down the pedigree tree to pull out every single horse record."""
    if not isinstance(pedigree_dict, dict) or "Horse" not in pedigree_dict:
        return
    
    # Standardize the horse name as the dictionary key using unified logic
    horse_name = clean_horse_name(pedigree_dict["Horse"])
    
    # Add it to our compiled map if it's not already there
    if horse_name and horse_name not in compiled_map:
        compiled_map[horse_name] = pedigree_dict
        
    # Recursively look down the Sire line
    if "Sire" in pedigree_dict:
        extract_all_horses(pedigree_dict["Sire"])
        
    # Recursively look down the Dam line
    if "Dam" in pedigree_dict:
        extract_all_horses(pedigree_dict["Dam"])


for farm in farm_files:
    try:
        # Dynamically import using the subfolder package path
        module_path = f"american_stallion_farms.{farm}"
        module = importlib.import_module(module_path)
        
        for attr_name in dir(module):
            if attr_name.endswith("_pedigree"):
                main_pedigree = getattr(module, attr_name)
                extract_all_horses(main_pedigree)
                
    except ModuleNotFoundError:
        print(f"Skipping missing file: american_stallion_farms/{farm}.py")

# FIX: Write the output directly to the root project folder so testing2.py can easily import it
output_path = os.path.join(project_root_dir, "pedigrees_database.py")

with open(output_path, "w", encoding="utf-8") as f:
    f.write('"""\nAutomatically compiled pedigree database\n"""\n\n')
    f.write("pedigree_map = {\n")
    for i, (key, value) in enumerate(sorted(compiled_map.items())):
        formatted_tree = pprint.pformat(value, indent=4, width=100)
        indented_lines = []
        for line_idx, line in enumerate(formatted_tree.splitlines()):
            if line_idx == 0:
                indented_lines.append(f"    {repr(key)}: {line}")
            else:
                indented_lines.append(f"    {line}")
        
        block_content = "\n".join(indented_lines)
        if i < len(compiled_map) - 1:
            f.write(block_content + ",\n\n")
        else:
            f.write(block_content + "\n")
            
    f.write("}\n")

print(f"✨ Successfully generated a clean pedigrees_database.py file at {output_path}!")