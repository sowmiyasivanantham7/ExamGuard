"""
app.py — ExamGuard Flask application.

Milestone 1: registration/login, session lifecycle (create/start/pause/resume/submit).
Milestone 2: webcam frame ingestion (face presence), browser event ingestion,
             and the rule-based detection engine run at submission time.
"""

import os
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, flash

from db import init_db
import auth
import session_manager as sm
import event_logger
import face_monitor
import detection_engine as de

app = Flask(__name__)
app.secret_key = os.environ.get("EXAMGUARD_SECRET", "dev-secret-change-me")


def current_candidate_id():
    return session.get("candidate_id")


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_candidate_id():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- pages ----

@app.route("/")
def index():
    if current_candidate_id():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    data = request.form
    photo_data_url = data.get("photo_data")
    try:
        candidate_id = auth.register_candidate(
            data.get("full_name"), data.get("email"), data.get("password"), photo_data_url
        )
    except auth.AuthError as e:
        flash(str(e), "error")
        return render_template("register.html"), 400

    session["candidate_id"] = candidate_id
    flash("Registration successful.", "success")
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    try:
        candidate = auth.login_candidate(request.form.get("email"), request.form.get("password"))
    except auth.AuthError as e:
        flash(str(e), "error")
        return render_template("login.html"), 401

    session["candidate_id"] = candidate["candidate_id"]
    session["candidate_name"] = candidate["full_name"]
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    sessions_list = sm.list_sessions_for_candidate(current_candidate_id())
    return render_template("dashboard.html", sessions=sessions_list,
                            name=session.get("candidate_name", ""))


@app.route("/exam/new", methods=["POST"])
@login_required
def new_exam():
    exam_name = request.form.get("exam_name", "General Assessment")
    session_id = sm.create_session(current_candidate_id(), exam_name)
    sm.start_session(session_id)
    return redirect(url_for("exam_room", session_id=session_id))


@app.route("/exam/<int:session_id>")
@login_required
def exam_room(session_id):
    exam_session = sm.get_session(session_id)
    if not exam_session or exam_session["candidate_id"] != current_candidate_id():
        return "Not found", 404
    return render_template("exam.html", exam_session=exam_session)


# ------------------------------------------------------------- API: M1 -----

@app.route("/api/session/<int:session_id>/pause", methods=["POST"])
@login_required
def api_pause(session_id):
    try:
        updated = sm.pause_session(session_id)
        face_monitor.close_open_absences(session_id)
        return jsonify(updated)
    except sm.SessionError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/session/<int:session_id>/resume", methods=["POST"])
@login_required
def api_resume(session_id):
    try:
        return jsonify(sm.resume_session(session_id))
    except sm.SessionError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/session/<int:session_id>/submit", methods=["POST"])
@login_required
def api_submit(session_id):
    try:
        face_monitor.close_open_absences(session_id)
        updated = sm.submit_session(session_id)
        flags = de.evaluate_session(session_id)
        return jsonify({"session": updated, "flags": flags})
    except sm.SessionError as e:
        return jsonify({"error": str(e)}), 400


# ------------------------------------------------------------- API: M2 -----

@app.route("/api/session/<int:session_id>/frame", methods=["POST"])
@login_required
def api_frame(session_id):
    payload = request.get_json(silent=True) or {}
    frame_data = payload.get("frame")
    if not frame_data:
        return jsonify({"error": "missing frame"}), 400
    try:
        result = face_monitor.process_frame(session_id, frame_data)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/session/<int:session_id>/event", methods=["POST"])
@login_required
def api_event(session_id):
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("event_type")
    meta = payload.get("meta")
    try:
        event_id = event_logger.log_event(session_id, event_type, meta)
        # re-evaluate rules live so an invigilator dashboard could show flags in real time
        flags = de.evaluate_session(session_id)
        return jsonify({"event_id": event_id, "flags": flags})
    except event_logger.EventLogError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/session/<int:session_id>/status")
@login_required
def api_status(session_id):
    exam_session = sm.get_session(session_id)
    if not exam_session:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "session": exam_session,
        "event_counts": event_logger.event_counts(session_id),
        "absence_summary": face_monitor.get_absence_summary(session_id),
        "flags": de.get_flags(session_id),
    })


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
