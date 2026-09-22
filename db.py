"""
db.py — SQLite database schema and connection helpers for ExamGuard.

Milestone 1 deliverable: system architecture / database schema.

Tables
------
candidates          : registered candidates + registration photo path
exam_sessions        : one row per exam attempt (lifecycle: created -> active -> paused -> submitted)
face_absence_events   : intervals where no face was detected during a session (Milestone 2)
activity_events       : browser focus/tab-switch/interaction events (Milestone 2)
flagged_events         : output of the rule-based suspicious event detection engine (Milestone 2)
integrity_scores        : per-session integrity score + risk label (Milestone 3, schema reserved now)
ai_reports              : LangChain-generated natural language summaries (Milestone 4, schema reserved now)
"""

import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "examguard.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    photo_path      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exam_sessions (
    session_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    INTEGER NOT NULL,
    exam_name       TEXT NOT NULL DEFAULT 'General Assessment',
    status          TEXT NOT NULL DEFAULT 'created'
                        CHECK (status IN ('created','active','paused','submitted')),
    started_at      TEXT,
    paused_at       TEXT,
    resumed_at      TEXT,
    submitted_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
);

CREATE TABLE IF NOT EXISTS face_absence_events (
    absence_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    duration_seconds REAL,
    FOREIGN KEY (session_id) REFERENCES exam_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS activity_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    event_type      TEXT NOT NULL
                        CHECK (event_type IN ('focus_loss','focus_regain','tab_switch',
                                               'fullscreen_exit','copy','paste','right_click',
                                               'devtools_open','session_start','session_pause',
                                               'session_resume','session_submit')),
    event_time      TEXT NOT NULL DEFAULT (datetime('now')),
    meta            TEXT,
    FOREIGN KEY (session_id) REFERENCES exam_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS flagged_events (
    flag_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    rule_code       TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    description     TEXT NOT NULL,
    flagged_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES exam_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS integrity_scores (
    score_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL UNIQUE,
    score           REAL,
    risk_label      TEXT CHECK (risk_label IN ('Low','Medium','High')),
    computed_at     TEXT,
    FOREIGN KEY (session_id) REFERENCES exam_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS ai_reports (
    report_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL UNIQUE,
    summary_text    TEXT,
    generated_at    TEXT,
    FOREIGN KEY (session_id) REFERENCES exam_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_candidate ON exam_sessions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_activity_session ON activity_events(session_id);
CREATE INDEX IF NOT EXISTS idx_absence_session ON face_absence_events(session_id);
CREATE INDEX IF NOT EXISTS idx_flags_session ON flagged_events(session_id);
"""


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    """Context manager that yields a connection and commits/closes automatically."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(reset: bool = False):
    """Create all tables. If reset=True, drop the existing DB file first."""
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    with db_session() as conn:
        conn.executescript(SCHEMA)
    print(f"[db] initialised at {DB_PATH}")


if __name__ == "__main__":
    init_db(reset=True)
