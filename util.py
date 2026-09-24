import re

def apply_corrections(text, corrections, flags=re.IGNORECASE):
    """Replace each (bad, good) pair in text, matching on word boundaries.

    bad is treated as literal text, not a regex.  A \\b anchor only matches
    next to a word character, so it is only added on sides of the phrase
    that start/end with one (e.g. "C++" gets a leading anchor only).
    """
    for (bad, good) in corrections:
        if not bad:
            continue
        prefix = r"\b" if re.match(r"\w", bad[0]) else ""
        suffix = r"\b" if re.match(r"\w", bad[-1]) else ""
        text = re.sub(prefix + re.escape(bad) + suffix, good, text, flags=flags)

    return text

def pick_winner(addressed, last_addressed):
    """Choose which (character, context) entry replies when several are addressed.

    Prefers the previous winner only if it is among the currently addressed,
    and always with the current message's context.  Falls back to the first
    addressed entry.
    """
    if len(addressed) > 1 and last_addressed:
        last_winner = last_addressed[0]
        for ch, context in addressed:
            if ch is last_winner:
                return ch, context

    return addressed[0]
