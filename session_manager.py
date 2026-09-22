"""
session_manager.py — Examination session lifecycle management (Milestone 1).

Lifecycle: created -> active -> paused -> active -> submitted
Every transition is also written to activity_events so it feeds the
Milestone 2 event log / detection engine.
"""

from datetime import datetime
from db import db_session


class SessionError(Exception):
    pass


VALID_TRANSITIONS = {
    "created": {"active"},
    "active": {"paused", "submitted"},
    "paused": {"active", "submitted"},
    "submitted": set(),
}

_EVENT_FOR_STATUS = {
    "active": "session_start",   # also used for resume; disambiguated by meta below
    "paused": "session_pause",
    "submitted": "session_submit",
}


def _now():
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def create_session(candidate_id: int, exam_name: str = "General Assessment") -> int:
    with db_session() as conn:
        candidate = conn.execute(
            "SELECT candidate_id FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        if candidate is None:
            raise SessionError("Unknown candidate.")
        cur = conn.execute(
            "INSERT INTO exam_sessions (candidate_id, exam_name, status) VALUES (?, ?, 'created')",
            (candidate_id, exam_name),
        )
        return cur.lastrowid


def _log_event(conn, session_id: int, event_type: str, meta: str = None):
    conn.execute(
        "INSERT INTO activity_events (session_id, event_type, meta) VALUES (?, ?, ?)",
        (session_id, event_type, meta),
    )


def _transition(session_id: int, new_status: str, meta: str = None) -> dict:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM exam_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise SessionError("Session not found.")

        current = row["status"]
        if new_status not in VALID_TRANSITIONS.get(current, set()):
            raise SessionError(f"Cannot move session from '{current}' to '{new_status}'.")

        now = _now()
        if new_status == "active" and current == "created":
            conn.execute(
                "UPDATE exam_sessions SET status=?, started_at=? WHERE session_id=?",
                (new_status, now, session_id),
            )
            _log_event(conn, session_id, "session_start", meta)
        elif new_status == "active" and current == "paused":
            conn.execute(
                "UPDATE exam_sessions SET status=?, resumed_at=? WHERE session_id=?",
                (new_status, now, session_id),
            )
            _log_event(conn, session_id, "session_resume", meta)
        elif new_status == "paused":
            conn.execute(
                "UPDATE exam_sessions SET status=?, paused_at=? WHERE session_id=?",
                (new_status, now, session_id),
            )
            _log_event(conn, session_id, "session_pause", meta)
        elif new_status == "submitted":
            conn.execute(
                "UPDATE exam_sessions SET status=?, submitted_at=? WHERE session_id=?",
                (new_status, now, session_id),
            )
            _log_event(conn, session_id, "session_submit", meta)

        updated = conn.execute(
            "SELECT * FROM exam_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(updated)


def start_session(session_id: int) -> dict:
    return _transition(session_id, "active")


def pause_session(session_id: int) -> dict:
    return _transition(session_id, "paused")


def resume_session(session_id: int) -> dict:
    return _transition(session_id, "active")


def submit_session(session_id: int) -> dict:
    return _transition(session_id, "submitted")


def get_session(session_id: int) -> dict | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM exam_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def list_sessions_for_candidate(candidate_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM exam_sessions WHERE candidate_id = ? ORDER BY created_at DESC",
            (candidate_id,),
        ).fetchall()
    return [dict(r) for r in rows]
