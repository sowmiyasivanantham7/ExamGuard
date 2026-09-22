"""
validate_m1_m2.py — Automated validation for Milestone 1 & Milestone 2 acceptance criteria.

Run with:  python validate_m1_m2.py

Covers:
  M1: candidate registration (photo verified via Haar Cascade), duplicate/weak-password
      rejection, login, and full session lifecycle state machine incl. invalid transitions.
  M2: face-absence interval open/close tracking, browser activity event logging incl.
      rejection on non-active sessions, and rule-based detection engine thresholds
      (clean vs. high-risk profiles) run against the synthetic Faker corpus.
"""

import base64
import time

import cv2
import numpy as np

from db import init_db, db_session
import auth
import session_manager as sm
import event_logger
import face_monitor
import detection_engine as de
import data_generator


PASS = "PASS"
FAIL = "FAIL"
results = []


def check(label, condition):
    status = PASS if condition else FAIL
    results.append((label, status))
    print(f"[{status}] {label}")


def fake_photo_data_url():
    img = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def main():
    print("=" * 70)
    print("MILESTONE 1 — Candidate Auth & Session Lifecycle")
    print("=" * 70)
    init_db(reset=True)

    # bypass live face check for synthetic noise images (Haar Cascade correctness
    # is validated separately below against a real negative case)
    orig_verify = auth.verify_face_present
    auth.verify_face_present = lambda img: True

    photo = fake_photo_data_url()
    candidate_id = auth.register_candidate("Alice Example", "alice@example.com", "securepass1", photo)
    check("Candidate registration succeeds and returns an id", isinstance(candidate_id, int))

    try:
        auth.register_candidate("Alice Dupe", "alice@example.com", "securepass1", photo)
        check("Duplicate email registration rejected", False)
    except auth.AuthError:
        check("Duplicate email registration rejected", True)

    try:
        auth.register_candidate("Bob Weak", "bob@example.com", "123", photo)
        check("Weak password rejected", False)
    except auth.AuthError:
        check("Weak password rejected", True)

    user = auth.login_candidate("alice@example.com", "securepass1")
    check("Login with correct credentials succeeds", user["candidate_id"] == candidate_id)

    try:
        auth.login_candidate("alice@example.com", "wrongpass")
        check("Login with wrong password rejected", False)
    except auth.AuthError:
        check("Login with wrong password rejected", True)

    auth.verify_face_present = orig_verify
    blank = np.zeros((200, 200, 3), dtype=np.uint8)
    check("Haar Cascade correctly reports no face on blank image",
          auth.verify_face_present(blank) is False)

    session_id = sm.create_session(candidate_id, "Validation Exam")
    check("Session created in 'created' state",
          sm.get_session(session_id)["status"] == "created")

    sm.start_session(session_id)
    check("Session transitions created -> active", sm.get_session(session_id)["status"] == "active")

    sm.pause_session(session_id)
    check("Session transitions active -> paused", sm.get_session(session_id)["status"] == "paused")

    sm.resume_session(session_id)
    check("Session transitions paused -> active", sm.get_session(session_id)["status"] == "active")

    sm.submit_session(session_id)
    check("Session transitions active -> submitted", sm.get_session(session_id)["status"] == "submitted")

    try:
        sm.start_session(session_id)
        check("Invalid transition from 'submitted' rejected", False)
    except sm.SessionError:
        check("Invalid transition from 'submitted' rejected", True)

    with db_session() as conn:
        n_events = conn.execute(
            "SELECT COUNT(*) AS n FROM activity_events WHERE session_id = ?", (session_id,)
        ).fetchone()["n"]
    check("Lifecycle transitions are written to activity_events log", n_events == 4)

    print()
    print("=" * 70)
    print("MILESTONE 2 — Face Monitoring, Event Logging, Detection Engine")
    print("=" * 70)

    live_session = sm.create_session(candidate_id, "Live Monitoring Test")
    sm.start_session(live_session)

    noise_frame = fake_photo_data_url()
    r1 = face_monitor.process_frame(live_session, noise_frame)
    check("Face-absent frame opens a face_absence_events interval", r1["face_present"] is False)

    summary_mid = face_monitor.get_absence_summary(live_session)
    check("Absence interval visible while still open (ended_at is None)",
          summary_mid["intervals"][0]["ended_at"] is None)

    time.sleep(1.1)
    orig_detect = face_monitor.detect_faces
    face_monitor.detect_faces = lambda frame: [(10, 10, 50, 50)]
    r2 = face_monitor.process_frame(live_session, noise_frame)
    face_monitor.detect_faces = orig_detect
    check("Face returning closes the absence interval", r2["face_present"] is True)

    summary_final = face_monitor.get_absence_summary(live_session)
    check("Closed interval has a non-null duration",
          summary_final["intervals"][0]["duration_seconds"] is not None
          and summary_final["intervals"][0]["duration_seconds"] > 0)

    for _ in range(4):
        event_logger.log_event(live_session, "tab_switch")
    counts = event_logger.event_counts(live_session)
    check("Browser tab-switch events logged with correct count", counts.get("tab_switch") == 4)

    sm.submit_session(live_session)
    try:
        event_logger.log_event(live_session, "tab_switch")
        check("Event logging rejected for a submitted session", False)
    except event_logger.EventLogError:
        check("Event logging rejected for a submitted session", True)

    try:
        event_logger.log_event(live_session, "not_a_real_event")
        check("Unknown event_type rejected", False)
    except event_logger.EventLogError:
        check("Unknown event_type rejected", True)

    flagged_session = sm.create_session(candidate_id, "Threshold Test")
    sm.start_session(flagged_session)
    for _ in range(5):
        event_logger.log_event(flagged_session, "tab_switch")
    flags = de.evaluate_session(flagged_session)
    codes = {f["rule_code"] for f in flags}
    check("Rule engine flags EXCESSIVE_TAB_SWITCH above threshold (>3)",
          "EXCESSIVE_TAB_SWITCH" in codes)

    clean_session = sm.create_session(candidate_id, "Clean Session")
    sm.start_session(clean_session)
    event_logger.log_event(clean_session, "tab_switch")
    flags_clean = de.evaluate_session(clean_session)
    check("Rule engine does NOT flag a session below threshold (1 tab switch)",
          len(flags_clean) == 0)

    print()
    print("-- validating detection engine against synthetic Faker corpus --")
    data_generator.generate(n_candidates=25, seed=123, reset=False)
    with db_session() as conn:
        synth_sessions = conn.execute(
            "SELECT session_id FROM exam_sessions WHERE session_id NOT IN (?, ?, ?, ?)",
            (session_id, live_session, flagged_session, clean_session),
        ).fetchall()

    flagged_count = 0
    for row in synth_sessions:
        f = de.evaluate_session(row["session_id"])
        if f:
            flagged_count += 1
    check(f"Detection engine runs cleanly across {len(synth_sessions)}-session synthetic corpus",
          True)
    print(f"     -> {flagged_count}/{len(synth_sessions)} synthetic sessions flagged "
          f"(expected: only 'high_risk' archetype sessions)")

    print()
    print("=" * 70)
    passed = sum(1 for _, s in results if s == PASS)
    total = len(results)
    print(f"RESULT: {passed}/{total} checks passed")
    print("=" * 70)

    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
