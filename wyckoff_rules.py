"""Wyckoff labels, phase texts and confirm/invalidate guidance (shared by wyckoff.py)."""
from __future__ import annotations

# internal key → (code, readable name); distribution is the price-mirrored accumulation.
ACC_LABELS = {
    "PS": ("PS", "Preliminary support"), "SC": ("SC", "Selling climax"),
    "AR": ("AR", "Automatic rally"), "ST": ("ST", "Secondary test"),
    "Spring": ("Spring", "Spring (shakeout below support)"), "Test": ("Test", "Test of the spring"),
    "SOS": ("SOS", "Sign of strength"), "LPS": ("LPS", "Last point of support / back-up (BU)"),
    "E": ("Markup", "Phase E: markup out of the range"),
}
DIST_LABELS = {
    "PS": ("PSY", "Preliminary supply"), "SC": ("BC", "Buying climax"),
    "AR": ("AR", "Automatic reaction"), "ST": ("ST", "Secondary test"),
    "Spring": ("UTAD", "Upthrust after distribution"), "Test": ("Test", "Test of the UTAD"),
    "SOS": ("SOW", "Sign of weakness"), "LPS": ("LPSY", "Last point of supply"),
    "E": ("Markdown", "Phase E: markdown out of the range"),
}
WORDS = {
    False: {"lo": "low", "hi": "high", "above": "above", "below": "below", "lowest": "lowest",
            "highest": "highest", "decline": "decline", "rally": "rally", "higher": "higher",
            "support": "support", "creek": "creek (AR high)", "breakdown": "breakdown",
            "breakout": "breakout", "spring": "spring", "sos": "SOS", "lps": "LPS",
            "beyond": "above"},
    True: {"lo": "high", "hi": "low", "above": "below", "below": "above", "lowest": "highest",
           "highest": "lowest", "decline": "advance", "rally": "reaction", "higher": "lower",
           "support": "resistance", "creek": "ice (AR low)", "breakdown": "breakout",
           "breakout": "breakdown", "spring": "UTAD", "sos": "SOW", "lps": "LPSY",
           "beyond": "below"},
}
PHASES = {
    "accumulation": {
        "A": "Phase A — stopping the decline (SC, AR; ST pending)",
        "B": "Phase B — building cause inside the range",
        "C": "Phase C — Spring tested the lows; supply looks exhausted",
        "D": "Phase D — sign of strength above the creek; LPS/back-up",
        "E": "Phase E — markup out of the range",
    },
    "distribution": {
        "A": "Phase A — stopping the advance (BC, AR; ST pending)",
        "B": "Phase B — building cause inside the range",
        "C": "Phase C — UTAD tested the highs; demand looks exhausted",
        "D": "Phase D — sign of weakness below the ice; LPSY",
        "E": "Phase E — markdown out of the range",
    },
}


def phase_label(schematic, phase):
    return PHASES.get(schematic, {}).get(phase)


def _fmt(x):
    return "—" if x is None else f"{x:.6g}"


def guidance(block):
    """(confirm, invalidate) sentences for a described structure (None when unclear)."""
    tr, phase, schematic = block.get("tr") or {}, block.get("phase"), block.get("schematic")
    if block.get("status") != "structure" or not tr:
        return None, None
    sup, res = tr.get("support"), tr.get("resistance")
    acc = schematic == "accumulation"
    edge_out, edge_in = (res, sup) if acc else (sup, res)
    word_out = "above the creek" if acc else "below the ice"
    event = "Spring" if acc else "UTAD"
    sign = "SOS" if acc else "SOW"
    if res is None or sup is None or not tr.get("height"):
        return ("an automatic " + ("rally" if acc else "reaction") + " that tops out and gives back "
                "35% of its move defines the range", f"a close {'below' if acc else 'above'} the "
                f"climax extreme {_fmt(sup if acc else res)} without a range")  # noqa: E501
    confirm = {
        "A": f"a secondary test holding {'above' if acc else 'below'} {_fmt(edge_in)} on lower volume",
        "B": f"a {event} beyond {_fmt(edge_in)} that closes back inside within 2 bars, or a {sign} "
             f"close {word_out} {_fmt(edge_out)}",
        "C": f"a {sign}: close {word_out} {_fmt(edge_out)} on rising volume",
        "D": f"the {'LPS' if acc else 'LPSY'} holding {_fmt(edge_out)} and a close one TR height "
             f"beyond it ({_fmt(edge_out + (tr['height'] or 0) * (1 if acc else -1))})",
        "E": "trend continuation; the range is now history (a new range or climax starts a new count)",
    }.get(phase)
    if phase in ("D", "E"):
        mid = (sup + res) / 2
        invalidate = f"a close back {'below' if acc else 'above'} the range midpoint {_fmt(mid)}"
    else:
        invalidate = (f"closes {'below' if acc else 'above'} {_fmt(edge_in)} that do not reclaim it "
                      f"(3 bars, or more than max(0.3·height, 1 ATR) beyond)")
    return confirm, invalidate
