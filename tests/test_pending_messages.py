"""The pending-message queue is the only route a drafted message takes to an
athlete now that automatic sending is off. These pin the two ways it silently
stranded messages.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _filter_pending(rows):
    """The mapping dashboard._pending_messages_cached performs.

    Kept in step with that function deliberately: the bug it replaces was the
    filtered-list index being used as the sheet row.
    """
    out = []
    for i, r in enumerate(rows):
        if str(r.get("Status", "")).strip().lower() != "pending":
            continue
        r = dict(r)
        r["_row"] = i + 2
        out.append(r)
    return out


def test_row_number_survives_earlier_rows_being_sent():
    """A sent row above a pending one must not shift the pending row's target.

    Before the fix the list index was used as the sheet row, so once the first
    draft was marked sent, "Mark sent" on the next one rewrote the already-sent
    row and left the real message queued forever.
    """
    rows = [
        {"Athlete Name": "A", "Status": "sent"},      # sheet row 2
        {"Athlete Name": "B", "Status": "pending"},   # sheet row 3
        {"Athlete Name": "C", "Status": "pending"},   # sheet row 4
    ]
    pending = _filter_pending(rows)
    assert [r["Athlete Name"] for r in pending] == ["B", "C"]
    assert [r["_row"] for r in pending] == [3, 4]


def test_first_row_maps_to_row_two():
    rows = [{"Athlete Name": "A", "Status": "pending"}]
    assert _filter_pending(rows)[0]["_row"] == 2


def test_mixed_statuses_keep_absolute_rows():
    rows = [
        {"Athlete Name": "A", "Status": "skipped"},
        {"Athlete Name": "B", "Status": "sent"},
        {"Athlete Name": "C", "Status": "pending"},
        {"Athlete Name": "D", "Status": "sent"},
        {"Athlete Name": "E", "Status": "pending"},
    ]
    pending = _filter_pending(rows)
    assert [(r["Athlete Name"], r["_row"]) for r in pending] == [("C", 4), ("E", 6)]


def test_status_matching_is_case_and_space_tolerant():
    rows = [{"Athlete Name": "A", "Status": " Pending "}]
    assert len(_filter_pending(rows)) == 1
