"""
auth.py — Candidate authentication (Milestone 1).

Handles registration (with OpenCV-verified registration photo) and login.
Passwords are salted + hashed with werkzeug's security helpers.
"""

import os
import base64
import re
from datetime import datetime

import cv2
import numpy as np
from werkzeug.security import generate_password_hash, check_password_hash

from db import db_session

CAPTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "captures")
FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    pass


def _decode_data_url_image(data_url: str) -> np.ndarray:
    """Decode a base64 data-URL (from <canvas>.toDataURL()) into a BGR image array."""
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    img_bytes = base64.b64decode(data_url)
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise AuthError("Could not decode registration photo.")
    return img


def verify_face_present(img: np.ndarray) -> bool:
    """Run Haar Cascade face detection on a still image. Returns True if exactly one face found."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
    return len(faces) >= 1


def register_candidate(full_name: str, email: str, password: str, photo_data_url: str) -> int:
    """
    Register a new candidate. Captures + verifies a registration photo with OpenCV
    Haar Cascade face detection before persisting the candidate record.
    Returns the new candidate_id.
    """
    full_name = (full_name or "").strip()
    email = (email or "").strip().lower()

    if not full_name:
        raise AuthError("Full name is required.")
    if not EMAIL_RE.match(email):
        raise AuthError("A valid email is required.")
    if not password or len(password) < 6:
        raise AuthError("Password must be at least 6 characters.")
    if not photo_data_url:
        raise AuthError("Registration photo is required.")

    img = _decode_data_url_image(photo_data_url)
    if not verify_face_present(img):
        raise AuthError("No face detected in the registration photo. Please retake it.")

    os.makedirs(CAPTURES_DIR, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    filename = f"candidate_{timestamp}.jpg"
    photo_path = os.path.join(CAPTURES_DIR, filename)
    cv2.imwrite(photo_path, img)

    password_hash = generate_password_hash(password)

    with db_session() as conn:
        existing = conn.execute(
            "SELECT candidate_id FROM candidates WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            raise AuthError("An account with this email already exists.")

        cur = conn.execute(
            "INSERT INTO candidates (full_name, email, password_hash, photo_path) "
            "VALUES (?, ?, ?, ?)",
            (full_name, email, password_hash, os.path.relpath(photo_path, os.path.dirname(os.path.abspath(__file__)))),
        )
        return cur.lastrowid


def login_candidate(email: str, password: str) -> dict:
    """Verify credentials. Returns the candidate row as a dict on success."""
    email = (email or "").strip().lower()
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM candidates WHERE email = ?", (email,)
        ).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password or ""):
        raise AuthError("Invalid email or password.")
    return dict(row)


def get_candidate(candidate_id: int) -> dict | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
    return dict(row) if row else None
