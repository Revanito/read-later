"""Built-in reading-map presets and the code that applies one to a series.

A preset describes a series' reading map: sections in order (a row, chains of rows, a set of arc
columns, a single column, or a loose "any order" group), the books in each, and the wires between
them. Applying it matches books already in the library by title (plus aliases), creates the missing
ones as manual books, and stores the map in the map_* tables (see books_db).

Horus Heresy: reading order and narrative arcs from wasniahC's "Horus Heresy Reading Order -
Narrative Arcs" chart (last updated 2022-01-12). Its 23 coloured lines are paired into arc columns;
every book keeps dots for all the lines it belongs to. Siege of Terra isn't in that chart and is
appended after The Buried Dagger. Neither are the Primarchs and Characters novels (Hardcover lists them
as separate series): reading guides agree they fit any time after the first five novels, so they get an
"any order" section, except Sigismund and Eidolon (set on the eve of the Siege), which join Act 3's
prelude. Luther and Zardu Layak carry a note on the book they're best read after.
"""
import books_db

# --- helpers ---------------------------------------------------------------------------------------


def e(title, sub="", lines="", stories=(), o=None, aliases=(), part_of=None, author="", lane="", style="",
      note="", note_fr="", sub_fr=""):
    """One map entry. `lines` is a space-separated list of line codes, `o` the narrative list order,
    `part_of` the title of the entry this one shares a cover with (split anthologies). Lanes sections
    also use `lane` (lane id), `style` (prequel / companion: dashed card) and `note` (shown with a "?"
    when the placement is uncertain)."""
    return {"title": title, "sub": sub, "lines": lines.split(), "stories": list(stories), "o": o,
            "aliases": list(aliases), "part_of": part_of, "author": author, "lane": lane, "style": style,
            "note": note, "note_fr": note_fr, "sub_fr": sub_fr}


def _num(n, extra=""):
    return f"#{n}" + (f" · {extra}" if extra else "")


# Entry labels ("#31 · up to Kryptos", "Novella", "Siege of Terra #3") in French. Story and book names
# inside them stay as published.
_SUB_FR = [
    ("Siege of Terra #", "Siège de Terra #"), ("Discworld #", "Disque-monde #"),
    ("Audio drama", "Drame audio"), ("Graphic novel", "Roman graphique"), ("Short", "Nouvelle"),
    ("up to Kryptos", "jusqu'à Kryptos"), ("Wolf's Claw onwards", "à partir de Wolf's Claw"),
    ("up to Shield of Lies", "jusqu'à Shield of Lies"), ("Garro part 3", "Garro, partie 3"),
    (" · part", " · partie"),
    ("Prequel · read here", "Préquelle · à lire ici"), ("Prequel", "Préquelle"), ("Companion", "Compagnon"),
    ("Standalone", "Indépendant"), ("Book ", "Tome "), ("finale", "final"),
    ("Primarchs anthology", "Anthologie des Primarques"), ("Primarchs #", "Primarques #"),
    ("Characters #", "Personnages #"), ("Unification Wars prequel", "préquelle, guerres d'Unification"),
    ("best after", "de préférence après"), ("eve of the Siege", "à la veille du Siège"),
]


def sub_fr(sub: str) -> str:
    out = sub or ""
    for en, fr in _SUB_FR:
        out = out.replace(en, fr)
    return out if out != sub else ""


# --- The Horus Heresy + Siege of Terra -------------------------------------------------------------

HH_LINES = {
    "DA": ["Dark Angels", "#000000", "#e03030"], "WS": ["White Scars", "#f4f4f4", "#e03030"],
    "SW": ["Space Wolves", "#9a9a9a", "#b02020"], "IF": ["Imperial Fists", "#f0e040", "#3a3a3a"],
    "BA": ["Blood Angels", "#d02020", "#111111"], "IH": ["Iron Hands", "#111111", "#a0a0a0"],
    "UM": ["Ultramarines", "#2a4cff", "#f2c14e"], "SA": ["Salamanders", "#1f9a2f", "#f29a3a"],
    "RG": ["Raven Guard", "#111111", "#dddddd"], "EC": ["Emperor's Children", "#7a2ad6", "#f2c14e"],
    "IW": ["Iron Warriors", "#5a5a5a", "#aaaaaa"], "NL": ["Night Lords", "#1a1a7a", "#e03030"],
    "WE": ["World Eaters", "#f4f4f4", "#2a4cff"], "DG": ["Death Guard", "#f4f4f4", "#9fd49f"],
    "TS": ["Thousand Sons", "#d02020", "#f2c14e"], "SH": ["Sons of Horus", "#6aab9c", "#111111"],
    "WB": ["Word Bearers", "#a01010", "#a0a0a0"], "AL": ["Alpha Legion", "#6a6aff", "#6cd04a"],
    "IM": ["The Imperium", "#fdf0cf", "#e03030", "L'Imperium"],
    "KE": ["Knights Errant", "#9a9a9a", "#f2c14e", "Chevaliers Errants"],
    "SL": ["Shattered Legions", "#111111", "#2fbf3f", "Légions Brisées"],
    "LD": ["Lost & Damned", "#a0104a", "#111111", "Perdus et Damnés"],
    "PE": ["Perpetuals", "#fdf0cf", "#111111", "Perpétuels"],
}

_SOT = "Siege of Terra"


def _pr(title, n, line, author):
    return e(title, f"Primarchs #{n}", line, author=author)

HORUS_HERESY = {
    "key": "horus-heresy",
    "name": "The Horus Heresy + Siege of Terra",
    "name_fr": "L'Hérésie d'Horus + Le Siège de Terra",
    "description_fr": ("L'ordre de lecture par arcs narratifs de wasniahC : l'Acte 1, les 49 entrées de l'Acte 2 en "
                       "10 colonnes d'arcs plus les anthologies, l'Acte 3, puis le Siège de Terra, avec les romans "
                       "Primarques et Personnages. 125 titres, avec des pastilles pour les 23 fils narratifs du "
                       "schéma. Les séries Primarques et Personnages déjà importées y sont fusionnées."),
    "description": ("wasniahC's narrative-arcs reading order: Act 1, Act 2's 49 entries in 10 arc columns plus "
                    "the anthologies, Act 3, then Siege of Terra, plus the Primarchs and Characters novels. "
                    "125 titles, with dots for the chart's 23 narrative lines. Primarchs / Characters series "
                    "you imported are folded into it."),
    "match_series": ["horus heresy", "siege of terra"],
    "author": "",
    "col": 112,
    "lines": HH_LINES,
    "sections": [
        {"kind": "row", "name": "Act 1 · Start here", "name_fr": "Acte 1 · Par où commencer", "color": "#5ec2a3", "books": [
            e("Horus Rising", _num(1)), e("False Gods", _num(2)), e("Galaxy in Flames", _num(3)),
            e("The Flight of the Eisenstein", _num(4)), e("Fulgrim", _num(5))]},
        {"kind": "chains", "name": "Act 1 · Other early reads", "color": "#7fb6d8",
         "name_fr": "Acte 1 · Autres premières lectures",
         "hint": "Crucial context: read these before book #16 onwards",
         "hint_fr": "Contexte essentiel : à lire avant le tome 16 et les suivants", "rows": [
            {"books": [e("The Kaban Project", "Short"), e("Mechanicum", _num(9))]},
            {"books": [e("A Thousand Sons", _num(12)), e("The First Heretic", _num(14)), e("Aurelian", "Novella")]},
            {"indent": 1, "books": [e("Prospero Burns", _num(15))]},
        ]},
        {"kind": "cols", "name": "Act 2", "name_fr": "Acte 2", "groups": [
            {"name": "Dark Angels", "color": "#3fa37a", "books": [
                e("Descent of Angels", _num(6), "DA", o=1), e("Fallen Angels", _num(11), "DA", o=5),
                e("Angels of Caliban", _num(38), "DA NL UM BA", o=42)]},
            {"name": "Raven Guard & Alpha Legion", "name_fr": "Raven Guard et Alpha Legion", "color": "#9a9ccf", "books": [
                e("Legion", _num(7), "AL PE", o=2), e("Deliverance Lost", _num(18), "RG AL", o=9),
                e("Corax", _num(40), "RG", o=44)]},
            {"name": "Ultramarines, Word Bearers & Blood Angels", "name_fr": "Ultramarines, Word Bearers et Blood Angels", "color": "#d4934a", "books": [
                e("Battle for the Abyss", _num(8), "WB", o=3), e("Know No Fear", _num(19), "UM WB PE", o=10),
                e("Macragge's Honour", "Graphic novel", "UM WB", o=11),
                e("The Honoured", "Novella", "UM WB", o=12), e("The Unburdened", "Novella", "UM WB", o=13),
                e("Fear to Tread", _num(21), "BA WB", o=16), e("Betrayer", _num(24), "WE WB UM PE", o=20),
                e("The Unremembered Empire", _num(27), "UM DA NL BA WB IF SA PE", o=25),
                e("Pharos", _num(34), "UM NL", o=38), e("Ruinstorm", _num(46), "DA BA UM NL LD", o=48)]},
            {"name": "The Imperium & Knights Errant", "name_fr": "L'Imperium et les Chevaliers Errants", "color": "#e6d3a3", "books": [
                e("Nemesis", _num(13), "IM", o=6), e("The Outcast Dead", _num(17), "KE", o=8),
                e("Cybernetica", "Novella", "IM", o=15),
                e("Garro (Parts 1–2)", _num(42, "up to Shield of Lies"), "KE IM", o=28,
                  aliases=["Garro", "Garro: Weapon of Fate"]),
                e("Garro: Vow of Faith", "Novella · Garro part 3", "KE IF", o=40, aliases=["Vow of Faith"])]},
            {"name": "Emperor's Children & Iron Warriors", "name_fr": "Emperor's Children et Iron Warriors", "color": "#d77fd2", "books": [
                e("Angel Exterminatus", _num(23), "EC IW SL", o=19), e("Tallarn", _num(45), "IW IF SH", o=35)]},
            {"name": "Salamanders & Iron Hands", "name_fr": "Salamanders et Iron Hands", "color": "#5cb874", "books": [
                e("Vulkan Lives", _num(26), "SA PE", o=24), e("Deathfire", _num(32), "SA", o=37),
                e("Old Earth", _num(47), "SA IH SH SL PE", o=49)]},
            {"name": "White Scars", "color": "#d07a7a", "books": [
                e("Scars", _num(28), "WS AL", o=26), e("The Path of Heaven", _num(36), "WS EC DG SH TS", o=41)]},
            {"name": "Space Wolves & Thousand Sons", "name_fr": "Space Wolves et Thousand Sons", "color": "#5b9bd5", "books": [
                e("Wolf King", "Novella", "SW DA", o=30), e("The Crimson King", _num(44), "TS EC KE", o=47)]},
            {"name": "Sons of Horus & Shattered Legions", "name_fr": "Sons of Horus et Légions Brisées", "color": "#6fb5a6", "books": [
                e("Vengeful Spirit", _num(29), "SH EC SL LD KE DG", o=31),
                e("The Damnation of Pythos", _num(30), "SL LD", o=33)]},
            {"name": "Imperial Fists", "color": "#d9c04a", "books": [
                e("Praetorian of Dorn", _num(39), "IF AL", o=43, aliases=["The Praetorian of Dorn"]),
                e("The Master of Mankind", _num(41), "IF IM", o=45)]},
            {"name": "Anthologies & collections", "name_fr": "Anthologies et recueils", "color": "#8f99a8",
             "hint": "Read each where it falls in the order", "hint_fr": "Chacune se lit à sa place dans l'ordre", "books": [
                e("Tales of Heresy", _num(10), "DA IM WB KE", o=4,
                  stories=["Call of the Lion", "Blood Games", "Scions of the Storm", "The Voice"]),
                e("Age of Darkness", _num(16), "DA NL IW SH SL TS RG AL", o=7,
                  stories=["Savage Weapons", "The Iron Within", "Little Horus", "Rebirth", "The Face of Treachery"]),
                e("The Primarchs", _num(20), "DA EC AL", o=14,
                  stories=["The Lion", "The Reflection Crack'd", "The Serpent Beneath"]),
                e("Shadows of Treachery", _num(22), "NL DA IF IW RG", o=17,
                  stories=["Prince of Crows", "The Crimson Fist", "Raven's Flight"]),
                e("Legacies of Betrayal (Part 1)", _num(31, "up to Kryptos"), "UM WE WB LD SL IH WS", o=18,
                  aliases=["Legacies of Betrayal"],
                  stories=["Honour to the Dead", "Butcher's Nails", "Serpent", "Veritas Ferrum", "Riven",
                           "Strike and Fade", "Kryptos", "Brotherhood of the Storm"]),
                e("War Without End: The Imperial Truth", _num(33, "part"), "IM DA LD", o=21, aliases=["War Without End"],
                  stories=["Hands of the Emperor", "The Phoenician", "By the Lion's Command", "The Divine Adoratrice",
                           "Lord of the Red Sands", "All That Remains"]),
                e("Mark of Calth", _num(25), "UM WB PE", o=22),
                e("Born of Flame", _num(50), "SA IH", o=23),
                e("Legacies of Betrayal (Part 2)", _num(31, "Wolf's Claw onwards"), "DA UM WB WE EC RG", o=27,
                  part_of="Legacies of Betrayal (Part 1)",
                  stories=["Guardian of Order", "Censure", "The Eightfold Path", "Heart of the Conqueror",
                           "Lucius, the Eternal Blade", "The Divine Word"]),
                e("The Silent War", _num(37), "KE IF IM", o=29),
                e("War Without End: Sedition's Gate", _num(33, "part"), "LD WS TS DA BA UM", o=32,
                  part_of="War Without End: The Imperial Truth",
                  stories=["Artefacts", "The Harrowing", "Allegiance", "The Laurel of Defiance", "Sermon of Exodus"]),
                e("War Without End: Death and Defiance + Blades of the Traitor", _num(33, "part"),
                  "EC UM NL BA IM LD IW SH KE PE", o=34, part_of="War Without End: The Imperial Truth",
                  stories=["Imperfect", "Howl of the Hearthworld", "A Safe and Shadowed Place", "Virtues of the Sons",
                           "Gunsight", "Black Oculus", "Daemonology", "Chirurgeon", "The Twisted", "Wolf Mother"]),
                e("Eye of Terra", _num(35), "DA NL BA UM WB IW IF WS", o=36,
                  stories=["Master of the First", "The Long Night", "Sins of the Father", "The Herald of Sanguinius",
                           "Red-Marked", "Inheritor", "Stratagem", "Ironfire", "The Eagle's Talon", "Iron Corpses",
                           "Brotherhood of the Moon"]),
                e("Shattered Legions", _num(43), "SL IH AL SH", o=39),
                e("The Burden of Loyalty", _num(48), "PE UM IM", o=46,
                  stories=["Perpetual", "The Heart of the Pharos", "Ordo Sinister", "The Binary Succession"])]},
        ]},
        {"kind": "chains", "name": "Act 3 · Prelude to the Siege", "color": "#d9b26f",
         "name_fr": "Acte 3 · Prélude au Siège",
         "hint": "Read these after all of Act 2", "hint_fr": "À lire après tout l'Acte 2",
         "exit": "The Buried Dagger", "rows": [
            {"seq": False, "books": [e("Heralds of the Siege", _num(52)), e("Titandeath", _num(53))]},
            {"books": [e("Wolfsbane", _num(49)), e("Slaves to Darkness", _num(51)), e("The Buried Dagger", _num(54))]},
            {"seq": False, "books": [e("Two Metaphysical Blades", "Short"), e("A Rose Watered with Blood", "Short")]},
            # Characters novels set as the Siege begins (not in the chart).
            {"seq": False, "books": [
                e("Sigismund: The Eternal Crusader", "Characters #3 · eve of the Siege", "IF", author="John French"),
                e("Eidolon: The Auric Hammer", "Characters #4 · eve of the Siege", "EC", author="Marc Collins")]},
        ]},
        {"kind": "solo", "name": "The Siege of Terra", "color": "#e2725b", "name_fr": "Le Siège de Terra",
         "hint": "Not in the chart: continues after The Buried Dagger",
         "hint_fr": "Absent du schéma : la suite après The Buried Dagger", "books": [
            # Novellas at Hardcover's half positions (not in the chart, which predates most of the Siege).
            e("The Solar War", f"{_SOT} #1"), e("The Lost and the Damned", f"{_SOT} #2"),
            e("The First Wall", f"{_SOT} #3"), e("Sons of the Selenar", f"{_SOT} #3.5 · Novella"),
            e("Saturnine", f"{_SOT} #4"), e("Fury of Magnus", f"{_SOT} #4.5 · Novella"), e("Mortis", f"{_SOT} #5"),
            e("Warhawk", f"{_SOT} #6"), e("Echoes of Eternity", f"{_SOT} #7"),
            e("Garro: Knight of Grey", f"{_SOT} #7.5 · Novella"),
            e("The End and the Death: Volume I", f"{_SOT} #8, vol. I",
              aliases=["The End and the Death, Vol. I", "The End and the Death Volume One", "The End and the Death Volume 1"]),
            e("The End and the Death: Volume II", f"{_SOT} #8, vol. II",
              aliases=["The End and the Death, Vol. II", "The End and the Death Volume Two", "The End and the Death Volume 2"]),
            e("The End and the Death: Volume III", f"{_SOT} #8, vol. III",
              aliases=["The End and the Death, Vol. III", "The End and the Death Volume Three", "The End and the Death Volume 3"]),
            e("Era of Ruin", f"{_SOT} #11")]},
        {"kind": "loose", "name": "Act 3 · Other stories", "color": "#8f99a8", "name_fr": "Acte 3 · Autres récits",
         "hint": "Written after their arcs ended: any order once Act 2 is done",
         "hint_fr": "Écrits après la fin de leur arc : dans n'importe quel ordre une fois l'Acte 2 terminé", "books": [
            e("Restorer", "Short"), e("Prologue to Nikaea", "Short"),
            e("Blackshields: The False War", "Audio drama · Blackshields 1/3", aliases=["The False War"]),
            e("Blackshields: The Red Fief", "Audio drama · Blackshields 2/3", aliases=["The Red Fief"]),
            e("Blackshields: The Broken Chain", "Audio drama · Blackshields 3/3", aliases=["The Broken Chain"]),
            e("Spear of Ultramar", "Novella"), e("Dreadwing", "Novella"), e("Old Wounds, New Scars", "Short"),
            e("Abyssal", "Short"), e("The Last Council", "Short"), e("Bringer of Sorrow", "Short"),
            e("Lantern's Light", "Short"), e("Hubris of Monarchia", "Audio drama"),
            e("Nightfane", "Audio drama", aliases=["Nightfane: The Horus Heresy Series"]),
            e("The Revelation of the Word", "Audio drama"), e("The Serpent's Dance", "Short"),
            e("The Lightning Hall", "Short"), e("Eater of Dreams", "Short"), e("Bloodhowl", "Short"),
            e("Amor Fati", "Short")]},
        {"kind": "loose", "name": "The Primarchs & Characters", "color": "#b48ad6",
         "name_fr": "Primarques et Personnages",
         "hint": "Mostly Great Crusade portraits: any order, any time after Act 1's first five",
         "hint_fr": "Surtout des portraits de la Grande Croisade : dans n'importe quel ordre, après les cinq premiers tomes",
         "books": [
            e("Valdor: Birth of the Imperium", "Characters #1 · Unification Wars prequel", "IM PE", author="Chris Wraight"),
            e("Scions of the Emperor", "Primarchs anthology", author="Chris Wraight"),
            _pr("Roboute Guilliman: Lord of Ultramar", 1, "UM", "David Annandale"),
            _pr("Leman Russ: The Great Wolf", 2, "SW", "Chris Wraight"),
            _pr("Magnus the Red: Master of Prospero", 3, "TS", "Graham McNeill"),
            _pr("Perturabo: Hammer of Olympia", 4, "IW", "Guy Haley"),
            _pr("Lorgar: Bearer of the Word", 5, "WB", "Gav Thorpe"),
            _pr("Fulgrim: The Palatine Phoenix", 6, "EC", "Joshua Reynolds"),
            _pr("Ferrus Manus: The Gorgon of Medusa", 7, "IH", "David Guymer"),
            _pr("Jaghatai Khan: Warhawk of Chogoris", 8, "WS", "Chris Wraight"),
            _pr("Vulkan: Lord of Drakes", 9, "SA", "David Annandale"),
            _pr("Corax: Lord of Shadows", 10, "RG", "Guy Haley"),
            _pr("Angron: Slave of Nuceria", 11, "WE", "Ian St. Martin"),
            _pr("Konrad Curze: The Night Haunter", 12, "NL", "Guy Haley"),
            _pr("Lion El'Jonson: Lord of the First", 13, "DA", "David Guymer"),
            _pr("Alpharius: Head of the Hydra", 14, "AL", "Mike Brooks"),
            _pr("Mortarion: The Pale King", 15, "DG", "David Annandale"),
            _pr("Rogal Dorn: The Emperor's Crusader", 16, "IF", "Gav Thorpe"),
            _pr("Sanguinius: The Great Angel", 17, "BA", "Chris Wraight"),
            e("Grandfather's Gift", "Short", author="Guy Haley"),
            e("Luther: First of the Fallen", "Characters #2 · best after Angels of Caliban", "DA", author="Gav Thorpe"),
            e("Zardu Layak: The Crimson Apostle", "Characters #5 · best after Betrayer", "WB", author="Rich McCormick")]},
    ],
    # Section refs are 0-based indexes into "sections"; book refs are titles.
    "links": [
        ["Fulgrim", "gate:1", "toGate"],
        ["gate:1", "colsfirst:2", "fromGate"],
        ["A Thousand Sons", "Prospero Burns", "elbow"],
        ["colslast:2", "gate:3", "toGate"],
        ["Heralds of the Siege", "The Buried Dagger", "elbow"],
        ["Titandeath", "The Buried Dagger", "elbow"],
        ["Wolfsbane", "Two Metaphysical Blades", "vdash"],
        ["Slaves to Darkness", "A Rose Watered with Blood", "vdash"],
        ["The Buried Dagger", "The Solar War", "elbow"],
        # not drawn, they decide "Up next"
        ["Fulgrim", "The Kaban Project", "logic"], ["Fulgrim", "A Thousand Sons", "logic"],
        ["Fulgrim", "colsfirst:2", "logic"],
        ["colslast:2", "Heralds of the Siege", "logic"], ["colslast:2", "Titandeath", "logic"],
        ["colslast:2", "Wolfsbane", "logic"],
        ["colslast:2", "Sigismund: The Eternal Crusader", "logic"], ["colslast:2", "Eidolon: The Auric Hammer", "logic"],
    ],
}

# --- Discworld -------------------------------------------------------------------------------------


def _dw(title, n):
    return e(title, f"Discworld #{n}", author="Terry Pratchett")


DISCWORLD = {
    "key": "discworld",
    "name": "Discworld",
    "name_fr": "Les Annales du Disque-monde",
    "description_fr": ("Les 41 romans par arc de personnages : Rincewind, la Mort, les Sorcières, Tiffany Patraque, "
                       "le Guet, Moite von Lipwig, plus les romans indépendants."),
    "description": "All 41 novels by character arc: Rincewind, Death, the Witches, Tiffany Aching, the City Watch, Moist von Lipwig, plus the standalones.",
    "match_series": ["discworld"],
    "author": "Terry Pratchett",
    "col": 150,
    "lines": {},
    "sections": [
        {"kind": "cols", "name": "Arcs", "groups": [
            {"name": "Rincewind & the Wizards", "name_fr": "Rincewind et les mages", "color": "#e08a4c", "books": [
                _dw("The Colour of Magic", 1), _dw("The Light Fantastic", 2), _dw("Sourcery", 5), _dw("Eric", 9),
                _dw("Interesting Times", 17), _dw("The Last Continent", 22), _dw("The Last Hero", 27),
                _dw("Unseen Academicals", 37)]},
            {"name": "Death", "name_fr": "La Mort", "color": "#c9c2b2", "books": [
                _dw("Mort", 4), _dw("Reaper Man", 11), _dw("Soul Music", 16), _dw("Hogfather", 20), _dw("Thief of Time", 26)]},
            {"name": "The Witches", "name_fr": "Les Sorcières", "color": "#a57fe0", "books": [
                _dw("Equal Rites", 3), _dw("Wyrd Sisters", 6), _dw("Witches Abroad", 12), _dw("Lords and Ladies", 14),
                _dw("Maskerade", 18), _dw("Carpe Jugulum", 23)]},
            {"name": "Tiffany Aching", "name_fr": "Tiffany Patraque", "color": "#6cc46c", "books": [
                _dw("The Wee Free Men", 30), _dw("A Hat Full of Sky", 32), _dw("Wintersmith", 35),
                _dw("I Shall Wear Midnight", 38), _dw("The Shepherd's Crown", 41)]},
            {"name": "The City Watch", "name_fr": "Le Guet", "color": "#5b9bd5", "books": [
                _dw("Guards! Guards!", 8), _dw("Men at Arms", 15), _dw("Feet of Clay", 19), _dw("Jingo", 21),
                _dw("The Fifth Elephant", 24), _dw("Night Watch", 29), _dw("Thud!", 34), _dw("Snuff", 39)]},
            {"name": "Moist von Lipwig", "name_fr": "Moite von Lipwig", "color": "#d9c04a", "books": [
                _dw("Going Postal", 33), _dw("Making Money", 36), _dw("Raising Steam", 40)]},
            {"name": "Standalones", "name_fr": "Romans indépendants", "color": "#7f8a99", "loose": True, "books": [
                _dw("Pyramids", 7), _dw("Moving Pictures", 10), _dw("Small Gods", 13), _dw("The Truth", 25),
                _dw("The Amazing Maurice and His Educated Rodents", 28), _dw("Monstrous Regiment", 31)]},
        ]},
    ],
    "links": [["Carpe Jugulum", "The Wee Free Men", "cross"]],
}


# --- Arisen + Raiders + Operators (lanes) --------------------------------------------------------------
# Michael Stephen Fuchs's own page says there's no strict chronology and recommends publication order;
# this chart instead lays the books out by when they happen, the way a re-read would go: Raiders (the
# JFK's first two years) and Operators (USOC's first two years, "from Hereford") run beside the main
# series before Fortress Britain, which they both lead into. Placements marked with a note are best
# guesses to confirm.

_MSF = "Michael Stephen Fuchs"


def _ar(title, lane, sub, **kw):
    return e(title, sub, author=_MSF, lane=lane, **kw)


ARISEN = {
    "key": "arisen",
    "name": "Arisen + Raiders + Operators",
    "name_fr": "Arisen + Raiders + Operators",
    "description": ("Lane chart: the main series in the middle, Raiders (the JFK) and Operators (Hereford) beside it "
                    "in story order, with the prequels where they're best read. 30 titles."),
    "description_fr": ("Carte en couloirs : la série principale au centre, Raiders (le JFK) et Operators (Hereford) à "
                       "côté dans l'ordre de l'histoire, les préquelles là où elles se lisent le mieux. 30 titres."),
    "match_series": ["arisen"],
    "author": _MSF,
    "col": 150,
    "lines": {},
    "sections": [
        {"kind": "lanes", "name": "Arisen", "name_fr": "Arisen",
         "lanes": [
             {"id": "raiders", "name": "Arisen: Raiders", "color": "#d4934a",
              "about": "The USS John F. Kennedy and its Marine Raiders, from the Collapse until they reach Britain.",
              "about_fr": "Le USS John F. Kennedy et ses Marine Raiders, de l'effondrement à leur arrivée en Grande-Bretagne."},
             {"id": "main", "name": "Arisen", "color": "#7fb6d8",
              "about": "Alpha team and Fortress Britain. Prequels sit where they're best read.",
              "about_fr": "L'équipe Alpha et la Forteresse Britannique. Les préquelles sont placées là où elles se lisent le mieux."},
             {"id": "operators", "name": "Arisen: Operators", "color": "#9a9ccf",
              "about": "USOC's own first two years of the Zulu Alpha, from Hereford.",
              "about_fr": "Les deux premières années du Zulu Alpha vues par l'USOC, depuis Hereford."},
         ],
         "eras": [
             {"from": "Genesis", "name": "Day 0", "sub": "The outbreak", "name_fr": "Jour 0", "sub_fr": "L'épidémie"},
             {"from": "The Collapse", "name": "Years 1–2", "sub": "The Zulu Alpha, before the JFK reaches Britain",
              "name_fr": "Années 1–2", "sub_fr": "Le Zulu Alpha, avant l'arrivée du JFK en Grande-Bretagne"},
             {"from": "Fortress Britain", "name": "Main series", "sub": "The JFK picks up Alpha team",
              "name_fr": "Série principale", "sub_fr": "Le JFK récupère l'équipe Alpha"},
             {"from": "Last Stand", "name": "The end", "sub": "Everything converges",
              "name_fr": "La fin", "sub_fr": "Tout converge"},
             {"from": "Fickisms", "name": "Companions", "sub": "Any time after Endgame",
              "name_fr": "Compagnons", "sub_fr": "À tout moment après Endgame"},
         ],
         "books": [
             _ar("Genesis", "main", "Prequel", style="prequel", aliases=["Arisen: Genesis", "Arisen : Genesis"]),
             _ar("The Collapse", "raiders", "Raiders #1"),
             _ar("The Fall of the Third Temple", "operators", "Operators #1"),
             _ar("Tribes", "raiders", "Raiders #2"),
             _ar("Pipe Hitters", "operators", "Operators #2", aliases=["Arisen: Operators, Volume II- Pipe Hitters"]),
             _ar("Dead Men Walking", "raiders", "Raiders #3"),
             _ar("Brothers", "operators", "Operators #3"),
             _ar("Duty", "raiders", "Raiders #4"),
             _ar("Stronghold", "operators", "Operators #4",
                 note="The author says Operators covers USOC's first two years; volumes IV and V might reach into the main series.",
                 note_fr="L'auteur dit qu'Operators couvre les deux premières années de l'USOC ; les tomes IV et V débordent peut-être sur la série principale."),
             _ar("The Last Alamo", "operators", "Operators #5",
                 note="Not out yet (pre-order). Placed as the end of the Operators arc, just before Book 1.",
                 note_fr="Pas encore sorti (précommande). Placé en fin d'arc Operators, juste avant le tome 1."),
             _ar("Fortress Britain", "main", "Book 1"),
             _ar("Mogadishu of the Dead", "main", "Book 2"),
             _ar("Three Parts Dead", "main", "Book 3"),
             _ar("Odyssey", "main", "Prequel · read here", style="prequel",
                 note="Placed after Book 3 as in your list. The author: it 'clears up a huge mystery of the main series'.",
                 note_fr="Placé après le tome 3 comme dans votre liste. L'auteur : il « éclaircit un grand mystère de la série principale »."),
             _ar("Maximum Violence", "main", "Book 4"),
             _ar("Exodus", "main", "Book 5"),
             _ar("The Horizon", "main", "Book 6"),
             _ar("Death of Empires", "main", "Book 7"),
             _ar("Empire of the Dead", "main", "Book 8"),
             _ar("Nemesis", "main", "Prequel · read here", style="prequel"),
             _ar("Cataclysm", "main", "Book 9"),
             _ar("The Flood", "main", "Book 10"),
             _ar("Deathmatch", "main", "Book 11"),
             _ar("Carnage", "main", "Book 12"),
             _ar("The Siege", "main", "Book 13"),
             _ar("Last Stand", "raiders", "Standalone", style="prequel",
                 note="The author: it 'adds backstory to the climax of the Raiders prequel series', so it sits just before The Last Raid.",
                 note_fr="L'auteur : il « ajoute du contexte au final de la série préquelle Raiders », d'où sa place juste avant The Last Raid."),
             _ar("The Last Raid", "raiders", "Raiders #5",
                 note="Placed alongside Endgame as you described. The author's blurb calls Raiders the two years before Book 1: check once read.",
                 note_fr="Placé à côté d'Endgame comme vous l'avez décrit. Le résumé de l'auteur situe Raiders dans les deux ans avant le tome 1 : à vérifier à la lecture."),
             _ar("Endgame", "main", "Book 14 · finale"),
             _ar("Fickisms", "main", "Companion", style="companion",
                 aliases=["Fickisms: The Complete Wit & Wisdom of Master Gunnery Sergeant Fick"]),
             _ar("Fickisms II: This Time It's Personal", "main", "Companion", style="companion"),
         ]},
    ],
    "links": [
        ["Genesis", "The Collapse", "fork"], ["Genesis", "The Fall of the Third Temple", "fork"],
        ["Duty", "Fortress Britain", "join"], ["The Last Alamo", "Fortress Britain", "join"],
        ["The Last Raid", "Endgame", "with"],
    ],
}

PRESETS = {p["key"]: p for p in (HORUS_HERESY, DISCWORLD, ARISEN)}


def preset_size(p) -> int:
    return sum(len(_section_entries(s)) for s in p["sections"])


def _section_groups(sec) -> list[dict]:
    """Every section is stored as one or more groups; non-column sections have exactly one."""
    if sec["kind"] == "cols":
        return sec["groups"]
    g = {k: sec.get(k) for k in ("name", "color", "hint", "name_fr", "hint_fr")}
    g["loose"] = sec["kind"] == "loose"
    g["rows"] = sec.get("rows") or [{"books": sec["books"]}]
    return [g]


def _section_entries(sec) -> list[dict]:
    if sec["kind"] == "lanes":
        return list(sec["books"])
    out = []
    for g in _section_groups(sec):
        for r in g.get("rows") or [{"books": g["books"]}]:
            out.extend(r["books"])
    return out


def guess_target(p) -> int | None:
    """The existing series that most likely is this preset's series: the shortest name containing the
    preset's main match word ("The Horus Heresy" rather than "The Horus Heresy: Primarchs")."""
    word = p["match_series"][0]
    hits = [s for s in books_db.all_series_names() if word in s["name"].lower()]
    return min(hits, key=lambda s: len(s["name"]))["id"] if hits else None


# --- applying --------------------------------------------------------------------------------------

def apply_preset(key: str, series_id: int | None, mark_unread: bool = True) -> tuple[int, dict]:
    """Build the preset's map on `series_id` (or a new series). Returns (series_id, stats)."""
    p = PRESETS[key]
    if series_id is None:
        series_id = books_db.create_series(p["name"], p.get("author", ""))
    target = books_db.get_series(series_id)
    if p.get("name_fr") and target is not None and not target["name_fr"]:
        books_db.set_series_name_fr(series_id, p["name_fr"])
    books_db.clear_map(series_id)
    books_db.set_map_meta(series_id, key, p.get("col") or 150)
    books_db.set_map_lines(series_id, p.get("lines") or {})

    index = books_db.preset_candidates(series_id, p["match_series"])  # norm key -> [rows]
    used: set[int] = set()
    by_title: dict[str, int] = {}
    stats = {"matched": 0, "moved": 0, "created": 0, "merged": 0, "folded": 0}
    related = books_db.related_series_ids(series_id, p["match_series"])
    part_links: list[tuple[int, str]] = []

    def find(ent):
        """The library's copy of an entry. With several copies of the same book (imported in two series),
        the one furthest along is kept and the others are merged into it."""
        hits, seen = [], set()
        for t in [ent["title"], *ent["aliases"]]:
            for k in books_db.title_keys(t):
                for row in index.get(k, []):
                    if row["id"] not in used and row["id"] not in seen:
                        seen.add(row["id"])
                        hits.append(row)
        if not hits:
            return None
        best = max(hits, key=lambda row: books_db.STATUS_RANK.get(row["status"], 0))  # first on ties
        for row in hits:
            if row["id"] != best["id"] and books_db.same_book(best, row):
                used.add(row["id"])
                books_db.merge_book_into(best["id"], row["id"])
                stats["merged"] += 1
        return best

    def take(ent) -> int:
        """The book for a preset entry: the library's copy (moved into the series) or a new manual book."""
        found = find(ent)
        if found:
            used.add(found["id"])
            stats["matched"] += 1
            if found["series_id"] != series_id:
                books_db.set_book_series_raw(found["id"], series_id)
                stats["moved"] += 1
            return found["id"]
        author = ent["author"] or p.get("author", "")
        book_id = books_db.add_book({"title": ent["title"], "author": author}, series_id, "", "want")
        stats["created"] += 1
        # Only books with an author can auto-link later (bare titles are too ambiguous), and split
        # parts share their first part's record instead.
        if author and not ent["part_of"]:
            books_db.queue_lookup(book_id)
        return book_id

    section_ids: list[int] = []
    order = 0
    for sec in p["sections"]:
        if sec["kind"] == "lanes":
            lane_ix = {ln["id"]: k for k, ln in enumerate(sec["lanes"])}
            entries = []
            for ent in sec["books"]:
                book_id = take(ent)
                by_title[ent["title"]] = book_id
                entries.append({"book_id": book_id, "lane": lane_ix[ent["lane"]], "sub": ent["sub"],
                                "sub_fr": ent["sub_fr"] or sub_fr(ent["sub"]), "note": ent["note"],
                                "note_fr": ent["note_fr"], "style": ent["style"]})
            eras = [{**era, "from": by_title[era["from"]]} for era in sec.get("eras", [])]
            section_ids.append(books_db.build_lanes_section(
                series_id, sec["name"], sec["lanes"], entries, [], eras, name_fr=sec.get("name_fr") or "",
                hint=sec.get("hint") or "", hint_fr=sec.get("hint_fr") or ""))
            continue
        sid = books_db.create_section(series_id, sec["kind"], sec["name"], sec.get("color") or "#8f99a8",
                                      sec.get("hint") or "", auto_wire=False,
                                      name_fr=sec.get("name_fr") or "", hint_fr=sec.get("hint_fr") or "")
        section_ids.append(sid)
        for g in _section_groups(sec):
            gid = books_db.create_group(sid, g["name"], g.get("color") or sec.get("color") or "#8f99a8",
                                        g.get("hint") or "", bool(g.get("loose")),
                                        name_fr=g.get("name_fr") or "", hint_fr=g.get("hint_fr") or "")
            for r_i, row in enumerate(g.get("rows") or [{"books": g["books"]}]):
                if row.get("seq") is False:
                    books_db.set_row_noseq(gid, r_i)
                for k, ent in enumerate(row["books"]):
                    book_id = take(ent)
                    if ent["part_of"]:
                        part_links.append((book_id, ent["part_of"]))
                    by_title[ent["title"]] = book_id
                    order += 1
                    books_db.add_entry(gid, book_id, row=r_i, indent=(row.get("indent", 0) if k == 0 else 0),
                                       sub=ent["sub"], lines=" ".join(ent["lines"]), stories="\n".join(ent["stories"]),
                                       list_order=ent["o"], sub_fr=sub_fr(ent["sub"]))
        if sec.get("exit"):
            books_db.set_section_exit(sid, by_title[sec["exit"]])

    for book_id, parent_title in part_links:
        parent = by_title.get(parent_title)
        if parent:
            books_db.copy_cover(parent, book_id)

    def ref(x: str) -> str:
        kind, _, rest = x.partition(":")
        if kind in ("gate", "colsfirst", "colslast") and rest.isdigit():
            return f"{kind}:{section_ids[int(rest)]}"
        return f"book:{by_title[x]}"

    for a, b, kind in p.get("links", []):
        books_db.add_link(series_id, ref(a), ref(b), kind)

    # Related series (e.g. "The Horus Heresy: Primarchs") are folded in: leftover copies of placed books
    # are merged, anything else moves in unplaced, and the old series keeps only its release watching.
    for rid in related:
        st = books_db.fold_series(rid, series_id)
        stats["moved"] += st["moved"]
        stats["merged"] += st["merged"]
        stats["folded"] += 1

    if mark_unread:
        books_db.mark_unread(list(by_title.values()))
    books_db.renumber_from_map(series_id)
    return series_id, stats
