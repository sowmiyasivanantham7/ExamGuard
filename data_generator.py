"""
data_generator.py — Synthetic session log data generator (Milestone 1, Week-1 warm-up).

Simulates realistic candidate exam sessions with varied event patterns so the
detection engine (Milestone 2) and analytics module (Milestone 3) have a
corpus to work against without needing real proctoring data.

Usage:
    python data_generator.py --candidates 15 --seed 42
"""

import argparse
import random
from datetime import timedelta

from faker import Faker
from werkzeug.security import generate_password_hash

from db import db_session, init_db

fake = Faker()

EVENT_TYPES_POOL = ["focus_loss", "focus_regain", "tab_switch", "fullscreen_exit",
                     "copy", "paste", "right_click"]

# behaviour archetypes so generated sessions aren't uniformly random —
# gives the downstream clustering module (Milestone 3) real structure to find
ARCHETYPES = {
    "clean":       {"event_count": (0, 3),  "absence_count": (0, 1), "absence_len": (2, 15)},
    "distracted":  {"event_count": (4, 10), "absence_count": (1, 3), "absence_len": (5, 40)},
    "high_risk":   {"event_count": (10, 25), "absence_count": (3, 8), "absence_len": (30, 150)},
}


def _random_time_within(start, span_minutes):
    offset = random.uniform(0, span_minutes * 60)
    return start + timedelta(seconds=offset)


def generate_candidate(conn, session_length_minutes=60):
    name = fake.name()
    email = fake.unique.email()
    password_hash = generate_password_hash("password123")
    photo_path = f"static/captures/synthetic_{fake.uuid4()}.jpg"

    cur = conn.execute(
        "INSERT INTO candidates (full_name, email, password_hash, photo_path) VALUES (?, ?, ?, ?)",
        (name, email, password_hash, photo_path),
    )
    candidate_id = cur.lastrowid

    archetype_name = random.choices(
        list(ARCHETYPES.keys()), weights=[0.5, 0.35, 0.15]
    )[0]
    profile = ARCHETYPES[archetype_name]

    start_time = fake.date_time_between(start_date="-30d", end_date="now")
    submit_time = start_time + timedelta(minutes=session_length_minutes)

    cur = conn.execute(
        "INSERT INTO exam_sessions (candidate_id, exam_name, status, started_at, submitted_at) "
        "VALUES (?, 'General Assessment', 'submitted', ?, ?)",
        (candidate_id, start_time.isoformat(sep=" "), submit_time.isoformat(sep=" ")),
    )
    session_id = cur.lastrowid

    conn.execute(
        "INSERT INTO activity_events (session_id, event_type, event_time) VALUES (?, 'session_start', ?)",
        (session_id, start_time.isoformat(sep=" ")),
    )

    n_events = random.randint(*profile["event_count"])
    for _ in range(n_events):
        etype = random.choice(EVENT_TYPES_POOL)
        etime = _random_time_within(start_time, session_length_minutes)
        conn.execute(
            "INSERT INTO activity_events (session_id, event_type, event_time) VALUES (?, ?, ?)",
            (session_id, etype, etime.isoformat(sep=" ")),
        )

    n_absences = random.randint(*profile["absence_count"])
    for _ in range(n_absences):
        a_start = _random_time_within(start_time, session_length_minutes)
        duration = random.uniform(*profile["absence_len"])
        a_end = a_start + timedelta(seconds=duration)
        conn.execute(
            "INSERT INTO face_absence_events (session_id, started_at, ended_at, duration_seconds) "
            "VALUES (?, ?, ?, ?)",
            (session_id, a_start.isoformat(sep=" "), a_end.isoformat(sep=" "), round(duration, 1)),
        )

    conn.execute(
        "INSERT INTO activity_events (session_id, event_type, event_time) VALUES (?, 'session_submit', ?)",
        (session_id, submit_time.isoformat(sep=" ")),
    )

    return candidate_id, session_id, archetype_name


def generate(n_candidates: int = 20, seed: int = None, reset: bool = False):
    if seed is not None:
        random.seed(seed)
        Faker.seed(seed)

    init_db(reset=reset)

    summary = {"clean": 0, "distracted": 0, "high_risk": 0}
    with db_session() as conn:
        for _ in range(n_candidates):
            _, _, archetype = generate_candidate(conn)
            summary[archetype] += 1

    print(f"[data_generator] created {n_candidates} synthetic candidate sessions")
    print(f"[data_generator] archetype breakdown: {summary}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic ExamGuard session data")
    parser.add_argument("--candidates", type=int, default=20)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--reset", action="store_true", help="wipe the DB before generating")
    args = parser.parse_args()

    generate(n_candidates=args.candidates, seed=args.seed, reset=args.reset)
