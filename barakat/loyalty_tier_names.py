"""Pure tier-name rules for Loyalty Program collection rules — no Frappe imports.

Two different comparisons, deliberately.

`normalize_tier_name` (strip + casefold) is the SAVE-TIME rule. It rejects `VIP`
sitting next to `vip ` — names every human reads as one tier — at the moment
someone is typing them.

Exact string equality is the REPAIR rule, used by the patch and mirrored by the
POS. Only an exact repeat actually breaks anything: the POS keys its local tier
table on (site_url, program, tier_name). Repair paths run unattended against live
configuration, so they touch only rows that genuinely break something, and never
rename a tier on a program that works.
"""


def normalize_tier_name(name):
    """Save-time comparison key: surrounding space and case are noise."""
    return (name or "").strip().casefold()


def first_duplicate_tier_name(names):
    """First name whose normalized key already appeared, else None.

    Returns the ORIGINAL string so the error message quotes what the user typed
    rather than the folded key. Blank names are skipped — the child doctype marks
    `tier_name` as `reqd`, so Frappe already rejects those, and flagging them here
    would put two errors on one row.
    """
    seen = set()
    for name in names:
        key = normalize_tier_name(name)
        if not key:
            continue
        if key in seen:
            return name
        seen.add(key)
    return None


def resolve_exact_duplicates(names):
    """Map index -> new name for every exact repeat. Untouched rows are absent.

    Keeps the first occurrence and renames each later one to `<name> (2)`, `(3)`,
    ... incrementing until the candidate matches no other name in the program.

    `taken` is seeded with EVERY name up front, not filled in as we go. A name
    further down the list can already be `a (2)`, and handing that string to an
    earlier row would create the very collision we are removing.
    """
    taken = set(names)
    seen = set()
    renames = {}
    for index, name in enumerate(names):
        if name not in seen:
            seen.add(name)
            continue
        counter = 2
        while f"{name} ({counter})" in taken:
            counter += 1
        renamed = f"{name} ({counter})"
        taken.add(renamed)
        seen.add(renamed)
        renames[index] = renamed
    return renames
