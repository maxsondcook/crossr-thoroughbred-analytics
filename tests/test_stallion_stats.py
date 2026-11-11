from crossr_core.stallion_stats import bms_crosses


def test_groups_by_broodmare_sire_counting_each_horse_once():
    rows = bms_crosses([
        {"name": "Life Is Good", "broodmare_sire": "Distorted Humor", "class": "MG1W"},
        {"name": "Citizen Bull", "broodmare_sire": "Distorted Humor", "class": "MG1W"},
        {"name": "Life Is Good", "broodmare_sire": "Distorted Humor", "class": "MG1W"},  # duplicate
        {"name": "Pretty Mischievous", "broodmare_sire": "Tapit Gr/R", "class": "MG1W"},
        {"name": "A Tapit Mare Foal", "broodmare_sire": "Tapit", "class": "G3W"},
        {"name": "Some Allowance Star", "broodmare_sire": "Tapit", "class": "MSW"},
        {"name": "Graded Twice", "broodmare_sire": "Bernardini", "class": "MGSW"},
    ])
    dh = next(r for r in rows if r["broodmare_sire"] == "Distorted Humor")
    assert dh["mg1w"] == 2 and dh["gsw"] == 2
    tapit = next(r for r in rows if r["broodmare_sire"] == "Tapit")  # "Tapit Gr/R" merged in
    assert (tapit["mg1w"], tapit["g3w"], tapit["sw"], tapit["gsw"]) == (1, 1, 1, 2)
    bern = next(r for r in rows if r["broodmare_sire"] == "Bernardini")
    assert bern["gsw"] == 1 and bern["g1w"] == 0 and bern["g2w"] == 0
    assert rows[0]["broodmare_sire"] == "Distorted Humor"  # most MG1Ws first


def test_empty_input():
    assert bms_crosses([]) == [] and bms_crosses(None) == []
