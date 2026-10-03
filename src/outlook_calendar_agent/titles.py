"""Deterministic clean-up of event titles and locations.

A small local model tends to paste the user's whole phrase into the subject ("Another meeting
Shaw centre 4pm-6pm"). This module turns that into a tidy title and, where it is unambiguous,
moves the place into the location field. It never invents anything: every word in the result
came from the input.

The rules are deliberately conservative. A title that is left slightly untidy is harmless (the
user sees it in the preview); a title that loses a real word ("Black Friday sale", "Book club",
"Call with Wed Lee") is not. When a rule is in doubt it leaves the text alone.
"""

from __future__ import annotations

import re

# Longer subjects are returned as typed; nobody dictates a 200 character title by accident.
MAX_CLEAN_CHARS = 200

# ---- leading conversation glue --------------------------------------------------------------
# "please book another ...", "and a ...". Articles only count when followed by a space, so
# "A&E" and "A-level" survive; verbs only count before an article, so "Book club" survives.
_LEADING_GLUE_RE = re.compile(
    r"^(?:(?:please|pls)\s+)?"
    r"(?:(?:book|schedule|add|create|set\s+up|arrange|make)\s+(?:me\s+)?"
    r"(?=(?:a|an|another|one\s+more)\s))?"
    r"(?:(?:another|one\s+more|also|and|then|a|an)\s+)*",
    re.IGNORECASE,
)

# ---- times and durations --------------------------------------------------------------------
_CLOCK = r"\d{1,2}(?:[:.]\d{2})?"
_TIME_LEAD = r"(?:\b(?:at|from|by|around|until|till)\s+|@\s*)"
_RANGE_SEP = r"\s*(?:-|–|to|until|till)\s*"
# Anything with am/pm is unmistakably a time: "4pm", "4-6pm", "10.30am to 12pm", "at 3 pm".
_AMPM_TIME_RE = re.compile(
    rf"{_TIME_LEAD}?\b{_CLOCK}\s*(?:am|pm)?{_RANGE_SEP}{_CLOCK}\s*(?:am|pm)\b"
    rf"|{_TIME_LEAD}?\b{_CLOCK}\s*(?:am|pm)\b",
    re.IGNORECASE,
)
# A bare "14:30" is only a time after a preposition or at either end ("Chapter 3:16" stays).
_HHMM = rf"\d{{1,2}}:\d{{2}}(?:{_RANGE_SEP}\d{{1,2}}:\d{{2}})?"
_CLOCK24_RE = re.compile(
    rf"{_TIME_LEAD}{_HHMM}\b|^{_HHMM}\b|\b{_HHMM}\s*$",
    re.IGNORECASE,
)
# "noon"/"midnight" only after a preposition or at the end ("Midnight mass" stays).
_NAMED_TIME_RE = re.compile(
    r"\b(?:at|from|by|around|until|till)\s+(?:noon|midnight)\b|\b(?:noon|midnight)\s*$",
    re.IGNORECASE,
)
_UNITS = r"(?:minutes?|mins?|hours?|hrs?)"
# "for 2 hours" anywhere, "45 minute"/"2-hour" (adjective form) anywhere, "... 2 hours" at the
# end unless it is "in 5 mins". "24 Hours of Le Mans" stays.
_DURATION_RE = re.compile(
    rf"\bfor\s+\d+(?:\.\d+)?\s*{_UNITS}\b"
    r"|\b\d+(?:\.\d+)?[\s-]*(?:minute|min|hour|hr)\b"
    rf"|(?<!\bin\s)\b\d+(?:\.\d+)?\s*{_UNITS}\s*$",
    re.IGNORECASE,
)

# ---- days -----------------------------------------------------------------------------------
_FULL_DAY = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_SHORT_DAY = r"mon|tues?|wed|thur?s?|fri|sat|sun"
_DAY_RE = re.compile(
    rf"(?:\bon\s+)?(?:\b(?:next|this|coming)\s+)?\b(?:{_FULL_DAY}|today|tomorrow|tonight|tmrw?)\b"
    r"(?:\s+(?:morning|afternoon|evening|night))?",
    re.IGNORECASE,
)
# Words that make the following weekday part of a name: "Black Friday", "Every Monday".
_NAMED_DAY_PREFIXES = frozenset(
    [
        "black",
        "good",
        "cyber",
        "ash",
        "easter",
        "palm",
        "super",
        "holy",
        "maundy",
        "shrove",
        "fat",
        "boxing",
        "casual",
        "every",
        "each",
    ]
)
# Short forms collide with names and words ("Sun Wei", "SAT prep", "Wed Lee"), so they are only
# removed after next/this/coming, or in lowercase after "on" or at either end of the title.
_SHORT_DAY_PREFIXED_RE = re.compile(
    rf"(?:\bon\s+)?\b(?:next|this|coming)\s+(?:{_SHORT_DAY})\b", re.IGNORECASE
)
_SHORT_DAY_LOWER_RE = re.compile(
    rf"\b[Oo]n\s+(?:{_SHORT_DAY})\b|^(?:{_SHORT_DAY})\s+|\s+(?:{_SHORT_DAY})$"
)

# ---- places ---------------------------------------------------------------------------------
# Event words that can stand alone as a title next to a place ("Lunch", "Meeting").
_KINDS = (
    "lunch|dinner|breakfast|brunch|supper|coffee|tea|drinks|meeting|appointment|interview|gym|"
    "workout|class|lesson|seminar|briefing|workshop|training|haircut|movie|concert|party"
)
# Further event words that make "X at Y" a title and a place.
_AT_KINDS = _KINDS + "|call|review|sync|standup|stand-up|catch-up|catchup|demo|presentation|event"
# Words that end a place name. Kept to words that are rarely anything else.
_PLACE_ENDINGS = (
    "centre|center|house|mall|tower|towers|plaza|building|office|cafe|café|restaurant|hotel|"
    "park|road|street|avenue|hall|station|airport|clinic|hospital|school|university|campus|"
    "mrt|hq|gardens|square|hub|lobby|stadium|zoo|museum|library|church|temple|mosque|terminal|"
    "market|kopitiam|studio|lounge|theatre|theater|arena|sands"
)
_PLACE_HINTS = _PLACE_ENDINGS + "|level|room|floor|bar|court|point|club|city|block|lab|bay|garden"
_KIND_PLACE_RE = re.compile(rf"^(?P<kind>{_KINDS})\s+(?P<place>.+)$", re.IGNORECASE)
_AT_SPLIT_RE = re.compile(r"^(?P<title>.+)\s+(?:at\s+|@\s*)(?P<place>.+)$", re.IGNORECASE)
_HAS_AT_KIND_RE = re.compile(rf"\b(?:{_AT_KINDS})\b", re.IGNORECASE)
_HAS_PLACE_HINT_RE = re.compile(rf"\b(?:{_PLACE_HINTS})\b", re.IGNORECASE)
_ENDS_WITH_PLACE_RE = re.compile(rf"\b(?:{_PLACE_ENDINGS})$", re.IGNORECASE)
# A "place" containing one of these is really more title ("his office about the lease").
_NOT_PLACE_RE = re.compile(r"\b(?:with|about|re|for|regarding|on|to|and|length)\b", re.IGNORECASE)
_MAX_PLACE_WORDS = 6


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;:-–@")


def _capitalise_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _title_place(text: str) -> str:
    """Capitalise every all-lowercase word of a place; leave other words as the user typed."""
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in text.split(" "))


def _drop_day(match: re.Match[str]) -> str:
    before = match.string[: match.start()].split()
    if before and before[-1].lower() in _NAMED_DAY_PREFIXES:
        return match.group(0)
    return " "


def _strip_when(title: str) -> str:
    """Remove times, durations and day words that leaked into the title."""
    for pattern in (_AMPM_TIME_RE, _CLOCK24_RE, _NAMED_TIME_RE, _DURATION_RE):
        title = _tidy(pattern.sub(" ", title))
    title = _tidy(_DAY_RE.sub(_drop_day, title))
    title = _tidy(_SHORT_DAY_PREFIXED_RE.sub(" ", title))
    return _tidy(_SHORT_DAY_LOWER_RE.sub(" ", title))


def _looks_like_place(place: str) -> bool:
    return (
        bool(place)
        and len(place.split()) <= _MAX_PLACE_WORDS
        and not _NOT_PLACE_RE.search(place)
        and not re.fullmatch(r"[\d\s:.,-]+", place)
    )


def _split_place(title: str) -> tuple[str, str | None]:
    """Move an obvious place out of the title: "Dinner at Shaw House", "lunch shaw centre"."""
    match = _AT_SPLIT_RE.match(title)
    if match:
        head, place = _tidy(match.group("title")), _tidy(match.group("place"))
        named = _HAS_AT_KIND_RE.search(head) or _HAS_PLACE_HINT_RE.search(place)
        if head and named and _looks_like_place(place):
            return head, place
        return title, None
    match = _KIND_PLACE_RE.match(title)
    if match:
        place = _tidy(match.group("place"))
        words = place.split()
        if 2 <= len(words) <= 4 and _looks_like_place(place) and _ENDS_WITH_PLACE_RE.search(place):
            return _tidy(match.group("kind")), place
    return title, None


def clean_subject_and_location(subject: str, location: str | None = None) -> tuple[str, str | None]:
    """Return ``(title, location)`` with glue words, dates and times removed from the title.

    If no location was given and the title clearly contains one ("lunch shaw centre",
    "Sync at Level 3"), it is moved to the location. The title may come back empty only when
    the subject itself had no usable text.
    """
    raw = re.sub(r"\s+", " ", subject).strip()
    place = _tidy(location or "") or None
    shown_place = _title_place(place) if place else None
    if len(raw) > MAX_CLEAN_CHARS:
        return raw, shown_place
    original = _tidy(raw)

    # 1. dates, times and durations that leaked into the title
    title = _strip_when(original) or original

    # 2. leading glue words ("Another meeting ..." -> "meeting ..."), never down to nothing
    title = _tidy(_LEADING_GLUE_RE.sub("", title, count=1)) or title

    if place:
        # 3. a location already supplied must not be repeated: "... at Shaw Centre" anywhere,
        #    or the bare place at the end. Whole words only ("Shawn birthday" keeps its name).
        escaped = re.escape(place)
        without = re.sub(
            rf"(?:\b(?:at|in)\s+|@\s*){escaped}(?!\w)|(?<!\w){escaped}\s*$",
            " ",
            title,
            flags=re.IGNORECASE,
        )
        title = _tidy(without) or title
    else:
        # 4. move an obvious place out of the title
        title, place = _split_place(title)
        shown_place = _title_place(place) if place else None

    return _capitalise_first(title or original or raw), shown_place


def format_duration(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minutes")
    return " ".join(parts) or "0 minutes"
