"""Dish-tag regex rules match the intended menu language and skip false positives.

Tags are pure config (config/dish_tags.json); tag_dishes.load_rules() compiles
it. Testing at that boundary -- the real regex, the live config -- means a
rule that's malformed, too narrow, or over-matches breaks a test here instead
of quietly under- or over-tagging restaurants no one is watching for it.
"""
from tag_dishes import load_rules

RULES = load_rules()


def hits(tag, text):
    return any(pat.search(text) for pat, _ in RULES[tag])


def best_conf(tag, text):
    """'high' beats 'low', mirroring tag_dishes.main()'s own preference."""
    found = [conf for pat, conf in RULES[tag] if pat.search(text)]
    if not found:
        return None
    return "high" if "high" in found else "low"


def test_beef_wellington_matches():
    assert hits("beef wellington", "Beef Wellington, potato puree, red wine jus")


def test_beef_wellington_skips_unrelated_beef():
    assert not hits("beef wellington", "grilled beef tenderloin, chimichurri")


def test_dry_aged_steak_matches():
    assert hits("dry-aged steak", "24oz dry-aged ribeye, bone marrow butter")
    assert hits("dry-aged steak", "dry aged NY strip")


def test_wagyu_matches():
    assert hits("wagyu / a5", "Japanese A5 Wagyu striploin, 4oz")
    assert hits("wagyu / a5", "Kobe beef sliders")


def test_wagyu_skips_bare_grade_code():
    assert not hits("wagyu / a5", "Table A5, party of four")


def test_uni_matches():
    assert hits("uni", "uni toast, chive, brioche")
    assert hits("uni", "sea urchin custard")


def test_uni_skips_university():
    assert not hits("uni", "Columbia University catering menu")


def test_caviar_matches():
    assert hits("caviar", "caviar service, blini, creme fraiche")
    assert hits("caviar", "ossetra with potato chip")


def test_caviar_skips_beluga_lentils():
    assert not hits("caviar", "beluga lentils, roasted carrot, yogurt")


def test_bone_marrow_matches():
    assert hits("bone marrow", "roasted bone marrow, parsley salad, grilled bread")


def test_foie_gras_matches():
    assert hits("foie gras", "seared foie gras, brioche, fig jam")


def test_foie_gras_skips_unrelated():
    assert not hits("foie gras", "chicken liver mousse, toast points")


def test_cassoulet_matches():
    assert hits("cassoulet", "duck confit cassoulet, toulouse sausage")


def test_bouillabaisse_matches():
    assert hits("bouillabaisse", "bouillabaisse, rouille, grilled baguette")


def test_whole_duck_matches():
    assert hits("duck a l'orange / whole duck", "duck a l'orange, for two")
    assert hits("duck a l'orange / whole duck", "canard a l'orange")
    assert hits("duck a l'orange / whole duck", "Peking duck, hoisin, scallion pancake")


def test_whole_duck_skips_duck_breast():
    assert not hits("duck a l'orange / whole duck", "seared duck breast, cherry gastrique")


def test_oysters_matches():
    assert hits("oysters", "half dozen oysters, mignonette")
    assert hits("oysters", "oyster on the half shell")


def test_oysters_skips_oyster_mushroom():
    assert not hits("oysters", "oyster mushroom risotto")


def test_raw_seafood_matches():
    assert hits("crudo / ceviche", "hamachi crudo, yuzu, jalapeno")
    assert hits("crudo / ceviche", "branzino ceviche, leche de tigre")
    assert hits("crudo / ceviche", "scallop carpaccio")


def test_raw_seafood_skips_cooked_dish():
    assert not hits("crudo / ceviche", "grilled branzino, salsa verde")


def test_raw_seafood_skips_vegetable_carpaccio():
    # Found live in the srw26 menus: Leon's "Carpaccio di Funghi Trifolati",
    # a mushroom dish, and plain squash/fennel carpaccios elsewhere.
    assert not hits("crudo / ceviche", "squash carpaccio, pumpkin seed vinaigrette")
    assert not hits("crudo / ceviche", "fennel carpaccio, citrus, shaved parmesan")
    assert not hits("crudo / ceviche", "carpaccio di funghi trifolati")
    assert not hits("crudo / ceviche", "watermelon carpaccio, baby kale, bentonleaf")


def test_monkfish_matches():
    assert hits("monkfish", "pan-roasted monkfish, saffron broth")


def test_eel_matches():
    assert hits("eel", "grilled eel, sticky rice")
    assert hits("eel", "unagi don")


def test_eel_skips_unrelated_word():
    assert not hits("eel", "steel-cut oats")


def test_rabbit_matches():
    assert hits("rabbit", "braised rabbit leg, mustard jus")
    assert hits("rabbit", "coniglio alla cacciatora")


def test_pigeon_matches():
    assert hits("pigeon / squab", "roasted squab, foie gras, cherry")
    assert hits("pigeon / squab", "pigeon breast, beets")


def test_souffle_matches():
    assert hits("souffle", "grand marnier souffle, creme anglaise")
    assert hits("souffle", "cheese soufflé au gruyère")


def test_sweetbreads_matches():
    assert hits("sweetbreads", "crispy veal sweetbreads, capers, brown butter")


def test_truffle_matches():
    assert hits("truffle", "shaved black truffle, tagliolini")
    assert hits("truffle", "white truffle risotto")


def test_truffle_skips_chocolate_and_oil():
    # Chocolate truffles are a candy; truffle oil/fries are synthetic aroma,
    # not the ingredient this tag is hunting.
    assert not hits("truffle", "dark chocolate truffles, sea salt")
    assert not hits("truffle", "truffle oil, parmesan fries")
    assert not hits("truffle", "truffle fries with aioli")


def test_steak_tartare_matches():
    assert hits("steak tartare", "steak tartare, cured egg yolk, crostini")
    assert hits("steak tartare", "beef tartare with smoked shallot")


def test_steak_tartare_skips_tuna_and_sauce():
    assert not hits("steak tartare", "tuna tartare, avocado")
    assert not hits("steak tartare", "fried fish, tartar sauce")


def test_octopus_matches():
    assert hits("octopus", "charcoal grilled octopus, onions, capers")
    assert hits("octopus", "polpo alla griglia")
    assert hits("octopus", "pulpo a la gallega")


def test_game_meats_matches():
    assert hits("game meats", "venison loin, juniper, parsnip")
    assert hits("game meats", "wild boar ragu pappardelle")
    assert hits("game meats", "elk chop, huckleberry")


def test_dover_sole_matches():
    assert hits("dover sole", "dover sole meuniere, tableside")


def test_paella_matches():
    assert hits("paella", "paella de mariscos, bomba rice, saffron")


def test_lobster_thermidor_matches():
    assert hits("lobster thermidor", "lobster thermidor, gruyere gratin")


def test_lobster_thermidor_skips_plain_lobster():
    assert not hits("lobster thermidor", "lobster roll, drawn butter")


def test_game_meats_skips_quail_egg_garnish():
    # Found live: the Boucherie group garnishes steak tartare with a quail
    # egg -- an egg on a crostini is not a game-bird dish.
    assert not hits("game meats", "capers, cornichons, shallots, quail egg, crostini")
    assert not hits("game meats", "fried shrimp with quail eggs")
    assert hits("game meats", "roasted quail, cherry, farro")


def test_game_meats_skips_winery_name():
    # Found live: Harta's wine list carries Elk Cove Vineyards riesling.
    assert not hits("game meats", "ELK COVE VINEYARDS ESTATE RIESLING 17/85")


def test_truffle_confidence_split():
    # Found live: "Truffle Tater Tots" / "Truffle Potato Wedges" are
    # truffle-oil dishes. A named variety or shaving is the real thing.
    assert best_conf("truffle", "shaved black truffle, tagliolini") == "high"
    assert best_conf("truffle", "white truffle risotto") == "high"
    assert best_conf("truffle", "truffle tater tots") == "low"
    assert best_conf("truffle", "truffle arancini") == "low"
    assert best_conf("truffle", "dark chocolate truffles, sea salt") is None
