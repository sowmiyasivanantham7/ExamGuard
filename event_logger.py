"""
event_logger.py — Browser activity & event logging (Milestone 2).

Receives events pushed from static/js/monitor.js (visibilitychange, window
blur/focus, copy/paste, right-click, fullscreen-exit) and persists them to
the activity_events table with a timestamp, so the detection engine and
later the analytics module (Milestone 3) have a full interaction trail.
"""

from db import db_session

ALLOWED_EVENTS = {
    "focus_loss", "focus_regain", "tab_switch", "fullscreen_exit",
    "copy", "paste", "right_click", "devtools_open",
}


class EventLogError(Exception):
    pass


def log_event(session_id: int, event_type: str, meta: str = None) -> int:
    if event_type not in ALLOWED_EVENTS:
        raise EventLogError(f"Unknown event_type '{event_type}'.")

    with db_session() as conn:
        session_row = conn.execute(
            "SELECT status FROM exam_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if session_row is None:
            raise EventLogError("Session not found.")
        if session_row["status"] not in ("active", "paused"):
            raise EventLogError(f"Cannot log events for a '{session_row['status']}' session.")

        cur = conn.execute(
            "INSERT INTO activity_events (session_id, event_type, meta) VALUES (?, ?, ?)",
            (session_id, event_type, meta),
        )
        return cur.lastrowid


def get_events(session_id: int, event_type: str = None) -> list[dict]:
    query = "SELECT * FROM activity_events WHERE session_id = ?"
    params = [session_id]
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    query += " ORDER BY event_time"

    with db_session() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def event_counts(session_id: int) -> dict:
    """Tally of each event_type for a session — the raw input to the detection engine."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) AS n FROM activity_events "
            "WHERE session_id = ? GROUP BY event_type",
            (session_id,),
        ).fetchall()
    return {r["event_type"]: r["n"] for r in rows}
