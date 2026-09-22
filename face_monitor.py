"""
face_monitor.py — Real-time face presence monitoring (Milestone 2).

Frames are pushed from the browser (via webcam + canvas, see static/js/monitor.js)
to a Flask endpoint as base64 JPEGs. This module runs Haar Cascade detection on
each frame and maintains a per-session "absence tracker" in memory:
  - Face detected after being absent  -> close out the open face_absence_events row.
  - Face not detected                 -> open (or keep open) a face_absence_events row.

No face recognition or liveness detection is performed — presence only, as scoped
for Milestone 2.
"""

import base64
import threading
from datetime import datetime

import cv2
import numpy as np

from db import db_session

FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

# In-memory tracker: session_id -> {"absent_since": datetime|None, "absence_id": int|None}
_lock = threading.Lock()
_absence_state: dict[int, dict] = {}


def _now():
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def decode_frame(data_url: str) -> np.ndarray:
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    img_bytes = base64.b64decode(data_url)
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Could not decode video frame.")
    return frame


def detect_faces(frame: np.ndarray):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = FACE_CASCADE.detectMultiScale(
        gray, scaleFactor=1.15, minNeighbors=5, minSize=(50, 50)
    )
    return faces  # list of (x, y, w, h)


def process_frame(session_id: int, data_url: str) -> dict:
    """
    Process a single webcam frame for a session. Opens/closes face_absence_events
    rows as presence toggles. Returns a status dict for the frontend indicator.
    """
    frame = decode_frame(data_url)
    faces = detect_faces(frame)
    face_present = len(faces) > 0

    with _lock:
        state = _absence_state.setdefault(session_id, {"absent_since": None, "absence_id": None})

        if not face_present and state["absent_since"] is None:
            # transition: present -> absent. Open a new absence interval.
            now = _now()
            with db_session() as conn:
                cur = conn.execute(
                    "INSERT INTO face_absence_events (session_id, started_at) VALUES (?, ?)",
                    (session_id, now),
                )
                state["absence_id"] = cur.lastrowid
            state["absent_since"] = now

        elif face_present and state["absent_since"] is not None:
            # transition: absent -> present. Close out the open interval.
            started = datetime.fromisoformat(state["absent_since"])
            ended = datetime.utcnow()
            duration = (ended - started).total_seconds()
            with db_session() as conn:
                conn.execute(
                    "UPDATE face_absence_events SET ended_at = ?, duration_seconds = ? "
                    "WHERE absence_id = ?",
                    (ended.isoformat(sep=" ", timespec="seconds"), round(duration, 1), state["absence_id"]),
                )
            state["absent_since"] = None
            state["absence_id"] = None

    return {
        "session_id": session_id,
        "face_present": face_present,
        "face_count": len(faces),
        "currently_absent_since": state["absent_since"],
    }


def close_open_absences(session_id: int):
    """Call at session submit/pause to make sure no absence interval is left dangling."""
    with _lock:
        state = _absence_state.get(session_id)
        if state and state["absent_since"] is not None:
            started = datetime.fromisoformat(state["absent_since"])
            ended = datetime.utcnow()
            duration = (ended - started).total_seconds()
            with db_session() as conn:
                conn.execute(
                    "UPDATE face_absence_events SET ended_at = ?, duration_seconds = ? "
                    "WHERE absence_id = ?",
                    (ended.isoformat(sep=" ", timespec="seconds"), round(duration, 1), state["absence_id"]),
                )
            state["absent_since"] = None
            state["absence_id"] = None


def get_absence_summary(session_id: int) -> dict:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM face_absence_events WHERE session_id = ? ORDER BY absence_id",
            (session_id,),
        ).fetchall()
    intervals = [dict(r) for r in rows]
    total_seconds = sum(r["duration_seconds"] or 0 for r in intervals)
    return {
        "session_id": session_id,
        "absence_count": len(intervals),
        "total_absent_seconds": round(total_seconds, 1),
        "intervals": intervals,
    }
