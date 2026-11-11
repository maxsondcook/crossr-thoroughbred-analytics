"""Horse-name cleaning shared by the pipeline jobs (main.clean_horse_name, loaded without importing main)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_clean_horse_name = None


def load_clean_horse_name():
    """Use main.clean_horse_name without importing main.

    Importing main executes load_and_sync_all_data(), which execs the 34MB
    pedigree file and deep-copies every tree.
    """
    global _clean_horse_name
    if _clean_horse_name is not None:
        return _clean_horse_name
    tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8"))
    # Module-level private constants (e.g. a compiled regex) the function uses.
    constants = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and all(isinstance(t, ast.Name) and t.id.startswith("_") for t in node.targets)
    ]
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "clean_horse_name":
            module = ast.Module(body=[*constants, node], type_ignores=[])
            ast.fix_missing_locations(module)
            namespace = {"re": re}
            exec(compile(module, str(REPO / "main.py"), "exec"), namespace)
            _clean_horse_name = namespace["clean_horse_name"]
            return _clean_horse_name
    raise RuntimeError("main.clean_horse_name was not found")


def clean_name(value: str) -> str:
    return load_clean_horse_name()(fold_quotes(value or ""))


def fold_quotes(value: str) -> str:
    return (
        (value or "")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u02bc", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


