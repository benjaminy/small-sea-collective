"""Micro tests for the three-way event merge."""

import pathlib

import pytest

from tide_table.merge import Conflict, Merged, Unsupported, merge_event

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _fixture(case, name):
    return (FIXTURES / case / name).read_bytes()


def _event_lines(merged):
    """Unfolded VEVENT property lines of a merged object."""
    text = merged.text.decode().replace("\r\n ", "")
    body = text.split("BEGIN:VEVENT\r\n", 1)[1].split("END:VEVENT", 1)[0]
    return [l for l in body.split("\r\n") if l]


def _prop(merged, name):
    return [l for l in _event_lines(merged) if l.split(";")[0].split(":")[0] == name]


def _cal(*event_lines, tz=False):
    zone = ["BEGIN:VTIMEZONE", "TZID:Test/Zone", "END:VTIMEZONE"] if tz else []
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:test", *zone,
             "BEGIN:VEVENT", "UID:u1", *event_lines, "END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode()


BASE_LINES = ["DTSTAMP:20261118T100000Z", "DTSTART:20261119T130000Z", "DTEND:20261119T140000Z",
              "SUMMARY:dinner"]


# --- the recorded Apple Calendar / Thunderbird run ---------------------------

@pytest.mark.parametrize("ours_name,theirs_name", [
    ("apple-move.ics", "thunderbird-location.ics"),
    ("thunderbird-location.ics", "apple-move.ics"),
])
def test_recorded_move_and_location_merge(ours_name, theirs_name):
    case = "move-vs-location"
    result = merge_event(_fixture(case, "base.ics"), _fixture(case, ours_name), _fixture(case, theirs_name))
    assert isinstance(result, Merged)
    assert _prop(result, "DTSTART") == ["DTSTART;TZID=America/Denver:20261119T151500"]
    assert _prop(result, "DTEND") == ["DTEND;TZID=America/Denver:20261119T161500"]
    assert _prop(result, "LOCATION") == ["LOCATION:over theere"]
    assert _prop(result, "SUMMARY") == ["SUMMARY:bloop"]
    # Both sides set SEQUENCE:1 independently; the merge is a newer revision.
    assert _prop(result, "SEQUENCE") == ["SEQUENCE:2"]
    assert _prop(result, "LAST-MODIFIED") == ["LAST-MODIFIED:20261118T192728Z"]
    assert _prop(result, "X-MOZ-GENERATION") == ["X-MOZ-GENERATION:1"]
    text = result.text.decode()
    assert text.count("BEGIN:VTIMEZONE") == 1
    assert "TZID:America/Denver" in text
    # The merged object is itself mergeable, so it parses cleanly.
    assert isinstance(merge_event(result.text, result.text, result.text), Merged)


def test_merged_calendar_header_and_zone_come_from_ours():
    case = "move-vs-location"
    result = merge_event(_fixture(case, "base.ics"), _fixture(case, "apple-move.ics"),
                         _fixture(case, "thunderbird-location.ics"))
    text = result.text.decode()
    assert "PRODID:-//Apple Inc." in text
    assert "Mozilla" not in text
    assert "18831118" not in text  # Thunderbird's long zone history is not carried along


# --- field rules ----------------------------------------------------------------

def test_same_field_changed_differently_conflicts():
    base = _cal(*BASE_LINES)
    ours = _cal(*BASE_LINES, "LOCATION:kitchen")
    theirs = _cal(*BASE_LINES, "LOCATION:garden")
    assert merge_event(base, ours, theirs) == Conflict(("LOCATION",))


def test_same_change_on_both_sides_is_not_a_conflict():
    base = _cal(*BASE_LINES)
    both = _cal(*BASE_LINES, "LOCATION:kitchen")
    result = merge_event(base, both, both)
    assert _prop(result, "LOCATION") == ["LOCATION:kitchen"]
    assert _prop(result, "SEQUENCE") == []


def test_start_and_end_changed_on_different_sides_conflict_as_time():
    base = _cal(*BASE_LINES)
    ours = _cal("DTSTAMP:20261118T100000Z", "DTSTART:20261119T120000Z", "DTEND:20261119T140000Z", "SUMMARY:dinner")
    theirs = _cal("DTSTAMP:20261118T100000Z", "DTSTART:20261119T130000Z", "DTEND:20261119T150000Z", "SUMMARY:dinner")
    assert merge_event(base, ours, theirs) == Conflict(("time",))


def test_deletion_on_one_side_wins_over_no_change():
    base = _cal(*BASE_LINES, "LOCATION:kitchen")
    ours = _cal(*BASE_LINES, "LOCATION:kitchen", "DESCRIPTION:bring bread")
    theirs = _cal(*BASE_LINES)
    result = merge_event(base, ours, theirs)
    assert _prop(result, "LOCATION") == []
    assert _prop(result, "DESCRIPTION") == ["DESCRIPTION:bring bread"]


def test_deletion_against_change_conflicts():
    base = _cal(*BASE_LINES, "LOCATION:kitchen")
    ours = _cal(*BASE_LINES, "LOCATION:garden")
    theirs = _cal(*BASE_LINES)
    assert merge_event(base, ours, theirs) == Conflict(("LOCATION",))


def test_unknown_properties_and_alarms_survive():
    alarm = ["BEGIN:VALARM", "ACTION:NONE", "TRIGGER;VALUE=DATE-TIME:19760401T005545Z", "END:VALARM"]
    extra = ["X-APPLE-CREATOR-IDENTITY:com.apple.calendar"]
    base = _cal(*BASE_LINES, *extra, *alarm)
    ours = _cal(*BASE_LINES[:3], "SUMMARY:dinner party", *extra, *alarm)
    theirs = _cal(*BASE_LINES, "LOCATION:kitchen", *extra, *alarm)
    result = merge_event(base, ours, theirs)
    assert _prop(result, "X-APPLE-CREATOR-IDENTITY") == extra
    assert result.text.decode().count("BEGIN:VALARM") == 1
    assert _prop(result, "SUMMARY") == ["SUMMARY:dinner party"]
    assert _prop(result, "LOCATION") == ["LOCATION:kitchen"]


def test_folded_lines_keep_their_folding():
    long_line = "DESCRIPTION:" + "x" * 70 + "\r\n " + "y" * 20
    base = _cal(*BASE_LINES)
    ours = _cal(*BASE_LINES, long_line)
    theirs = _cal(*BASE_LINES[:3], "SUMMARY:supper")
    result = merge_event(base, ours, theirs)
    assert long_line.encode() in result.text


def test_bookkeeping_takes_latest_and_bumps_sequence():
    base = _cal("DTSTAMP:20261118T100000Z", "SEQUENCE:3", "SUMMARY:dinner")
    ours = _cal("DTSTAMP:20261118T110000Z", "SEQUENCE:4", "SUMMARY:supper")
    theirs = _cal("DTSTAMP:20261118T120000Z", "SEQUENCE:5", "SUMMARY:dinner", "LOCATION:kitchen")
    result = merge_event(base, ours, theirs)
    assert _prop(result, "DTSTAMP") == ["DTSTAMP:20261118T120000Z"]
    assert _prop(result, "SEQUENCE") == ["SEQUENCE:6"]


def test_one_sided_edit_keeps_that_sides_sequence():
    base = _cal("DTSTAMP:20261118T100000Z", "SEQUENCE:3", "SUMMARY:dinner")
    theirs = _cal("DTSTAMP:20261118T120000Z", "SEQUENCE:4", "SUMMARY:supper")
    result = merge_event(base, base, theirs)
    assert _prop(result, "SEQUENCE") == ["SEQUENCE:4"]
    assert _prop(result, "SUMMARY") == ["SUMMARY:supper"]


def test_zone_block_comes_from_theirs_when_only_theirs_uses_it():
    base = _cal(*BASE_LINES)
    ours = _cal(*BASE_LINES[:3], "SUMMARY:supper")
    theirs = _cal("DTSTAMP:20261118T100000Z", "DTSTART;TZID=Test/Zone:20261119T130000",
                  "DTEND;TZID=Test/Zone:20261119T140000", "SUMMARY:dinner", tz=True)
    result = merge_event(base, ours, theirs)
    assert "TZID:Test/Zone" in result.text.decode()


# --- out of scope ---------------------------------------------------------------

def test_recurrence_is_unsupported():
    base = _cal(*BASE_LINES, "RRULE:FREQ=WEEKLY")
    with pytest.raises(Unsupported):
        merge_event(base, base, base)


def test_different_events_are_unsupported():
    a = _cal(*BASE_LINES)
    b = a.replace(b"UID:u1", b"UID:u2")
    with pytest.raises(Unsupported):
        merge_event(a, a, b)
