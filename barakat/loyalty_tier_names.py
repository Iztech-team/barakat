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


# Frappe's `tier_name` field is a `Data` fieldtype, which MariaDB stores as
# varchar(140). This is not an arbitrary choice: it is the actual column
# width, and a rename that exceeds it either raises `Data too long` (strict
# sql_mode, aborting `bench migrate` for the whole site with the patch left
# unrecorded) or gets silently truncated back to the original duplicate
# (non-strict sql_mode, so the patch never converges). Python's `len()` and
# slicing operate on characters, matching how MariaDB counts a varchar's
# length, so no extra care is needed here beyond respecting the constant.
TIER_NAME_MAX_LENGTH = 140


def _capped_rename(base, counter):
    """`<base> (<counter>)`, truncating `base` so the result never exceeds
    TIER_NAME_MAX_LENGTH characters.

    The suffix widens as `counter` reaches double, then triple digits, so the
    truncation point is recomputed for every counter value rather than fixed
    once.
    """
    suffix = f" ({counter})"
    base_limit = max(TIER_NAME_MAX_LENGTH - len(suffix), 0)
    return f"{base[:base_limit]}{suffix}"


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
    Every candidate is capped at TIER_NAME_MAX_LENGTH characters (truncating
    `name`, never skipping the row) so the rename can never overflow the
    `tier_name` column.

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
        candidate = _capped_rename(name, counter)
        while candidate in taken:
            counter += 1
            candidate = _capped_rename(name, counter)
        taken.add(candidate)
        seen.add(candidate)
        renames[index] = candidate
    return renames
