"""
detection_engine.py — Rule-based suspicious event detection engine (Milestone 2).

Applies a configurable set of threshold rules to a session's activity_events
and face_absence_events, writing any triggered rule as a row in flagged_events.

Rules are intentionally simple/explainable (no ML here — that's Milestone 3's
scoring/analytics work); each rule has a severity used later by the integrity
scoring module.
"""

from dataclasses import dataclass
from typing import Callable

from db import db_session
from event_logger import event_counts
from face_monitor import get_absence_summary

# ---- configurable thresholds -------------------------------------------------
THRESHOLDS = {
    "max_tab_switches": 3,          # more than this many tab-switch events -> flag
    "max_face_absent_seconds": 120,  # single continuous absence longer than this -> flag
    "max_total_absent_seconds": 180, # cumulative absence across the session -> flag
    "max_focus_loss": 5,            # excessive focus-loss frequency -> flag
    "max_copy_paste": 4,            # combined copy+paste events -> flag
    "devtools_open_is_critical": True,
}


@dataclass
class Rule:
    code: str
    severity: str  # low | medium | high
    description: str
    check: Callable[[dict, dict], bool]  # (counts, absence_summary) -> bool


def _rule_tab_switches(counts, absence):
    return counts.get("tab_switch", 0) > THRESHOLDS["max_tab_switches"]


def _rule_single_absence(counts, absence):
    return any(
        (iv["duration_seconds"] or 0) > THRESHOLDS["max_face_absent_seconds"]
        for iv in absence["intervals"]
    )


def _rule_total_absence(counts, absence):
    return absence["total_absent_seconds"] > THRESHOLDS["max_total_absent_seconds"]


def _rule_focus_loss(counts, absence):
    return counts.get("focus_loss", 0) > THRESHOLDS["max_focus_loss"]


def _rule_copy_paste(counts, absence):
    return (counts.get("copy", 0) + counts.get("paste", 0)) > THRESHOLDS["max_copy_paste"]


def _rule_devtools(counts, absence):
    return THRESHOLDS["devtools_open_is_critical"] and counts.get("devtools_open", 0) > 0


RULES: list[Rule] = [
    Rule("EXCESSIVE_TAB_SWITCH", "medium",
         "More than {max_tab_switches} tab-switch events detected during the session.",
         _rule_tab_switches),
    Rule("PROLONGED_FACE_ABSENCE", "high",
         "Face was absent from the camera for a single continuous interval exceeding "
         "{max_face_absent_seconds} seconds.",
         _rule_single_absence),
    Rule("CUMULATIVE_FACE_ABSENCE", "medium",
         "Total face-absent time across the session exceeded {max_total_absent_seconds} seconds.",
         _rule_total_absence),
    Rule("EXCESSIVE_FOCUS_LOSS", "medium",
         "Browser window focus was lost more than {max_focus_loss} times.",
         _rule_focus_loss),
    Rule("EXCESSIVE_COPY_PASTE", "low",
         "Combined copy/paste actions exceeded {max_copy_paste} during the session.",
         _rule_copy_paste),
    Rule("DEVTOOLS_OPENED", "high",
         "Browser developer tools were opened during the session.",
         _rule_devtools),
]


def evaluate_session(session_id: int, persist: bool = True) -> list[dict]:
    """
    Runs every rule against the session's current event/absence data.
    Returns the list of newly-triggered flags (dicts). If persist=True, also
    clears any prior flags for this session and writes the fresh set to
    flagged_events (so re-running is idempotent rather than duplicating rows).
    """
    counts = event_counts(session_id)
    absence = get_absence_summary(session_id)

    triggered = []
    for rule in RULES:
        if rule.check(counts, absence):
            triggered.append({
                "session_id": session_id,
                "rule_code": rule.code,
                "severity": rule.severity,
                "description": rule.description.format(**THRESHOLDS),
            })

    if persist:
        with db_session() as conn:
            conn.execute("DELETE FROM flagged_events WHERE session_id = ?", (session_id,))
            for flag in triggered:
                conn.execute(
                    "INSERT INTO flagged_events (session_id, rule_code, severity, description) "
                    "VALUES (?, ?, ?, ?)",
                    (flag["session_id"], flag["rule_code"], flag["severity"], flag["description"]),
                )

    return triggered


def get_flags(session_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM flagged_events WHERE session_id = ? ORDER BY flagged_at",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]
