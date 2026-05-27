"""
JHIMS Consultation Portal
=========================
Standalone portal for online consultation workflows.

Patient module:
- Sign up / login
- Browse doctors
- Book online consultation appointments
- Track appointments and chat with doctor

Doctor module:
- Sign up / login
- Review incoming bookings
- Accept / reschedule / cancel / complete consultations
- Record diagnosis, prescription, notes, and follow-up
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, g, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("JHIMS_CONSULTATION_DB", os.path.join(BASE_DIR, "consultation_portal.db"))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
PUBLIC_URL = os.environ.get("JHIMS_CONSULTATION_PUBLIC_URL", "https://consultation.jhimssoftware.com")
PORT = int(os.environ.get("PORT") or os.environ.get("JHIMS_CONSULTATION_PORT", "5052"))

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = os.environ.get("JHIMS_CONSULTATION_SECRET_KEY", "consultation-change-me")

VISIT_MODES = ["Video Call", "Audio Call", "Chat Consultation", "In-Person Follow-up"]
DOCTOR_SPECIALTIES = [
    "General Practice",
    "Family Medicine",
    "Internal Medicine",
    "Pediatrics",
    "Obstetrics and Gynecology",
    "Surgery",
    "Dermatology",
    "Psychiatry",
    "Cardiology",
    "Orthopedics",
]

MODULE_PAGES = {
    "medical-records": {
        "title": "Medical Records",
        "summary": "Patient history, allergies, diagnoses, and visit records in one secure workspace.",
        "actions": [
            {"label": "Patient Dashboard", "endpoint": "consult_patient_dashboard"},
            {"label": "Find Doctors", "endpoint": "consult_doctor_directory"},
        ],
    },
    "e-prescriptions": {
        "title": "e-Prescriptions",
        "summary": "Digital prescriptions shared between doctors and patients with clear refill tracking.",
        "actions": [
            {"label": "Patient Appointments", "endpoint": "consult_patient_dashboard"},
            {"label": "Doctor Dashboard", "endpoint": "consult_doctor_dashboard"},
        ],
    },
    "lab-results": {
        "title": "Lab Results",
        "summary": "Upload, review, and share investigation results linked to consultation encounters.",
        "actions": [
            {"label": "Patient Dashboard", "endpoint": "consult_patient_dashboard"},
            {"label": "Doctor Dashboard", "endpoint": "consult_doctor_dashboard"},
        ],
    },
    "payments": {
        "title": "Payments",
        "summary": "Track consultation fees, service charges, and payment confirmations.",
        "actions": [
            {"label": "Book Consultation", "endpoint": "consult_doctor_directory"},
            {"label": "Patient Dashboard", "endpoint": "consult_patient_dashboard"},
        ],
    },
    "patients": {
        "title": "Patients",
        "summary": "Doctor-facing patient queue with consultation status and communication history.",
        "actions": [
            {"label": "Doctor Dashboard", "endpoint": "consult_doctor_dashboard"},
            {"label": "Consultations", "endpoint": "consult_doctor_dashboard"},
        ],
    },
    "consultations": {
        "title": "Consultations",
        "summary": "Consultation tracking center for appointment lifecycle and follow-ups.",
        "actions": [
            {"label": "Patient Portal", "endpoint": "consult_patient_dashboard"},
            {"label": "Doctor Portal", "endpoint": "consult_doctor_dashboard"},
        ],
    },
    "reports": {
        "title": "Reports",
        "summary": "Operational analytics for appointment volume, completion trends, and doctor activity.",
        "actions": [
            {"label": "Consultation Home", "endpoint": "consultation_home"},
            {"label": "Doctor Dashboard", "endpoint": "consult_doctor_dashboard"},
        ],
    },
    "users-roles": {
        "title": "Users & Roles",
        "summary": "Access governance for patient, doctor, and administrative user permissions.",
        "actions": [
            {"label": "Consultation Home", "endpoint": "consultation_home"},
            {"label": "Doctor Dashboard", "endpoint": "consult_doctor_dashboard"},
        ],
    },
    "departments": {
        "title": "Departments",
        "summary": "Organize doctor specialties and departmental consultation routing.",
        "actions": [
            {"label": "Find Doctors", "endpoint": "consult_doctor_directory"},
            {"label": "Consultation Home", "endpoint": "consultation_home"},
        ],
    },
    "settings": {
        "title": "System Settings",
        "summary": "Portal configuration for schedules, availability, and operational preferences.",
        "actions": [
            {"label": "Consultation Home", "endpoint": "consultation_home"},
            {"label": "Doctor Profile", "endpoint": "consult_doctor_profile"},
        ],
    },
    "audit-logs": {
        "title": "Audit Logs",
        "summary": "Review security and activity events across the consultation platform.",
        "actions": [
            {"label": "Consultation Home", "endpoint": "consultation_home"},
            {"label": "Reports", "endpoint": "consult_module_page", "kwargs": {"slug": "reports"}},
        ],
    },
    "billing": {
        "title": "Billing",
        "summary": "Billing operations center for consultation fees and revenue reconciliation.",
        "actions": [
            {"label": "Payments", "endpoint": "consult_module_page", "kwargs": {"slug": "payments"}},
            {"label": "Reports", "endpoint": "consult_module_page", "kwargs": {"slug": "reports"}},
        ],
    },
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _date_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _appointment_code() -> str:
    return f"CONS-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def _safe_int(value, default=0):
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_float(value, default=0.0):
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _generate_tx_ref() -> str:
    return f"PAY-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def _upsert_setting(setting_key: str, setting_value: str):
    conn = get_db()
    conn.execute(
        """INSERT INTO consult_portal_settings(setting_key, setting_value, updated_at)
           VALUES(?,?,?)
           ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
        ((setting_key or "").strip(), (setting_value or "").strip(), _now_text()),
    )
    conn.commit()


def _audit_log(action: str, target: str = "", details: str = "", actor_role: str = "", actor_id: int | None = None):
    role = actor_role or "system"
    aid = actor_id
    if not actor_role:
        if session.get("consult_patient_id"):
            role = "patient"
            aid = _safe_int(session.get("consult_patient_id"), 0)
        elif session.get("consult_doctor_id"):
            role = "doctor"
            aid = _safe_int(session.get("consult_doctor_id"), 0)
    conn = get_db()
    conn.execute(
        """INSERT INTO consult_audit_logs(actor_role, actor_id, action, target, details, created_at)
           VALUES(?,?,?,?,?,?)""",
        (role, aid if aid else None, action, (target or "").strip(), (details or "").strip(), _now_text()),
    )
    conn.commit()


def _valid_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", (value or "").strip()))


def _valid_phone(value: str) -> bool:
    clean = re.sub(r"[^\d+]", "", value or "")
    digits = re.sub(r"\D", "", clean)
    return len(digits) >= 9


def _password_policy_errors(password: str, email: str = "", full_name: str = "") -> list[str]:
    value = password or ""
    errors = []
    if len(value) < 8:
        errors.append("Password must be at least 8 characters long.")
    if not re.search(r"[A-Z]", value):
        errors.append("Password must include at least one uppercase letter.")
    if not re.search(r"[a-z]", value):
        errors.append("Password must include at least one lowercase letter.")
    if not re.search(r"\d", value):
        errors.append("Password must include at least one number.")
    if not re.search(r"[^A-Za-z0-9]", value):
        errors.append("Password must include at least one special character.")
    if re.search(r"\s", value):
        errors.append("Password must not contain spaces.")

    email_local = (email or "").strip().lower().split("@")[0]
    if email_local and len(email_local) >= 3 and email_local in value.lower():
        errors.append("Password should not contain your email name.")

    name_tokens = [part.lower() for part in re.split(r"[\s\-_]+", (full_name or "").strip()) if len(part) >= 3]
    lowered = value.lower()
    if any(token in lowered for token in name_tokens):
        errors.append("Password should not contain your personal name.")

    return errors


def get_db():
    db = getattr(g, "_consult_db", None)
    if db is None:
        db = g._consult_db = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_consult_db", None)
    if db:
        db.close()


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_patients(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            date_of_birth TEXT,
            gender TEXT,
            created_at TEXT,
            last_login_at TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_doctors(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            specialty TEXT NOT NULL,
            bio TEXT,
            years_experience INTEGER DEFAULT 0,
            consultation_fee REAL DEFAULT 0,
            available_days TEXT,
            available_hours TEXT,
            meeting_provider TEXT DEFAULT 'Google Meet / Zoom / WhatsApp',
            status TEXT DEFAULT 'active',
            verification_status TEXT DEFAULT 'pending',
            created_at TEXT,
            last_login_at TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_appointments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_code TEXT UNIQUE NOT NULL,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            scheduled_for TEXT NOT NULL,
            visit_mode TEXT NOT NULL,
            reason TEXT NOT NULL,
            symptoms TEXT,
            status TEXT NOT NULL DEFAULT 'Requested',
            meeting_link TEXT,
            patient_cancel_reason TEXT,
            doctor_cancel_reason TEXT,
            doctor_notes TEXT,
            diagnosis TEXT,
            prescription TEXT,
            follow_up_date TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES consult_patients(id),
            FOREIGN KEY(doctor_id) REFERENCES consult_doctors(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_appointment_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            sender_role TEXT NOT NULL,
            sender_id INTEGER,
            sender_name TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY(appointment_id) REFERENCES consult_appointments(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_lab_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER,
            report_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            result_link TEXT,
            status TEXT NOT NULL DEFAULT 'Pending Review',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(patient_id) REFERENCES consult_patients(id),
            FOREIGN KEY(doctor_id) REFERENCES consult_doctors(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            patient_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'GHS',
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Paid',
            tx_ref TEXT UNIQUE NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(appointment_id) REFERENCES consult_appointments(id),
            FOREIGN KEY(patient_id) REFERENCES consult_patients(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_portal_settings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT NOT NULL UNIQUE,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_role TEXT NOT NULL,
            actor_id INTEGER,
            action TEXT NOT NULL,
            target TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_appt_patient ON consult_appointments(patient_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_appt_doctor ON consult_appointments(doctor_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_appt_status ON consult_appointments(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_msg_appt ON consult_appointment_messages(appointment_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_lab_patient ON consult_lab_results(patient_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_lab_doctor ON consult_lab_results(doctor_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_pay_patient ON consult_payments(patient_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_pay_appt ON consult_payments(appointment_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_log_created ON consult_audit_logs(created_at)")

    defaults = [
        ("portal_name", "JHIMS Consultation"),
        ("support_phone", "050 805 0007"),
        ("support_email", "jacksmeg99@gmail.com"),
        ("consultation_window_minutes", "30"),
    ]
    for key, value in defaults:
        c.execute(
            """INSERT INTO consult_portal_settings(setting_key, setting_value, updated_at)
               VALUES(?,?,?)
               ON CONFLICT(setting_key) DO NOTHING""",
            (key, value, _now_text()),
        )
    conn.commit()


def current_patient():
    pid = session.get("consult_patient_id")
    if not pid:
        return None
    return get_db().execute("SELECT * FROM consult_patients WHERE id=?", (pid,)).fetchone()


def current_doctor():
    did = session.get("consult_doctor_id")
    if not did:
        return None
    return get_db().execute("SELECT * FROM consult_doctors WHERE id=?", (did,)).fetchone()


def patient_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("consult_patient_id"):
            flash("Please log in as patient to continue.", "warning")
            return redirect(url_for("consult_patient_login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def doctor_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("consult_doctor_id"):
            flash("Please log in as doctor to continue.", "warning")
            return redirect(url_for("consult_doctor_login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def _render(template_name: str, **context):
    return render_template(
        template_name,
        public_url=PUBLIC_URL,
        visit_modes=VISIT_MODES,
        specialties=DOCTOR_SPECIALTIES,
        current_patient=current_patient(),
        current_doctor=current_doctor(),
        **context,
    )


@app.template_filter("fmt_dt")
def fmt_dt(value):
    raw = (value or "").strip()
    if not raw:
        return "-"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            continue
    return raw


@app.template_filter("fmt_date")
def fmt_date(value):
    raw = (value or "").strip()
    if not raw:
        return "-"
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except Exception:
        return raw


@app.before_request
def _bootstrap():
    init_db()


@app.route("/")
@app.route("/consultation")
def consultation_home():
    conn = get_db()
    total_appointments = conn.execute("SELECT COUNT(*) AS c FROM consult_appointments").fetchone()["c"]
    active_doctors = conn.execute("SELECT COUNT(*) AS c FROM consult_doctors WHERE status='active'").fetchone()["c"]
    completed_consults = conn.execute(
        "SELECT COUNT(*) AS c FROM consult_appointments WHERE status='Completed'"
    ).fetchone()["c"]
    upcoming_consults = conn.execute(
        "SELECT COUNT(*) AS c FROM consult_appointments WHERE status IN ('Requested','Confirmed','Reschedule Requested')"
    ).fetchone()["c"]

    upcoming_rows = conn.execute(
        """SELECT a.id, a.appointment_code, a.scheduled_for, a.status, a.visit_mode,
                  p.full_name AS patient_name, d.full_name AS doctor_name, d.specialty
           FROM consult_appointments a
           JOIN consult_patients p ON p.id=a.patient_id
           JOIN consult_doctors d ON d.id=a.doctor_id
           WHERE a.status IN ('Requested','Confirmed','Reschedule Requested')
           ORDER BY a.scheduled_for ASC, a.id ASC
           LIMIT 5"""
    ).fetchall()

    schedule_rows = conn.execute(
        """SELECT a.id, a.scheduled_for, a.status, p.full_name AS patient_name
           FROM consult_appointments a
           JOIN consult_patients p ON p.id=a.patient_id
           WHERE a.status IN ('Confirmed','Reschedule Requested')
           ORDER BY a.scheduled_for ASC, a.id ASC
           LIMIT 5"""
    ).fetchall()

    quick_doctors = conn.execute(
        """SELECT id, full_name, specialty
           FROM consult_doctors
           WHERE status='active'
           ORDER BY full_name ASC
           LIMIT 30"""
    ).fetchall()

    recent_booked = conn.execute(
        "SELECT created_at FROM consult_appointments ORDER BY id DESC LIMIT 1"
    ).fetchone()
    recent_scheduled = conn.execute(
        "SELECT updated_at, created_at FROM consult_appointments WHERE status='Confirmed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    recent_in_progress = conn.execute(
        "SELECT updated_at, created_at FROM consult_appointments WHERE status='Reschedule Requested' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    recent_completed = conn.execute(
        "SELECT updated_at, created_at FROM consult_appointments WHERE status='Completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    timeline = [
        {
            "label": "Appointment Booked",
            "stamp": (recent_booked["created_at"] if recent_booked else ""),
            "active": bool(recent_booked),
        },
        {
            "label": "Consultation Scheduled",
            "stamp": ((recent_scheduled["updated_at"] or recent_scheduled["created_at"]) if recent_scheduled else ""),
            "active": bool(recent_scheduled),
        },
        {
            "label": "Consultation in Progress",
            "stamp": ((recent_in_progress["updated_at"] or recent_in_progress["created_at"]) if recent_in_progress else ""),
            "active": bool(recent_in_progress),
        },
        {
            "label": "Completed",
            "stamp": ((recent_completed["updated_at"] or recent_completed["created_at"]) if recent_completed else ""),
            "active": bool(recent_completed),
        },
    ]

    today_view = {
        "appointments": total_appointments,
        "active_doctors": active_doctors,
        "completed": completed_consults,
        "rating": "4.8",
        "upcoming": upcoming_consults,
    }
    return _render(
        "consultation_home.html",
        today_date=_date_text(),
        current_year=datetime.now().year,
        today_view=today_view,
        quick_doctors=quick_doctors,
        schedule_rows=schedule_rows,
        upcoming_rows=upcoming_rows,
        timeline=timeline,
    )


@app.route("/consultation/healthz")
def consultation_healthz():
    return {"ok": True, "service": "jhims-consultation-portal", "date": _now_text(), "public_url": PUBLIC_URL}


def _module_dataset(slug: str, patient, doctor):
    conn = get_db()
    slug = (slug or "").strip().lower()
    data = {"slug": slug}

    if slug == "medical-records":
        where = ["a.status='Completed'"]
        params = []
        if patient:
            where.append("a.patient_id=?")
            params.append(patient["id"])
        records = conn.execute(
            f"""SELECT a.appointment_code, a.scheduled_for, a.diagnosis, a.prescription, a.doctor_notes,
                       p.full_name AS patient_name, d.full_name AS doctor_name, d.specialty
                FROM consult_appointments a
                JOIN consult_patients p ON p.id=a.patient_id
                JOIN consult_doctors d ON d.id=a.doctor_id
                WHERE {' AND '.join(where)}
                ORDER BY a.scheduled_for DESC, a.id DESC
                LIMIT 120""",
            params,
        ).fetchall()
        data["records"] = records
        data["kpis"] = {
            "completed_records": len(records),
            "with_diagnosis": len([r for r in records if (r["diagnosis"] or "").strip()]),
            "with_prescription": len([r for r in records if (r["prescription"] or "").strip()]),
        }

    elif slug == "e-prescriptions":
        where = ["a.status='Completed'", "TRIM(COALESCE(a.prescription,''))<>''"]
        params = []
        if patient:
            where.append("a.patient_id=?")
            params.append(patient["id"])
        rows = conn.execute(
            f"""SELECT a.appointment_code, a.scheduled_for, a.prescription, a.diagnosis, a.follow_up_date,
                       p.full_name AS patient_name, d.full_name AS doctor_name
                FROM consult_appointments a
                JOIN consult_patients p ON p.id=a.patient_id
                JOIN consult_doctors d ON d.id=a.doctor_id
                WHERE {' AND '.join(where)}
                ORDER BY a.scheduled_for DESC, a.id DESC
                LIMIT 120""",
            params,
        ).fetchall()
        data["prescriptions"] = rows
        data["kpis"] = {
            "total_prescriptions": len(rows),
            "with_follow_up": len([r for r in rows if (r["follow_up_date"] or "").strip()]),
        }

    elif slug == "lab-results":
        where = ["1=1"]
        params = []
        if patient:
            where.append("lr.patient_id=?")
            params.append(patient["id"])
        elif doctor:
            where.append("(lr.doctor_id=? OR lr.patient_id IN (SELECT patient_id FROM consult_appointments WHERE doctor_id=?))")
            params.extend([doctor["id"], doctor["id"]])
        rows = conn.execute(
            f"""SELECT lr.*, p.full_name AS patient_name, d.full_name AS doctor_name
                FROM consult_lab_results lr
                JOIN consult_patients p ON p.id=lr.patient_id
                LEFT JOIN consult_doctors d ON d.id=lr.doctor_id
                WHERE {' AND '.join(where)}
                ORDER BY lr.created_at DESC, lr.id DESC
                LIMIT 120""",
            params,
        ).fetchall()
        if doctor:
            patient_options = conn.execute(
                """SELECT DISTINCT p.id, p.full_name
                   FROM consult_appointments a
                   JOIN consult_patients p ON p.id=a.patient_id
                   WHERE a.doctor_id=?
                   ORDER BY p.full_name ASC""",
                (doctor["id"],),
            ).fetchall()
        elif patient:
            patient_options = [patient]
        else:
            patient_options = conn.execute("SELECT id, full_name FROM consult_patients ORDER BY full_name ASC LIMIT 200").fetchall()

        doctor_options = conn.execute(
            "SELECT id, full_name, specialty FROM consult_doctors WHERE status='active' ORDER BY full_name ASC LIMIT 200"
        ).fetchall()
        data["lab_rows"] = rows
        data["patient_options"] = patient_options
        data["doctor_options"] = doctor_options
        data["can_add"] = bool(patient or doctor)

    elif slug in ("payments", "billing"):
        pay_where = ["1=1"]
        pay_params = []
        if patient:
            pay_where.append("pay.patient_id=?")
            pay_params.append(patient["id"])
        rows = conn.execute(
            f"""SELECT pay.*, p.full_name AS patient_name, a.appointment_code, a.reason, a.scheduled_for, d.full_name AS doctor_name
                FROM consult_payments pay
                JOIN consult_patients p ON p.id=pay.patient_id
                LEFT JOIN consult_appointments a ON a.id=pay.appointment_id
                LEFT JOIN consult_doctors d ON d.id=a.doctor_id
                WHERE {' AND '.join(pay_where)}
                ORDER BY pay.created_at DESC, pay.id DESC
                LIMIT 200""",
            pay_params,
        ).fetchall()
        total_paid = sum([_safe_float(r["amount"], 0.0) for r in rows if (r["status"] or "").lower() == "paid"])
        by_method = {}
        for r in rows:
            method = (r["method"] or "Other").strip() or "Other"
            by_method[method] = by_method.get(method, 0) + _safe_float(r["amount"], 0.0)

        if patient:
            due_appointments = conn.execute(
                """SELECT a.id, a.appointment_code, a.reason, a.scheduled_for, d.full_name AS doctor_name, d.consultation_fee
                   FROM consult_appointments a
                   JOIN consult_doctors d ON d.id=a.doctor_id
                   WHERE a.patient_id=? AND a.status IN ('Requested','Confirmed','Reschedule Requested','Completed')
                   ORDER BY a.scheduled_for DESC, a.id DESC
                   LIMIT 120""",
                (patient["id"],),
            ).fetchall()
        else:
            due_appointments = []

        data["payments"] = rows
        data["total_paid"] = total_paid
        data["method_totals"] = sorted(by_method.items(), key=lambda x: x[0].lower())
        data["due_appointments"] = due_appointments
        data["can_pay"] = bool(patient)

    elif slug == "patients":
        where = ["1=1"]
        params = []
        if doctor:
            where.append("a.doctor_id=?")
            params.append(doctor["id"])
        rows = conn.execute(
            f"""SELECT p.id, p.full_name, p.phone, p.email, p.created_at, p.last_login_at,
                       COUNT(a.id) AS total_consults,
                       SUM(CASE WHEN a.status='Completed' THEN 1 ELSE 0 END) AS completed_consults
                FROM consult_patients p
                LEFT JOIN consult_appointments a ON a.patient_id=p.id
                WHERE {' AND '.join(where)}
                GROUP BY p.id, p.full_name, p.phone, p.email, p.created_at, p.last_login_at
                ORDER BY total_consults DESC, p.full_name ASC
                LIMIT 300""",
            params,
        ).fetchall()
        data["patients"] = rows

    elif slug == "consultations":
        status_filter = (request.args.get("status", "") or "").strip()
        where = ["1=1"]
        params = []
        if doctor:
            where.append("a.doctor_id=?")
            params.append(doctor["id"])
        if patient:
            where.append("a.patient_id=?")
            params.append(patient["id"])
        if status_filter:
            where.append("a.status=?")
            params.append(status_filter)

        rows = conn.execute(
            f"""SELECT a.*, p.full_name AS patient_name, d.full_name AS doctor_name, d.specialty
                FROM consult_appointments a
                JOIN consult_patients p ON p.id=a.patient_id
                JOIN consult_doctors d ON d.id=a.doctor_id
                WHERE {' AND '.join(where)}
                ORDER BY a.scheduled_for DESC, a.id DESC
                LIMIT 300""",
            params,
        ).fetchall()
        status_counts = conn.execute(
            """SELECT status, COUNT(*) AS c
               FROM consult_appointments
               GROUP BY status
               ORDER BY c DESC"""
        ).fetchall()
        data["consultations"] = rows
        data["status_counts"] = status_counts
        data["status_filter"] = status_filter

    elif slug == "reports":
        status_counts = conn.execute(
            """SELECT status, COUNT(*) AS c
               FROM consult_appointments
               GROUP BY status
               ORDER BY c DESC"""
        ).fetchall()
        specialty_counts = conn.execute(
            """SELECT specialty, COUNT(*) AS c
               FROM consult_doctors
               WHERE status='active'
               GROUP BY specialty
               ORDER BY c DESC, specialty ASC"""
        ).fetchall()
        daily = conn.execute(
            """SELECT substr(created_at,1,10) AS day, COUNT(*) AS c
               FROM consult_appointments
               GROUP BY day
               ORDER BY day DESC
               LIMIT 14"""
        ).fetchall()
        daily = list(reversed(daily))
        data["status_counts"] = status_counts
        data["specialty_counts"] = specialty_counts
        data["daily_counts"] = daily

    elif slug == "users-roles":
        data["counts"] = {
            "patients": conn.execute("SELECT COUNT(*) AS c FROM consult_patients").fetchone()["c"],
            "doctors": conn.execute("SELECT COUNT(*) AS c FROM consult_doctors").fetchone()["c"],
            "appointments": conn.execute("SELECT COUNT(*) AS c FROM consult_appointments").fetchone()["c"],
        }
        data["roles"] = [
            {"role": "Patient", "permissions": "Book appointments, access records, send messages"},
            {"role": "Doctor", "permissions": "Manage consultations, update notes, manage schedule"},
            {"role": "Admin", "permissions": "Configure modules, monitor billing, audit system logs"},
        ]

    elif slug == "departments":
        rows = conn.execute(
            """SELECT specialty AS department, COUNT(*) AS doctors
               FROM consult_doctors
               WHERE status='active'
               GROUP BY specialty
               ORDER BY doctors DESC, department ASC"""
        ).fetchall()
        data["departments"] = rows

    elif slug == "settings":
        rows = conn.execute(
            "SELECT setting_key, setting_value, updated_at FROM consult_portal_settings ORDER BY setting_key ASC"
        ).fetchall()
        data["settings"] = rows
        data["can_edit"] = bool(doctor)

    elif slug == "audit-logs":
        where = ["1=1"]
        params = []
        if patient:
            where.append("actor_role='patient' AND actor_id=?")
            params.append(patient["id"])
        rows = conn.execute(
            f"""SELECT * FROM consult_audit_logs
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC, id DESC
                LIMIT 200""",
            params,
        ).fetchall()
        data["logs"] = rows

    return data


@app.route("/consultation/modules/<slug>", methods=["GET", "POST"])
def consult_module_page(slug):
    module = MODULE_PAGES.get((slug or "").strip().lower())
    if not module:
        flash("Requested module was not found.", "warning")
        return redirect(url_for("consultation_home"))
    module_slug = (slug or "").strip().lower()
    patient = current_patient()
    doctor = current_doctor()

    if request.method == "POST":
        action = (request.form.get("action", "") or "").strip().lower()
        conn = get_db()
        if module_slug == "lab-results" and action == "add_lab_result":
            if not (patient or doctor):
                flash("Please log in as patient or doctor to add lab results.", "warning")
                return redirect(url_for("consult_patient_login", next=url_for("consult_module_page", slug=module_slug)))

            if patient:
                patient_id = patient["id"]
            else:
                patient_id = _safe_int(request.form.get("patient_id", "0"), 0)

            doctor_id = doctor["id"] if doctor else _safe_int(request.form.get("doctor_id", "0"), 0)
            report_type = (request.form.get("report_type", "") or "").strip()
            summary = (request.form.get("summary", "") or "").strip()
            result_link = (request.form.get("result_link", "") or "").strip()
            status = (request.form.get("status", "") or "").strip() or "Pending Review"

            valid_patient = conn.execute("SELECT id FROM consult_patients WHERE id=?", (patient_id,)).fetchone()
            valid_doctor = conn.execute("SELECT id FROM consult_doctors WHERE id=?", (doctor_id,)).fetchone() if doctor_id else None
            if not valid_patient or not report_type or not summary:
                flash("Patient, report type, and summary are required for lab result entry.", "danger")
            else:
                conn.execute(
                    """INSERT INTO consult_lab_results(
                           patient_id, doctor_id, report_type, summary, result_link, status, created_at, updated_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        valid_patient["id"],
                        valid_doctor["id"] if valid_doctor else None,
                        report_type,
                        summary,
                        result_link,
                        status,
                        _now_text(),
                        _now_text(),
                    ),
                )
                conn.commit()
                _audit_log("lab_result_added", target=f"patient:{valid_patient['id']}", details=report_type)
                flash("Lab result entry added successfully.", "success")

        elif module_slug in ("payments", "billing") and action == "add_payment":
            if not patient:
                flash("Patient login is required to create a payment entry.", "warning")
                return redirect(url_for("consult_patient_login", next=url_for("consult_module_page", slug=module_slug)))

            appointment_id = _safe_int(request.form.get("appointment_id", "0"), 0)
            amount = _safe_float(request.form.get("amount", "0"), 0.0)
            method = (request.form.get("method", "") or "").strip()
            notes = (request.form.get("notes", "") or "").strip()
            if amount <= 0 or not method:
                flash("Payment amount and method are required.", "danger")
            else:
                if appointment_id:
                    owned = conn.execute(
                        "SELECT id FROM consult_appointments WHERE id=? AND patient_id=?",
                        (appointment_id, patient["id"]),
                    ).fetchone()
                    if not owned:
                        flash("Selected appointment is invalid for this patient.", "danger")
                        return redirect(url_for("consult_module_page", slug=module_slug))
                else:
                    appointment_id = None
                tx_ref = _generate_tx_ref()
                conn.execute(
                    """INSERT INTO consult_payments(
                           appointment_id, patient_id, amount, currency, method, status, tx_ref, notes, created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (appointment_id, patient["id"], amount, "GHS", method, "Paid", tx_ref, notes, _now_text()),
                )
                conn.commit()
                _audit_log("payment_recorded", target=f"tx:{tx_ref}", details=f"amount={amount}")
                flash("Payment recorded successfully.", "success")

        elif module_slug == "settings" and action == "update_setting":
            if not doctor:
                flash("Doctor/admin login is required to update settings.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            setting_key = (request.form.get("setting_key", "") or "").strip()
            setting_value = (request.form.get("setting_value", "") or "").strip()
            if not setting_key:
                flash("Setting key is required.", "danger")
            else:
                _upsert_setting(setting_key, setting_value)
                _audit_log("setting_updated", target=setting_key, details=setting_value, actor_role="doctor", actor_id=doctor["id"])
                flash("Setting updated successfully.", "success")

        return redirect(url_for("consult_module_page", slug=module_slug))

    module_data = _module_dataset(module_slug, patient, doctor)
    return _render("consultation_module.html", module=module, module_slug=module_slug, module_data=module_data)


@app.route("/consultation/quick-book", methods=["POST"])
@patient_required
def consult_quick_book():
    patient = current_patient()
    conn = get_db()
    doctor_id = _safe_int(request.form.get("doctor_id", "0"), 0)
    scheduled_date = (request.form.get("scheduled_date", "") or "").strip()
    scheduled_time = (request.form.get("scheduled_time", "") or "").strip()
    visit_mode = (request.form.get("visit_mode", "") or "").strip()
    reason = (request.form.get("reason", "") or "").strip()
    symptoms = (request.form.get("symptoms", "") or "").strip()

    doctor = conn.execute("SELECT * FROM consult_doctors WHERE id=? AND status='active'", (doctor_id,)).fetchone()
    if not doctor:
        flash("Please select a valid active doctor for booking.", "danger")
        return redirect(url_for("consultation_home"))
    if not scheduled_date or not scheduled_time or not reason:
        flash("Select date, time, and reason to submit booking.", "danger")
        return redirect(url_for("consultation_home"))
    if visit_mode not in VISIT_MODES:
        flash("Please choose a valid consultation mode.", "danger")
        return redirect(url_for("consultation_home"))

    scheduled_for = f"{scheduled_date} {scheduled_time}:00"
    try:
        dt = datetime.strptime(scheduled_for, "%Y-%m-%d %H:%M:%S")
        if dt < datetime.now():
            flash("Please choose a future consultation date and time.", "danger")
            return redirect(url_for("consultation_home"))
    except Exception:
        flash("Invalid date/time format for booking.", "danger")
        return redirect(url_for("consultation_home"))

    code = _appointment_code()
    conn.execute(
        """INSERT INTO consult_appointments(
                appointment_code,patient_id,doctor_id,scheduled_for,visit_mode,reason,symptoms,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (code, patient["id"], doctor["id"], scheduled_for, visit_mode, reason, symptoms, "Requested", _now_text(), _now_text()),
    )
    conn.commit()
    appt = conn.execute("SELECT id FROM consult_appointments WHERE appointment_code=?", (code,)).fetchone()
    flash("Appointment booked successfully from quick booking panel.", "success")
    return redirect(url_for("consult_patient_appointment_detail", appointment_id=appt["id"]))


@app.route("/consultation/patient/signup", methods=["GET", "POST"])
def consult_patient_signup():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        date_of_birth = request.form.get("date_of_birth", "").strip()
        gender = request.form.get("gender", "").strip()

        if not full_name or not phone or not email or not password:
            flash("Full name, phone, email, and password are required.", "danger")
        elif not _valid_email(email):
            flash("Enter a valid email address.", "danger")
        elif not _valid_phone(phone):
            flash("Enter a valid phone number.", "danger")
        elif password != confirm_password:
            flash("Password and confirm password do not match.", "danger")
        else:
            policy_errors = _password_policy_errors(password, email=email, full_name=full_name)
            if policy_errors:
                flash(policy_errors[0], "danger")
                return _render("consultation_patient_signup.html")
            conn = get_db()
            existing = conn.execute("SELECT id FROM consult_patients WHERE email=?", (email,)).fetchone()
            if existing:
                flash("This patient email already exists. Please log in.", "warning")
                return redirect(url_for("consult_patient_login"))
            conn.execute(
                """INSERT INTO consult_patients(full_name,phone,email,password_hash,date_of_birth,gender,created_at,last_login_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (full_name, phone, email, generate_password_hash(password), date_of_birth, gender, _now_text(), _now_text()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM consult_patients WHERE email=?", (email,)).fetchone()
            session.clear()
            session["consult_patient_id"] = row["id"]
            _audit_log("patient_signup", target=f"patient:{row['id']}", details=f"email={email}", actor_role="patient", actor_id=row["id"])
            flash("Patient account created successfully. You can now book appointments.", "success")
            return redirect(url_for("consult_patient_dashboard"))
    return _render("consultation_patient_signup.html")


@app.route("/consultation/patient/login", methods=["GET", "POST"])
def consult_patient_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        row = get_db().execute("SELECT * FROM consult_patients WHERE email=?", (email,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            flash("Invalid patient login details.", "danger")
        else:
            get_db().execute("UPDATE consult_patients SET last_login_at=? WHERE id=?", (_now_text(), row["id"]))
            get_db().commit()
            session.clear()
            session["consult_patient_id"] = row["id"]
            flash(f"Welcome back, {row['full_name']}.", "success")
            return redirect(request.args.get("next") or url_for("consult_patient_dashboard"))
    return _render("consultation_patient_login.html")


@app.route("/consultation/patient/logout")
def consult_patient_logout():
    session.pop("consult_patient_id", None)
    flash("Patient session closed.", "info")
    return redirect(url_for("consultation_home"))


@app.route("/consultation/patient/change-password", methods=["GET", "POST"])
@patient_required
def consult_patient_change_password():
    patient = current_patient()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("Current password, new password, and confirm password are required.", "danger")
        elif not check_password_hash(patient["password_hash"], current_password):
            flash("Current password is incorrect.", "danger")
        elif new_password != confirm_password:
            flash("New password and confirm password do not match.", "danger")
        elif check_password_hash(patient["password_hash"], new_password):
            flash("New password must be different from current password.", "danger")
        else:
            policy_errors = _password_policy_errors(
                new_password,
                email=patient["email"],
                full_name=patient["full_name"],
            )
            if policy_errors:
                flash(policy_errors[0], "danger")
            else:
                conn = get_db()
                conn.execute(
                    "UPDATE consult_patients SET password_hash=?, last_login_at=? WHERE id=?",
                    (generate_password_hash(new_password), _now_text(), patient["id"]),
                )
                conn.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("consult_patient_dashboard"))

    password_rules = [
        "At least 8 characters",
        "At least one uppercase letter",
        "At least one lowercase letter",
        "At least one number",
        "At least one special character",
        "Should not contain your name or email name",
    ]
    return _render(
        "consultation_change_password.html",
        account_type="Patient",
        password_rules=password_rules,
    )


@app.route("/consultation/doctor/signup", methods=["GET", "POST"])
def consult_doctor_signup():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        specialty = request.form.get("specialty", "").strip()
        years_experience = _safe_int(request.form.get("years_experience", "0"), 0)
        consultation_fee = float(request.form.get("consultation_fee", "0") or 0)
        bio = request.form.get("bio", "").strip()
        available_days = request.form.get("available_days", "").strip()
        available_hours = request.form.get("available_hours", "").strip()

        if not full_name or not phone or not email or not password or not specialty:
            flash("Complete all required doctor fields.", "danger")
        elif specialty not in DOCTOR_SPECIALTIES:
            flash("Please choose a valid specialty.", "danger")
        elif not _valid_email(email):
            flash("Enter a valid email address.", "danger")
        elif not _valid_phone(phone):
            flash("Enter a valid phone number.", "danger")
        elif password != confirm_password:
            flash("Password and confirm password do not match.", "danger")
        else:
            policy_errors = _password_policy_errors(password, email=email, full_name=full_name)
            if policy_errors:
                flash(policy_errors[0], "danger")
                return _render("consultation_doctor_signup.html")
            conn = get_db()
            existing = conn.execute("SELECT id FROM consult_doctors WHERE email=?", (email,)).fetchone()
            if existing:
                flash("This doctor email already exists. Please log in.", "warning")
                return redirect(url_for("consult_doctor_login"))
            conn.execute(
                """INSERT INTO consult_doctors(
                        full_name,phone,email,password_hash,specialty,bio,years_experience,consultation_fee,
                        available_days,available_hours,created_at,last_login_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    full_name,
                    phone,
                    email,
                    generate_password_hash(password),
                    specialty,
                    bio,
                    years_experience,
                    consultation_fee,
                    available_days,
                    available_hours,
                    _now_text(),
                    _now_text(),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM consult_doctors WHERE email=?", (email,)).fetchone()
            session.clear()
            session["consult_doctor_id"] = row["id"]
            _audit_log("doctor_signup", target=f"doctor:{row['id']}", details=f"email={email}", actor_role="doctor", actor_id=row["id"])
            flash("Doctor account created. Keep profile complete so patients can trust and book you.", "success")
            return redirect(url_for("consult_doctor_dashboard"))
    return _render("consultation_doctor_signup.html")


@app.route("/consultation/doctor/login", methods=["GET", "POST"])
def consult_doctor_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        row = get_db().execute("SELECT * FROM consult_doctors WHERE email=?", (email,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            flash("Invalid doctor login details.", "danger")
        elif (row["status"] or "active").lower() != "active":
            flash("Doctor account is currently inactive.", "danger")
        else:
            get_db().execute("UPDATE consult_doctors SET last_login_at=? WHERE id=?", (_now_text(), row["id"]))
            get_db().commit()
            session.clear()
            session["consult_doctor_id"] = row["id"]
            flash(f"Welcome back, Dr. {row['full_name']}.", "success")
            return redirect(request.args.get("next") or url_for("consult_doctor_dashboard"))
    return _render("consultation_doctor_login.html")


@app.route("/consultation/doctor/logout")
def consult_doctor_logout():
    session.pop("consult_doctor_id", None)
    flash("Doctor session closed.", "info")
    return redirect(url_for("consultation_home"))


@app.route("/consultation/doctor/change-password", methods=["GET", "POST"])
@doctor_required
def consult_doctor_change_password():
    doctor = current_doctor()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("Current password, new password, and confirm password are required.", "danger")
        elif not check_password_hash(doctor["password_hash"], current_password):
            flash("Current password is incorrect.", "danger")
        elif new_password != confirm_password:
            flash("New password and confirm password do not match.", "danger")
        elif check_password_hash(doctor["password_hash"], new_password):
            flash("New password must be different from current password.", "danger")
        else:
            policy_errors = _password_policy_errors(
                new_password,
                email=doctor["email"],
                full_name=doctor["full_name"],
            )
            if policy_errors:
                flash(policy_errors[0], "danger")
            else:
                conn = get_db()
                conn.execute(
                    "UPDATE consult_doctors SET password_hash=?, last_login_at=? WHERE id=?",
                    (generate_password_hash(new_password), _now_text(), doctor["id"]),
                )
                conn.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("consult_doctor_dashboard"))

    password_rules = [
        "At least 8 characters",
        "At least one uppercase letter",
        "At least one lowercase letter",
        "At least one number",
        "At least one special character",
        "Should not contain your name or email name",
    ]
    return _render(
        "consultation_change_password.html",
        account_type="Doctor",
        password_rules=password_rules,
    )


@app.route("/consultation/patient/dashboard")
@patient_required
def consult_patient_dashboard():
    patient = current_patient()
    conn = get_db()
    appointments = conn.execute(
        """SELECT a.*, d.full_name AS doctor_name, d.specialty, d.consultation_fee
           FROM consult_appointments a
           JOIN consult_doctors d ON d.id=a.doctor_id
           WHERE a.patient_id=?
           ORDER BY a.scheduled_for DESC, a.id DESC""",
        (patient["id"],),
    ).fetchall()
    upcoming = [row for row in appointments if row["status"] in ("Requested", "Confirmed", "Reschedule Requested")]
    completed = [row for row in appointments if row["status"] == "Completed"]
    doctors = conn.execute(
        """SELECT * FROM consult_doctors
           WHERE status='active'
           ORDER BY COALESCE(last_login_at, created_at) DESC, full_name ASC
           LIMIT 8"""
    ).fetchall()
    return _render(
        "consultation_patient_dashboard.html",
        patient=patient,
        upcoming=upcoming,
        completed=completed,
        doctors=doctors,
    )


@app.route("/consultation/patient/doctors")
@patient_required
def consult_doctor_directory():
    q = request.args.get("q", "").strip()
    specialty = request.args.get("specialty", "").strip()
    conn = get_db()
    sql = """SELECT * FROM consult_doctors WHERE status='active'"""
    params = []
    if specialty:
        sql += " AND specialty=?"
        params.append(specialty)
    if q:
        sql += " AND (full_name LIKE ? OR specialty LIKE ? OR bio LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY full_name ASC"
    doctors = conn.execute(sql, params).fetchall()
    return _render("consultation_doctors.html", doctors=doctors, q=q, specialty=specialty)


@app.route("/consultation/patient/book/<int:doctor_id>", methods=["GET", "POST"])
@patient_required
def consult_book_appointment(doctor_id):
    conn = get_db()
    doctor = conn.execute("SELECT * FROM consult_doctors WHERE id=? AND status='active'", (doctor_id,)).fetchone()
    if not doctor:
        flash("Doctor profile not found or inactive.", "danger")
        return redirect(url_for("consult_doctor_directory"))

    if request.method == "POST":
        scheduled_date = request.form.get("scheduled_date", "").strip()
        scheduled_time = request.form.get("scheduled_time", "").strip()
        visit_mode = request.form.get("visit_mode", "").strip()
        reason = request.form.get("reason", "").strip()
        symptoms = request.form.get("symptoms", "").strip()

        if not scheduled_date or not scheduled_time or not reason:
            flash("Date, time, and reason are required.", "danger")
        elif visit_mode not in VISIT_MODES:
            flash("Please choose a valid consultation mode.", "danger")
        else:
            scheduled_for = f"{scheduled_date} {scheduled_time}:00"
            try:
                dt = datetime.strptime(scheduled_for, "%Y-%m-%d %H:%M:%S")
                if dt < datetime.now():
                    flash("Please select a future date/time.", "danger")
                    return _render("consultation_book.html", doctor=doctor, min_date=_date_text())
            except Exception:
                flash("Invalid date or time format.", "danger")
                return _render("consultation_book.html", doctor=doctor, min_date=_date_text())

            patient = current_patient()
            code = _appointment_code()
            conn.execute(
                """INSERT INTO consult_appointments(
                        appointment_code,patient_id,doctor_id,scheduled_for,visit_mode,reason,symptoms,status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (code, patient["id"], doctor["id"], scheduled_for, visit_mode, reason, symptoms, "Requested", _now_text(), _now_text()),
            )
            conn.commit()
            _audit_log(
                "appointment_requested",
                target=f"appointment:{code}",
                details=f"doctor_id={doctor['id']};patient_id={patient['id']};mode={visit_mode}",
                actor_role="patient",
                actor_id=patient["id"],
            )
            row = conn.execute("SELECT id FROM consult_appointments WHERE appointment_code=?", (code,)).fetchone()
            flash("Appointment request submitted successfully. Doctor will confirm shortly.", "success")
            return redirect(url_for("consult_patient_appointment_detail", appointment_id=row["id"]))

    return _render("consultation_book.html", doctor=doctor, min_date=_date_text())


def _load_patient_appointment_or_404(appointment_id, patient_id):
    row = get_db().execute(
        """SELECT a.*, d.full_name AS doctor_name, d.specialty, d.consultation_fee, d.meeting_provider
           FROM consult_appointments a
           JOIN consult_doctors d ON d.id=a.doctor_id
           WHERE a.id=? AND a.patient_id=?""",
        (appointment_id, patient_id),
    ).fetchone()
    return row


def _load_doctor_appointment_or_404(appointment_id, doctor_id):
    row = get_db().execute(
        """SELECT a.*, p.full_name AS patient_name, p.phone AS patient_phone, p.email AS patient_email
           FROM consult_appointments a
           JOIN consult_patients p ON p.id=a.patient_id
           WHERE a.id=? AND a.doctor_id=?""",
        (appointment_id, doctor_id),
    ).fetchone()
    return row


@app.route("/consultation/patient/appointment/<int:appointment_id>", methods=["GET", "POST"])
@patient_required
def consult_patient_appointment_detail(appointment_id):
    patient = current_patient()
    conn = get_db()
    appt = _load_patient_appointment_or_404(appointment_id, patient["id"])
    if not appt:
        flash("Appointment not found.", "danger")
        return redirect(url_for("consult_patient_dashboard"))

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "send_message":
            message = request.form.get("message", "").strip()
            if message:
                conn.execute(
                    """INSERT INTO consult_appointment_messages(appointment_id,sender_role,sender_id,sender_name,message,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (appt["id"], "patient", patient["id"], patient["full_name"], message, _now_text()),
                )
                conn.commit()
                flash("Message sent to doctor.", "success")
            else:
                flash("Message cannot be empty.", "danger")
        elif action == "cancel":
            reason = request.form.get("cancel_reason", "").strip()
            if appt["status"] in ("Completed", "Cancelled"):
                flash("This appointment can no longer be cancelled.", "warning")
            else:
                conn.execute(
                    """UPDATE consult_appointments
                       SET status='Cancelled', patient_cancel_reason=?, updated_at=?
                       WHERE id=?""",
                    (reason, _now_text(), appt["id"]),
                )
                conn.commit()
                _audit_log(
                    "appointment_cancelled_by_patient",
                    target=f"appointment:{appt['appointment_code']}",
                    details=reason or "no reason provided",
                    actor_role="patient",
                    actor_id=patient["id"],
                )
                flash("Appointment has been cancelled.", "info")
        return redirect(url_for("consult_patient_appointment_detail", appointment_id=appointment_id))

    messages = conn.execute(
        """SELECT * FROM consult_appointment_messages
           WHERE appointment_id=?
           ORDER BY id ASC""",
        (appointment_id,),
    ).fetchall()
    return _render("consultation_patient_appointment.html", patient=patient, appt=appt, messages=messages)


@app.route("/consultation/doctor/dashboard")
@doctor_required
def consult_doctor_dashboard():
    doctor = current_doctor()
    conn = get_db()
    appointments = conn.execute(
        """SELECT a.*, p.full_name AS patient_name, p.phone AS patient_phone
           FROM consult_appointments a
           JOIN consult_patients p ON p.id=a.patient_id
           WHERE a.doctor_id=?
           ORDER BY a.scheduled_for DESC, a.id DESC""",
        (doctor["id"],),
    ).fetchall()

    pending = [row for row in appointments if row["status"] == "Requested"]
    upcoming = [row for row in appointments if row["status"] in ("Confirmed", "Reschedule Requested")]
    completed = [row for row in appointments if row["status"] == "Completed"]

    return _render(
        "consultation_doctor_dashboard.html",
        doctor=doctor,
        pending=pending,
        upcoming=upcoming,
        completed=completed,
    )


@app.route("/consultation/doctor/profile", methods=["GET", "POST"])
@doctor_required
def consult_doctor_profile():
    doctor = current_doctor()
    conn = get_db()
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        specialty = request.form.get("specialty", "").strip()
        bio = request.form.get("bio", "").strip()
        years_experience = _safe_int(request.form.get("years_experience", "0"), 0)
        consultation_fee = float(request.form.get("consultation_fee", "0") or 0)
        available_days = request.form.get("available_days", "").strip()
        available_hours = request.form.get("available_hours", "").strip()
        meeting_provider = request.form.get("meeting_provider", "").strip()
        if not full_name or not phone or specialty not in DOCTOR_SPECIALTIES:
            flash("Please complete profile with valid details.", "danger")
        else:
            conn.execute(
                """UPDATE consult_doctors
                   SET full_name=?, phone=?, specialty=?, bio=?, years_experience=?, consultation_fee=?,
                       available_days=?, available_hours=?, meeting_provider=?
                   WHERE id=?""",
                (
                    full_name,
                    phone,
                    specialty,
                    bio,
                    years_experience,
                    consultation_fee,
                    available_days,
                    available_hours,
                    meeting_provider,
                    doctor["id"],
                ),
            )
            conn.commit()
            flash("Doctor profile updated successfully.", "success")
            return redirect(url_for("consult_doctor_profile"))
    doctor = current_doctor()
    return _render("consultation_doctor_profile.html", doctor=doctor)


@app.route("/consultation/doctor/appointment/<int:appointment_id>", methods=["GET", "POST"])
@doctor_required
def consult_doctor_appointment_detail(appointment_id):
    doctor = current_doctor()
    conn = get_db()
    appt = _load_doctor_appointment_or_404(appointment_id, doctor["id"])
    if not appt:
        flash("Appointment not found.", "danger")
        return redirect(url_for("consult_doctor_dashboard"))

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "send_message":
            message = request.form.get("message", "").strip()
            if message:
                conn.execute(
                    """INSERT INTO consult_appointment_messages(appointment_id,sender_role,sender_id,sender_name,message,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (appt["id"], "doctor", doctor["id"], doctor["full_name"], message, _now_text()),
                )
                conn.commit()
                flash("Message sent to patient.", "success")
            else:
                flash("Message cannot be empty.", "danger")
        elif action == "accept":
            conn.execute(
                "UPDATE consult_appointments SET status='Confirmed', updated_at=? WHERE id=?",
                (_now_text(), appt["id"]),
            )
            conn.commit()
            _audit_log(
                "appointment_confirmed_by_doctor",
                target=f"appointment:{appt['appointment_code']}",
                actor_role="doctor",
                actor_id=doctor["id"],
            )
            flash("Appointment confirmed.", "success")
        elif action == "reschedule":
            new_date = request.form.get("new_date", "").strip()
            new_time = request.form.get("new_time", "").strip()
            if not new_date or not new_time:
                flash("Provide both new date and time for reschedule.", "danger")
            else:
                try:
                    scheduled_for = f"{new_date} {new_time}:00"
                    datetime.strptime(scheduled_for, "%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        """UPDATE consult_appointments
                           SET scheduled_for=?, status='Reschedule Requested', updated_at=?
                           WHERE id=?""",
                        (scheduled_for, _now_text(), appt["id"]),
                    )
                    conn.commit()
                    _audit_log(
                        "appointment_rescheduled_by_doctor",
                        target=f"appointment:{appt['appointment_code']}",
                        details=f"scheduled_for={scheduled_for}",
                        actor_role="doctor",
                        actor_id=doctor["id"],
                    )
                    flash("Appointment moved to new schedule.", "success")
                except Exception:
                    flash("Invalid reschedule date/time.", "danger")
        elif action == "cancel":
            reason = request.form.get("doctor_cancel_reason", "").strip()
            conn.execute(
                """UPDATE consult_appointments
                   SET status='Cancelled', doctor_cancel_reason=?, updated_at=?
                   WHERE id=?""",
                (reason, _now_text(), appt["id"]),
            )
            conn.commit()
            _audit_log(
                "appointment_cancelled_by_doctor",
                target=f"appointment:{appt['appointment_code']}",
                details=reason or "no reason provided",
                actor_role="doctor",
                actor_id=doctor["id"],
            )
            flash("Appointment cancelled by doctor.", "warning")
        elif action == "complete":
            diagnosis = request.form.get("diagnosis", "").strip()
            prescription = request.form.get("prescription", "").strip()
            doctor_notes = request.form.get("doctor_notes", "").strip()
            follow_up_date = request.form.get("follow_up_date", "").strip()
            meeting_link = request.form.get("meeting_link", "").strip()
            conn.execute(
                """UPDATE consult_appointments
                   SET status='Completed', diagnosis=?, prescription=?, doctor_notes=?, follow_up_date=?, meeting_link=?, updated_at=?
                   WHERE id=?""",
                (diagnosis, prescription, doctor_notes, follow_up_date, meeting_link, _now_text(), appt["id"]),
            )
            conn.commit()
            _audit_log(
                "consultation_completed",
                target=f"appointment:{appt['appointment_code']}",
                details=f"follow_up_date={follow_up_date}",
                actor_role="doctor",
                actor_id=doctor["id"],
            )
            flash("Consultation marked as completed.", "success")
        return redirect(url_for("consult_doctor_appointment_detail", appointment_id=appointment_id))

    messages = conn.execute(
        """SELECT * FROM consult_appointment_messages
           WHERE appointment_id=?
           ORDER BY id ASC""",
        (appointment_id,),
    ).fetchall()
    return _render("consultation_doctor_appointment.html", doctor=doctor, appt=appt, messages=messages)


if __name__ == "__main__":
    with app.app_context():
        init_db()
    try:
        from waitress import serve

        print(f"[consultation] Starting Waitress on 0.0.0.0:{PORT}")
        print(f"[consultation] Public URL: {PUBLIC_URL}")
        serve(app, host="0.0.0.0", port=PORT, threads=12)
    except Exception as exc:
        print(f"[consultation] Waitress unavailable, falling back to Flask: {exc}")
        app.run(host="0.0.0.0", port=PORT, debug=False)
