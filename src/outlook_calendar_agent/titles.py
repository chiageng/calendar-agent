"""Deterministic clean-up of event titles and locations.

A small local model tends to paste the user's whole phrase into the subject ("Another meeting
Shaw centre 4pm-6pm"). This module turns that into a tidy title and, where it is unambiguous,
moves the place into the location field. It never invents anything: every word in the result
came from the input.
"""

from __future__ import annotations

import re

# Leading words that are conversation glue, not part of a title.
_FILLERS = (
    r"(?:another|one more|also|and|then|plus|a|an|the|new|my|please|pls|book|schedule|add|"
    r"create|draft)"
)
_LEADING_FILLER_RE = re.compile(rf"^(?:{_FILLERS})\b[\s,:-]*", re.IGNORECASE)

_WEEKDAY = (
    r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|"
    r"sat(?:urday)?|sun(?:day)?"
)
_DAY_RE = re.compile(
    rf"\b(?:(?:next|this|coming|on)\s+)?(?:{_WEEKDAY})\b|\b(?:today|tomorrow|tmr|tmrw|tonight)\b",
    re.IGNORECASE,
)
_TIME = r"\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}"
_TIME_RE = re.compile(
    rf"\b(?:from\s+)?(?:{_TIME})(?:\s*(?:-|–|to|until|till)\s*(?:{_TIME}))?\b|\b(?:noon|midnight)\b",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"\b(?:for\s+)?\d+(?:\.\d+)?\s*(?:minutes?|mins?|hours?|hrs?|h)\b", re.IGNORECASE
)

# Event words that can stand alone as a title ("Lunch", "Meeting").
_KINDS = (
    "lunch|dinner|breakfast|brunch|supper|coffee|tea|drinks|meeting|call|appointment|interview|"
    "review|sync|standup|stand-up|catch-up|catchup|gym|workout|class|lesson|seminar|briefing|"
    "demo|presentation|workshop|training|checkup|check-up|haircut|movie|concert|party|event"
)
# Words that mark the remainder as a place rather than more title.
_PLACE_WORDS = (
    "centre|center|house|mall|tower|towers|plaza|building|office|cafe|café|restaurant|hotel|"
    "park|road|rd|street|st|avenue|ave|level|room|hall|club|bar|station|airport|clinic|"
    "hospital|school|university|campus|mrt|hq|bay|garden|gardens|square|point|city|hub|block|"
    "blk|lobby|casino|sands|stadium|zoo|museum|library|church|temple|mosque|court|terminal|"
    "market|food court|hawker|kopitiam|studio|lounge|suite|floor|wing|lab|arena|theatre|theater"
)
_KIND_PLACE_RE = re.compile(
    rf"^(?P<kind>{_KINDS})\b\s*(?:at|@|in)?\s+(?P<place>.*\b(?:{_PLACE_WORDS})\b.*)$", re.IGNORECASE
)
_AT_SPLIT_RE = re.compile(r"^(?P<title>.+?)\s+(?:at|@)\s+(?P<place>.+)$", re.IGNORECASE)
_NOT_A_PLACE_START = re.compile(r"^(?:with|about|re|for|on|regarding)\b", re.IGNORECASE)


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;:-–@")


def _capitalise_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _title_place(text: str) -> str:
    """Capitalise every all-lowercase word of a place; leave other words as the user typed."""
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in text.split(" "))


def clean_subject_and_location(subject: str, location: str | None = None) -> tuple[str, str | None]:
    """Return ``(title, location)`` with glue words, dates and times removed from the title.

    If no location was given and the title clearly contains one ("lunch shaw centre",
    "Sync at Level 3"), it is moved to the location.
    """
    original = _tidy(subject)
    place = _tidy(location or "") or None
    title = original

    # 1. strip dates, times and durations that leaked into the title
    for pattern in (_TIME_RE, _DURATION_RE, _DAY_RE):
        title = pattern.sub(" ", title)
    title = _tidy(title)

    # 2. strip leading glue words ("Another meeting ..." -> "meeting ...")
    previous = None
    while previous != title:
        previous = title
        stripped = _LEADING_FILLER_RE.sub("", title)
        if stripped:  # never strip the title down to nothing
            title = _tidy(stripped)

    # 3. a location already supplied must not be repeated in the title
    if place:
        without = re.sub(
            rf"(?:\b(?:at|@|in)\s+)?{re.escape(place)}", " ", title, flags=re.IGNORECASE
        )
        if _tidy(without):
            title = _tidy(without)
    else:
        # 4. move an obvious place out of the title
        match = _AT_SPLIT_RE.match(title)
        if match and not _NOT_A_PLACE_START.match(match.group("place")):
            title, place = _tidy(match.group("title")), _tidy(match.group("place"))
        else:
            match = _KIND_PLACE_RE.match(title)
            if match and not _NOT_A_PLACE_START.match(match.group("place")):
                title, place = _tidy(match.group("kind")), _tidy(match.group("place"))

    title = _capitalise_first(title or original)
    return title, (_title_place(place) if place else None)


def format_duration(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minutes")
    return " ".join(parts) or "0 minutes"
