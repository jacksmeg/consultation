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
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, g, flash, redirect, render_template, request, session, url_for, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("JHIMS_CONSULTATION_DB", os.path.join(BASE_DIR, "consultation_portal.db"))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
PUBLIC_URL = os.environ.get("JHIMS_CONSULTATION_PUBLIC_URL", "https://consultation.jhimssoftware.com")
PORT = int(os.environ.get("PORT") or os.environ.get("JHIMS_CONSULTATION_PORT", "5052"))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads", "consultation_docs")
ALLOWED_DOC_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp", "txt", "doc", "docx"}
DAYS_LOOKUP = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

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
    "video-room": {
        "title": "Video Consultation Room",
        "summary": "Secure in-browser video calls, live chat, and consultation notes per appointment.",
        "actions": [
            {"label": "Consultations", "endpoint": "consult_module_page", "kwargs": {"slug": "consultations"}},
            {"label": "Doctor Dashboard", "endpoint": "consult_doctor_dashboard"},
        ],
    },
    "availability": {
        "title": "Doctor Availability Engine",
        "summary": "Real time slot calendar with conflict prevention and smart reschedule suggestions.",
        "actions": [
            {"label": "Doctor Profile", "endpoint": "consult_doctor_profile"},
            {"label": "Consultations", "endpoint": "consult_module_page", "kwargs": {"slug": "consultations"}},
        ],
    },
    "admin-control": {
        "title": "Admin Control Center",
        "summary": "Approve doctors, manage departments, pricing, and module permissions in one place.",
        "actions": [
            {"label": "Users & Roles", "endpoint": "consult_module_page", "kwargs": {"slug": "users-roles"}},
            {"label": "System Settings", "endpoint": "consult_module_page", "kwargs": {"slug": "settings"}},
        ],
    },
    "notifications": {
        "title": "Notifications Center",
        "summary": "Manage WhatsApp, SMS, and email alerts for booking, reschedule, and follow-up events.",
        "actions": [
            {"label": "Consultations", "endpoint": "consult_module_page", "kwargs": {"slug": "consultations"}},
            {"label": "Audit Logs", "endpoint": "consult_module_page", "kwargs": {"slug": "audit-logs"}},
        ],
    },
    "analytics-pro": {
        "title": "Analytics Pro",
        "summary": "Doctor performance, no-show rate, revenue trends, and specialty demand analytics.",
        "actions": [
            {"label": "Reports", "endpoint": "consult_module_page", "kwargs": {"slug": "reports"}},
            {"label": "Billing", "endpoint": "consult_module_page", "kwargs": {"slug": "billing"}},
        ],
    },
    "mobile-pwa": {
        "title": "Patient Mobile Experience",
        "summary": "Progressive web app install mode for fast booking, updates, and notifications.",
        "actions": [
            {"label": "Patient Portal", "endpoint": "consult_patient_dashboard"},
            {"label": "Consultation Home", "endpoint": "consultation_home"},
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


def _generate_invoice_no() -> str:
    return f"INV-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def _normalize_dt(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    return ""


def _setting_value(setting_key: str, default: str = "") -> str:
    row = get_db().execute(
        "SELECT setting_value FROM consult_portal_settings WHERE setting_key=?",
        ((setting_key or "").strip(),),
    ).fetchone()
    if not row:
        return default
    return row["setting_value"] or default


def _slot_interval_minutes() -> int:
    return max(15, min(120, _safe_int(_setting_value("consultation_window_minutes", "30"), 30)))


def _doctor_has_conflict(doctor_id: int, scheduled_for: str, exclude_appointment_id: int | None = None) -> bool:
    params = [doctor_id, _normalize_dt(scheduled_for)]
    sql = """SELECT id FROM consult_appointments
             WHERE doctor_id=? AND scheduled_for=?
               AND status IN ('Requested','Confirmed','Reschedule Requested','In Progress')"""
    if exclude_appointment_id:
        sql += " AND id<>?"
        params.append(exclude_appointment_id)
    row = get_db().execute(sql, params).fetchone()
    return bool(row)


def _parse_available_days(raw_days: str) -> set[int]:
    if not (raw_days or "").strip():
        return set(range(0, 7))
    tokens = re.split(r"[,\s/|]+", raw_days.strip().lower())
    days = set()
    for token in tokens:
        if token in DAYS_LOOKUP:
            days.add(DAYS_LOOKUP[token])
    return days or set(range(0, 7))


def _parse_hour_ranges(raw_hours: str) -> list[tuple[str, str]]:
    text = (raw_hours or "").strip()
    if not text:
        return [("09:00", "17:00")]
    parts = [p.strip() for p in text.split(",") if p.strip()]
    ranges: list[tuple[str, str]] = []
    for part in parts:
        if "-" not in part:
            continue
        start, end = [x.strip() for x in part.split("-", 1)]
        try:
            datetime.strptime(start, "%H:%M")
            datetime.strptime(end, "%H:%M")
            ranges.append((start, end))
        except Exception:
            continue
    return ranges or [("09:00", "17:00")]


def _doctor_allows_datetime(doctor_row, dt_obj: datetime) -> bool:
    conn = get_db()
    slot_date = dt_obj.strftime("%Y-%m-%d")
    slot_time = dt_obj.strftime("%H:%M")
    manual = conn.execute(
        """SELECT is_available
           FROM consult_doctor_availability
           WHERE doctor_id=? AND slot_date=? AND slot_time=?""",
        (doctor_row["id"], slot_date, slot_time),
    ).fetchone()
    if manual:
        return bool(manual["is_available"])
    allowed_days = _parse_available_days(doctor_row["available_days"] or "")
    if dt_obj.weekday() not in allowed_days:
        return False
    hour_ranges = _parse_hour_ranges(doctor_row["available_hours"] or "")
    clock = dt_obj.strftime("%H:%M")
    for start_hour, end_hour in hour_ranges:
        if start_hour <= clock <= end_hour:
            return True
    return False


def _suggest_next_slots(doctor_row, start_dt: datetime | None = None, limit: int = 6) -> list[dict]:
    start_from = start_dt or datetime.now()
    conn = get_db()
    suggestions = []
    seen = set()

    manual_slots = conn.execute(
        """SELECT slot_date, slot_time
           FROM consult_doctor_availability
           WHERE doctor_id=? AND is_available=1
           ORDER BY slot_date ASC, slot_time ASC
           LIMIT 300""",
        (doctor_row["id"],),
    ).fetchall()
    for row in manual_slots:
        dt_raw = f"{row['slot_date']} {row['slot_time']}:00"
        normalized = _normalize_dt(dt_raw)
        if not normalized:
            continue
        dt_obj = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        if dt_obj < start_from:
            continue
        if _doctor_has_conflict(doctor_row["id"], normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        suggestions.append({"scheduled_for": normalized, "source": "manual"})
        if len(suggestions) >= limit:
            return suggestions

    allowed_days = _parse_available_days(doctor_row["available_days"] or "")
    hour_ranges = _parse_hour_ranges(doctor_row["available_hours"] or "")
    slot_minutes = _slot_interval_minutes()
    for day_offset in range(0, 45):
        base_day = (start_from + timedelta(days=day_offset)).date()
        if base_day.weekday() not in allowed_days:
            continue
        for start_hour, end_hour in hour_ranges:
            try:
                start_clock = datetime.strptime(start_hour, "%H:%M").time()
                end_clock = datetime.strptime(end_hour, "%H:%M").time()
            except Exception:
                continue
            candidate = datetime.combine(base_day, start_clock)
            day_end = datetime.combine(base_day, end_clock)
            while candidate < day_end:
                if candidate < start_from:
                    candidate += timedelta(minutes=slot_minutes)
                    continue
                normalized = candidate.strftime("%Y-%m-%d %H:%M:%S")
                if normalized not in seen and not _doctor_has_conflict(doctor_row["id"], normalized):
                    seen.add(normalized)
                    suggestions.append({"scheduled_for": normalized, "source": "availability"})
                    if len(suggestions) >= limit:
                        return suggestions
                candidate += timedelta(minutes=slot_minutes)
    return suggestions


def _queue_notification(
    channel: str,
    subject: str,
    message: str,
    recipient_role: str,
    recipient_id: int | None = None,
    appointment_id: int | None = None,
    scheduled_for: str | None = None,
):
    conn = get_db()
    conn.execute(
        """INSERT INTO consult_notifications(
               appointment_id, recipient_role, recipient_id, channel, subject,
               message, status, scheduled_for, sent_at, created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            appointment_id,
            (recipient_role or "patient").strip().lower(),
            recipient_id,
            (channel or "email").strip().lower(),
            (subject or "").strip(),
            (message or "").strip(),
            "Queued",
            _normalize_dt(scheduled_for or "") or None,
            None,
            _now_text(),
        ),
    )
    conn.commit()


def _queue_multi_channel_notification(
    recipient_role: str,
    recipient_id: int | None,
    subject: str,
    message: str,
    appointment_id: int | None = None,
):
    for channel in ("whatsapp", "sms", "email"):
        _queue_notification(channel, subject, message, recipient_role, recipient_id, appointment_id=appointment_id)


def _allowed_doc(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower().strip()
    return ext in ALLOWED_DOC_EXTENSIONS


def _ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def _create_video_room_for_appointment(appt_row):
    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM consult_video_sessions WHERE appointment_id=?",
        (appt_row["id"],),
    ).fetchone()
    if existing:
        return existing
    room_code = f"jhims-{(appt_row['appointment_code'] or secrets.token_hex(4)).lower().replace(' ', '-')}"
    room_url = f"https://meet.jit.si/{room_code}"
    token = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO consult_video_sessions(
               appointment_id, room_code, room_url, access_token, created_at, updated_at
           ) VALUES(?,?,?,?,?,?)""",
        (appt_row["id"], room_code, room_url, token, _now_text(), _now_text()),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM consult_video_sessions WHERE appointment_id=?",
        (appt_row["id"],),
    ).fetchone()


def _create_invoice_for_appointment(appt_id: int):
    conn = get_db()
    existing = conn.execute("SELECT id FROM consult_invoices WHERE appointment_id=?", (appt_id,)).fetchone()
    if existing:
        return
    appt = conn.execute(
        """SELECT a.id, a.patient_id, a.scheduled_for, d.consultation_fee
           FROM consult_appointments a
           JOIN consult_doctors d ON d.id=a.doctor_id
           WHERE a.id=?""",
        (appt_id,),
    ).fetchone()
    if not appt:
        return
    base_amount = max(_safe_float(appt["consultation_fee"], 0.0), 0.0)
    tax_amount = round(base_amount * 0.0, 2)
    total_amount = round(base_amount + tax_amount, 2)
    due_date = (appt["scheduled_for"] or "").split(" ")[0] if appt["scheduled_for"] else _date_text()
    conn.execute(
        """INSERT INTO consult_invoices(
               invoice_no, appointment_id, patient_id, amount, tax_amount, total_amount,
               status, due_date, issued_at, paid_at, created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _generate_invoice_no(),
            appt["id"],
            appt["patient_id"],
            base_amount,
            tax_amount,
            total_amount,
            "Pending",
            due_date,
            _now_text(),
            None,
            _now_text(),
            _now_text(),
        ),
    )
    conn.commit()


def _refresh_invoice_status_for_appointment(appt_id: int):
    conn = get_db()
    invoice = conn.execute("SELECT * FROM consult_invoices WHERE appointment_id=?", (appt_id,)).fetchone()
    if not invoice:
        return
    paid_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM consult_payments WHERE appointment_id=? AND lower(status)='paid'",
        (appt_id,),
    ).fetchone()["total"]
    due_total = _safe_float(invoice["total_amount"], 0.0)
    if paid_total >= due_total and due_total > 0:
        status = "Paid"
        paid_at = _now_text()
    elif paid_total > 0:
        status = "Partially Paid"
        paid_at = None
    else:
        status = "Pending"
        paid_at = None
    conn.execute(
        "UPDATE consult_invoices SET status=?, paid_at=?, updated_at=? WHERE id=?",
        (status, paid_at, _now_text(), invoice["id"]),
    )
    conn.commit()


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
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_video_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL UNIQUE,
            room_code TEXT NOT NULL,
            room_url TEXT NOT NULL,
            access_token TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(appointment_id) REFERENCES consult_appointments(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_appointment_notes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            author_role TEXT NOT NULL,
            author_id INTEGER,
            visibility TEXT NOT NULL DEFAULT 'private',
            note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(appointment_id) REFERENCES consult_appointments(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_doctor_availability(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            slot_date TEXT NOT NULL,
            slot_time TEXT NOT NULL,
            is_available INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            UNIQUE(doctor_id, slot_date, slot_time),
            FOREIGN KEY(doctor_id) REFERENCES consult_doctors(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_departments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_doctor_departments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            department_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(doctor_id, department_id),
            FOREIGN KEY(doctor_id) REFERENCES consult_doctors(id),
            FOREIGN KEY(department_id) REFERENCES consult_departments(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_module_permissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role_name TEXT NOT NULL,
            module_slug TEXT NOT NULL,
            can_view INTEGER NOT NULL DEFAULT 1,
            can_edit INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            UNIQUE(role_name, module_slug)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            recipient_role TEXT NOT NULL,
            recipient_id INTEGER,
            channel TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Queued',
            scheduled_for TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(appointment_id) REFERENCES consult_appointments(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_invoices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT NOT NULL UNIQUE,
            appointment_id INTEGER NOT NULL UNIQUE,
            patient_id INTEGER NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            tax_amount REAL NOT NULL DEFAULT 0,
            total_amount REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Pending',
            due_date TEXT,
            issued_at TEXT,
            paid_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(appointment_id) REFERENCES consult_appointments(id),
            FOREIGN KEY(patient_id) REFERENCES consult_patients(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_prescription_refills(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER,
            medication_summary TEXT NOT NULL,
            request_note TEXT,
            refill_status TEXT NOT NULL DEFAULT 'Requested',
            requested_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY(appointment_id) REFERENCES consult_appointments(id),
            FOREIGN KEY(patient_id) REFERENCES consult_patients(id),
            FOREIGN KEY(doctor_id) REFERENCES consult_doctors(id)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS consult_documents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER,
            category TEXT NOT NULL DEFAULT 'General',
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            mime_type TEXT,
            file_size INTEGER NOT NULL DEFAULT 0,
            uploaded_by_role TEXT NOT NULL,
            uploaded_by_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(appointment_id) REFERENCES consult_appointments(id),
            FOREIGN KEY(patient_id) REFERENCES consult_patients(id),
            FOREIGN KEY(doctor_id) REFERENCES consult_doctors(id)
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
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_video_appt ON consult_video_sessions(appointment_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_note_appt ON consult_appointment_notes(appointment_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_avail_doctor_date ON consult_doctor_availability(doctor_id, slot_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_notify_created ON consult_notifications(created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_notify_status ON consult_notifications(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_invoice_patient ON consult_invoices(patient_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_refill_appt ON consult_prescription_refills(appointment_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_doc_patient ON consult_documents(patient_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consult_doc_appt ON consult_documents(appointment_id)")

    defaults = [
        ("portal_name", "JHIMS Consultation"),
        ("support_phone", "050 805 0007"),
        ("support_email", "jacksmeg99@gmail.com"),
        ("consultation_window_minutes", "30"),
        ("default_currency", "GHS"),
        ("default_video_provider", "Jitsi"),
        ("billing_tax_rate", "0"),
    ]
    for key, value in defaults:
        c.execute(
            """INSERT INTO consult_portal_settings(setting_key, setting_value, updated_at)
               VALUES(?,?,?)
               ON CONFLICT(setting_key) DO NOTHING""",
            (key, value, _now_text()),
        )
    for specialty in DOCTOR_SPECIALTIES:
        c.execute(
            """INSERT INTO consult_departments(name, description, is_active, created_at)
               VALUES(?,?,1,?)
               ON CONFLICT(name) DO NOTHING""",
            (specialty, f"{specialty} consultation unit", _now_text()),
        )
    default_modules = list(MODULE_PAGES.keys())
    for role_name in ("patient", "doctor", "admin"):
        for module_slug in default_modules:
            can_edit = 1 if role_name in ("doctor", "admin") else 0
            can_view = 1
            if role_name == "patient" and module_slug in ("users-roles", "departments", "settings", "audit-logs", "admin-control"):
                can_view = 0
            c.execute(
                """INSERT INTO consult_module_permissions(role_name, module_slug, can_view, can_edit, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(role_name, module_slug) DO NOTHING""",
                (role_name, module_slug, can_view, can_edit, _now_text()),
            )
    _ensure_upload_dir()
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


def _current_role_name() -> str:
    if session.get("consult_doctor_id"):
        return "doctor"
    if session.get("consult_patient_id"):
        return "patient"
    return "guest"


def _can_access_module(module_slug: str, need_edit: bool = False) -> bool:
    role_name = _current_role_name()
    if role_name == "guest":
        return False
    row = get_db().execute(
        "SELECT can_view, can_edit FROM consult_module_permissions WHERE role_name=? AND module_slug=?",
        (role_name, (module_slug or "").strip().lower()),
    ).fetchone()
    if not row:
        return role_name == "doctor"
    return bool(row["can_edit"]) if need_edit else bool(row["can_view"])


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
        "SELECT COUNT(*) AS c FROM consult_appointments WHERE status IN ('Requested','Confirmed','Reschedule Requested','In Progress')"
    ).fetchone()["c"]

    upcoming_rows = conn.execute(
        """SELECT a.id, a.appointment_code, a.scheduled_for, a.status, a.visit_mode,
                  p.full_name AS patient_name, d.full_name AS doctor_name, d.specialty
           FROM consult_appointments a
           JOIN consult_patients p ON p.id=a.patient_id
           JOIN consult_doctors d ON d.id=a.doctor_id
           WHERE a.status IN ('Requested','Confirmed','Reschedule Requested','In Progress')
           ORDER BY a.scheduled_for ASC, a.id ASC
           LIMIT 5"""
    ).fetchall()

    schedule_rows = conn.execute(
        """SELECT a.id, a.scheduled_for, a.status, p.full_name AS patient_name
           FROM consult_appointments a
           JOIN consult_patients p ON p.id=a.patient_id
           WHERE a.status IN ('Confirmed','Reschedule Requested','In Progress')
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
        "SELECT updated_at, created_at FROM consult_appointments WHERE status='In Progress' ORDER BY id DESC LIMIT 1"
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
    data = {"slug": slug, "role_name": _current_role_name()}

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
        if doctor:
            where.append("a.doctor_id=?")
            params.append(doctor["id"])
        rows = conn.execute(
            f"""SELECT a.id, a.appointment_code, a.scheduled_for, a.prescription, a.diagnosis, a.follow_up_date,
                       p.full_name AS patient_name, d.full_name AS doctor_name, p.id AS patient_id, d.id AS doctor_id
                FROM consult_appointments a
                JOIN consult_patients p ON p.id=a.patient_id
                JOIN consult_doctors d ON d.id=a.doctor_id
                WHERE {' AND '.join(where)}
                ORDER BY a.scheduled_for DESC, a.id DESC
                LIMIT 120""",
            params,
        ).fetchall()
        refill_where = ["1=1"]
        refill_params = []
        if patient:
            refill_where.append("r.patient_id=?")
            refill_params.append(patient["id"])
        elif doctor:
            refill_where.append("r.doctor_id=?")
            refill_params.append(doctor["id"])
        refills = conn.execute(
            f"""SELECT r.*, a.appointment_code, p.full_name AS patient_name, d.full_name AS doctor_name
                FROM consult_prescription_refills r
                JOIN consult_appointments a ON a.id=r.appointment_id
                JOIN consult_patients p ON p.id=r.patient_id
                LEFT JOIN consult_doctors d ON d.id=r.doctor_id
                WHERE {' AND '.join(refill_where)}
                ORDER BY r.requested_at DESC, r.id DESC
                LIMIT 200""",
            refill_params,
        ).fetchall()
        data["prescriptions"] = rows
        data["refills"] = refills
        data["kpis"] = {
            "total_prescriptions": len(rows),
            "with_follow_up": len([r for r in rows if (r["follow_up_date"] or "").strip()]),
            "refill_requests": len(refills),
        }
        data["can_request_refill"] = bool(patient)
        data["can_resolve_refill"] = bool(doctor)

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
        doc_where = ["1=1"]
        doc_params = []
        if patient:
            doc_where.append("doc.patient_id=?")
            doc_params.append(patient["id"])
        elif doctor:
            doc_where.append("(doc.doctor_id=? OR doc.patient_id IN (SELECT patient_id FROM consult_appointments WHERE doctor_id=?))")
            doc_params.extend([doctor["id"], doctor["id"]])
        documents = conn.execute(
            f"""SELECT doc.*, p.full_name AS patient_name, d.full_name AS doctor_name, a.appointment_code
                FROM consult_documents doc
                JOIN consult_patients p ON p.id=doc.patient_id
                LEFT JOIN consult_doctors d ON d.id=doc.doctor_id
                LEFT JOIN consult_appointments a ON a.id=doc.appointment_id
                WHERE {' AND '.join(doc_where)}
                ORDER BY doc.created_at DESC, doc.id DESC
                LIMIT 160""",
            doc_params,
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
        appointment_options = []
        if patient:
            appointment_options = conn.execute(
                """SELECT id, appointment_code, scheduled_for
                   FROM consult_appointments
                   WHERE patient_id=?
                   ORDER BY scheduled_for DESC, id DESC
                   LIMIT 120""",
                (patient["id"],),
            ).fetchall()
        elif doctor:
            appointment_options = conn.execute(
                """SELECT id, appointment_code, scheduled_for
                   FROM consult_appointments
                   WHERE doctor_id=?
                   ORDER BY scheduled_for DESC, id DESC
                   LIMIT 120""",
                (doctor["id"],),
            ).fetchall()
        data["lab_rows"] = rows
        data["documents"] = documents
        data["patient_options"] = patient_options
        data["doctor_options"] = doctor_options
        data["appointment_options"] = appointment_options
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
                LIMIT 260""",
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
                   WHERE a.patient_id=? AND a.status IN ('Requested','Confirmed','Reschedule Requested','Completed','In Progress')
                   ORDER BY a.scheduled_for DESC, a.id DESC
                   LIMIT 120""",
                (patient["id"],),
            ).fetchall()
        else:
            due_appointments = conn.execute(
                """SELECT a.id, a.appointment_code, a.reason, a.scheduled_for, p.full_name AS patient_name, d.full_name AS doctor_name, d.consultation_fee
                   FROM consult_appointments a
                   JOIN consult_patients p ON p.id=a.patient_id
                   JOIN consult_doctors d ON d.id=a.doctor_id
                   ORDER BY a.scheduled_for DESC, a.id DESC
                   LIMIT 120"""
            ).fetchall()
        for appt in due_appointments:
            _create_invoice_for_appointment(appt["id"])
            _refresh_invoice_status_for_appointment(appt["id"])
        inv_where = ["1=1"]
        inv_params = []
        if patient:
            inv_where.append("inv.patient_id=?")
            inv_params.append(patient["id"])
        invoices = conn.execute(
            f"""SELECT inv.*, p.full_name AS patient_name, a.appointment_code, d.full_name AS doctor_name,
                       COALESCE((SELECT SUM(amount) FROM consult_payments pay WHERE pay.appointment_id=inv.appointment_id AND lower(pay.status)='paid'),0) AS paid_amount
                FROM consult_invoices inv
                JOIN consult_patients p ON p.id=inv.patient_id
                JOIN consult_appointments a ON a.id=inv.appointment_id
                JOIN consult_doctors d ON d.id=a.doctor_id
                WHERE {' AND '.join(inv_where)}
                ORDER BY inv.created_at DESC, inv.id DESC
                LIMIT 260""",
            inv_params,
        ).fetchall()
        outstanding_total = 0.0
        for inv in invoices:
            outstanding_total += max(_safe_float(inv["total_amount"], 0.0) - _safe_float(inv["paid_amount"], 0.0), 0.0)

        data["payments"] = rows
        data["invoices"] = invoices
        data["total_paid"] = total_paid
        data["outstanding_total"] = outstanding_total
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
                LIMIT 320""",
            params,
        ).fetchall()
        for row in rows[:40]:
            _create_invoice_for_appointment(row["id"])
            _refresh_invoice_status_for_appointment(row["id"])
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
        data["doctors"] = conn.execute(
            """SELECT id, full_name, specialty, status, verification_status, consultation_fee, created_at
               FROM consult_doctors
               ORDER BY created_at DESC, id DESC
               LIMIT 200"""
        ).fetchall()
        data["permissions"] = conn.execute(
            """SELECT role_name, module_slug, can_view, can_edit, updated_at
               FROM consult_module_permissions
               ORDER BY role_name ASC, module_slug ASC"""
        ).fetchall()
        data["can_manage"] = bool(doctor)

    elif slug == "departments":
        rows = conn.execute(
            """SELECT dep.id, dep.name AS department, dep.description, dep.is_active,
                      COUNT(dd.id) AS doctors
               FROM consult_departments dep
               LEFT JOIN consult_doctor_departments dd ON dd.department_id=dep.id
               GROUP BY dep.id, dep.name, dep.description, dep.is_active
               ORDER BY dep.name ASC"""
        ).fetchall()
        data["departments"] = rows
        data["doctor_department_rows"] = conn.execute(
            """SELECT d.id, d.full_name, d.specialty,
                      GROUP_CONCAT(dep.name, ', ') AS department_names
               FROM consult_doctors d
               LEFT JOIN consult_doctor_departments dd ON dd.doctor_id=d.id
               LEFT JOIN consult_departments dep ON dep.id=dd.department_id
               GROUP BY d.id, d.full_name, d.specialty
               ORDER BY d.full_name ASC"""
        ).fetchall()
        data["can_manage"] = bool(doctor)

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

    elif slug == "video-room":
        where = ["1=1"]
        params = []
        if doctor:
            where.append("a.doctor_id=?")
            params.append(doctor["id"])
        elif patient:
            where.append("a.patient_id=?")
            params.append(patient["id"])
        rows = conn.execute(
            f"""SELECT a.id, a.appointment_code, a.scheduled_for, a.status, a.visit_mode,
                       p.full_name AS patient_name, d.full_name AS doctor_name, d.specialty
                FROM consult_appointments a
                JOIN consult_patients p ON p.id=a.patient_id
                JOIN consult_doctors d ON d.id=a.doctor_id
                WHERE {' AND '.join(where)} AND a.status IN ('Requested','Confirmed','Reschedule Requested','In Progress','Completed')
                ORDER BY a.scheduled_for DESC, a.id DESC
                LIMIT 140""",
            params,
        ).fetchall()
        data["appointments"] = rows
        data["room_ready_count"] = len([r for r in rows if r["status"] in ("Confirmed", "Reschedule Requested", "In Progress", "Completed")])

    elif slug == "availability":
        data["slot_minutes"] = _slot_interval_minutes()
        if doctor:
            slots = conn.execute(
                """SELECT * FROM consult_doctor_availability
                   WHERE doctor_id=? AND is_available=1
                   ORDER BY slot_date ASC, slot_time ASC
                   LIMIT 300""",
                (doctor["id"],),
            ).fetchall()
            data["doctor_slots"] = slots
            data["suggested_slots"] = _suggest_next_slots(doctor, datetime.now(), limit=8)
            data["can_manage"] = True
        else:
            selected_doctor_id = _safe_int(request.args.get("doctor_id", "0"), 0)
            doctors = conn.execute(
                "SELECT id, full_name, specialty, available_days, available_hours FROM consult_doctors WHERE status='active' ORDER BY full_name ASC"
            ).fetchall()
            selected_doctor = None
            if selected_doctor_id:
                selected_doctor = conn.execute(
                    "SELECT id, full_name, specialty, available_days, available_hours FROM consult_doctors WHERE id=? AND status='active'",
                    (selected_doctor_id,),
                ).fetchone()
            data["doctors"] = doctors
            data["selected_doctor_id"] = selected_doctor_id
            data["selected_doctor"] = selected_doctor
            data["suggested_slots"] = _suggest_next_slots(selected_doctor, datetime.now(), limit=8) if selected_doctor else []
            data["can_manage"] = False

    elif slug == "admin-control":
        data["can_manage"] = bool(doctor)
        data["doctors"] = conn.execute(
            """SELECT id, full_name, specialty, status, verification_status, consultation_fee, created_at
               FROM consult_doctors
               ORDER BY created_at DESC, id DESC
               LIMIT 240"""
        ).fetchall()
        data["departments"] = conn.execute(
            "SELECT id, name, description, is_active FROM consult_departments ORDER BY name ASC"
        ).fetchall()
        data["permissions"] = conn.execute(
            """SELECT role_name, module_slug, can_view, can_edit, updated_at
               FROM consult_module_permissions
               ORDER BY role_name ASC, module_slug ASC"""
        ).fetchall()
        data["pricing"] = conn.execute(
            """SELECT setting_key, setting_value, updated_at
               FROM consult_portal_settings
               WHERE setting_key LIKE 'price_%' OR setting_key='default_currency'
               ORDER BY setting_key ASC"""
        ).fetchall()

    elif slug == "notifications":
        where = ["1=1"]
        params = []
        if patient:
            where.append("n.recipient_role='patient' AND n.recipient_id=?")
            params.append(patient["id"])
        elif doctor:
            where.append("(n.recipient_role='doctor' AND n.recipient_id=?) OR n.recipient_role='system'")
            params.append(doctor["id"])
        rows = conn.execute(
            f"""SELECT n.*, a.appointment_code
                FROM consult_notifications n
                LEFT JOIN consult_appointments a ON a.id=n.appointment_id
                WHERE {' AND '.join(where)}
                ORDER BY n.created_at DESC, n.id DESC
                LIMIT 260""",
            params,
        ).fetchall()
        data["notifications"] = rows
        data["stats"] = conn.execute(
            "SELECT channel, status, COUNT(*) AS c FROM consult_notifications GROUP BY channel, status ORDER BY channel ASC, status ASC"
        ).fetchall()
        data["can_manage"] = bool(doctor)

    elif slug == "analytics-pro":
        totals = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) AS completed,
                      SUM(CASE WHEN status='Cancelled' THEN 1 ELSE 0 END) AS cancelled
               FROM consult_appointments"""
        ).fetchone()
        total_appointments = _safe_int(totals["total"], 0)
        cancelled = _safe_int(totals["cancelled"], 0)
        no_show_rate = (cancelled / total_appointments * 100) if total_appointments else 0.0
        doctor_performance = conn.execute(
            """SELECT d.full_name, d.specialty,
                      COUNT(a.id) AS total_consults,
                      SUM(CASE WHEN a.status='Completed' THEN 1 ELSE 0 END) AS completed_consults,
                      ROUND(CASE WHEN COUNT(a.id)=0 THEN 0 ELSE (SUM(CASE WHEN a.status='Completed' THEN 1 ELSE 0 END)*100.0/COUNT(a.id)) END, 2) AS completion_rate
               FROM consult_doctors d
               LEFT JOIN consult_appointments a ON a.doctor_id=d.id
               GROUP BY d.id, d.full_name, d.specialty
               ORDER BY total_consults DESC, d.full_name ASC
               LIMIT 80"""
        ).fetchall()
        revenue_trends = conn.execute(
            """SELECT substr(created_at,1,10) AS day, ROUND(SUM(amount),2) AS revenue
               FROM consult_payments
               WHERE lower(status)='paid'
               GROUP BY day
               ORDER BY day DESC
               LIMIT 30"""
        ).fetchall()
        revenue_trends = list(reversed(revenue_trends))
        specialty_demand = conn.execute(
            """SELECT d.specialty, COUNT(a.id) AS demand
               FROM consult_appointments a
               JOIN consult_doctors d ON d.id=a.doctor_id
               GROUP BY d.specialty
               ORDER BY demand DESC, d.specialty ASC"""
        ).fetchall()
        total_revenue = _safe_float(
            conn.execute("SELECT COALESCE(SUM(amount),0) AS total FROM consult_payments WHERE lower(status)='paid'").fetchone()["total"],
            0.0,
        )
        data["kpis"] = {
            "total_appointments": total_appointments,
            "completed": _safe_int(totals["completed"], 0),
            "no_show_rate": round(no_show_rate, 2),
            "total_revenue": round(total_revenue, 2),
        }
        data["doctor_performance"] = doctor_performance
        data["revenue_trends"] = revenue_trends
        data["specialty_demand"] = specialty_demand

    elif slug == "mobile-pwa":
        data["install_ready"] = True
        data["manifest_url"] = url_for("consultation_manifest")
        data["service_worker_url"] = url_for("consultation_service_worker")
        data["quick_links"] = [
            {
                "label": "Book Appointment",
                "url": url_for("consult_doctor_directory")
                if patient
                else url_for("consult_patient_login", next=url_for("consult_doctor_directory")),
            },
            {
                "label": "My Dashboard",
                "url": url_for("consult_patient_dashboard") if patient else url_for("consult_patient_login"),
            },
            {"label": "Notifications", "url": url_for("consult_module_page", slug="notifications")},
        ]

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
    if not (patient or doctor):
        flash("Please log in to access consultation modules.", "warning")
        return redirect(url_for("consult_patient_login", next=url_for("consult_module_page", slug=module_slug)))
    if not _can_access_module(module_slug):
        flash("You do not have permission to access this module.", "danger")
        return redirect(url_for("consultation_home"))

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

        elif module_slug == "lab-results" and action == "add_document":
            if not (patient or doctor):
                flash("Please log in first to upload document.", "warning")
                return redirect(url_for("consult_patient_login", next=url_for("consult_module_page", slug=module_slug)))
            file_obj = request.files.get("document_file")
            category = (request.form.get("category", "") or "General").strip() or "General"
            appointment_id = _safe_int(request.form.get("appointment_id", "0"), 0)
            if not file_obj or not (file_obj.filename or "").strip():
                flash("Please choose a file to upload.", "danger")
            elif not _allowed_doc(file_obj.filename):
                flash("Unsupported file type. Allowed: pdf, png, jpg, jpeg, webp, txt, doc, docx.", "danger")
            else:
                if patient:
                    patient_id = patient["id"]
                    doctor_id = _safe_int(request.form.get("doctor_id", "0"), 0)
                else:
                    patient_id = _safe_int(request.form.get("patient_id", "0"), 0)
                    doctor_id = doctor["id"]
                valid_patient = conn.execute("SELECT id FROM consult_patients WHERE id=?", (patient_id,)).fetchone()
                valid_doctor = conn.execute("SELECT id FROM consult_doctors WHERE id=?", (doctor_id,)).fetchone() if doctor_id else None
                if not valid_patient:
                    flash("Valid patient is required for document upload.", "danger")
                else:
                    _ensure_upload_dir()
                    original_name = secure_filename(file_obj.filename)
                    unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:10]}-{original_name}"
                    file_path = os.path.join(UPLOAD_DIR, unique_name)
                    file_obj.save(file_path)
                    file_size = os.path.getsize(file_path)
                    conn.execute(
                        """INSERT INTO consult_documents(
                               appointment_id, patient_id, doctor_id, category, original_name, stored_name,
                               mime_type, file_size, uploaded_by_role, uploaded_by_id, created_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            appointment_id if appointment_id else None,
                            valid_patient["id"],
                            valid_doctor["id"] if valid_doctor else None,
                            category,
                            original_name,
                            unique_name,
                            (file_obj.mimetype or "").strip(),
                            file_size,
                            "doctor" if doctor else "patient",
                            doctor["id"] if doctor else patient["id"],
                            _now_text(),
                        ),
                    )
                    conn.commit()
                    _audit_log("document_uploaded", target=f"doc:{unique_name}", details=f"patient_id={valid_patient['id']}")
                    flash("Document uploaded successfully.", "success")

        elif module_slug == "e-prescriptions" and action == "request_refill":
            if not patient:
                flash("Patient login is required to request refill.", "warning")
                return redirect(url_for("consult_patient_login", next=url_for("consult_module_page", slug=module_slug)))
            appointment_id = _safe_int(request.form.get("appointment_id", "0"), 0)
            medication_summary = (request.form.get("medication_summary", "") or "").strip()
            request_note = (request.form.get("request_note", "") or "").strip()
            appt = conn.execute(
                "SELECT id, doctor_id FROM consult_appointments WHERE id=? AND patient_id=?",
                (appointment_id, patient["id"]),
            ).fetchone()
            if not appt or not medication_summary:
                flash("Appointment and medication summary are required for refill request.", "danger")
            else:
                conn.execute(
                    """INSERT INTO consult_prescription_refills(
                           appointment_id, patient_id, doctor_id, medication_summary, request_note, refill_status, requested_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (appt["id"], patient["id"], appt["doctor_id"], medication_summary, request_note, "Requested", _now_text()),
                )
                conn.commit()
                _queue_multi_channel_notification(
                    "doctor",
                    appt["doctor_id"],
                    "New refill request",
                    f"Patient requested refill for appointment #{appointment_id}.",
                    appointment_id=appt["id"],
                )
                _audit_log("refill_requested", target=f"appointment:{appointment_id}", details=medication_summary)
                flash("Refill request submitted.", "success")

        elif module_slug == "e-prescriptions" and action == "resolve_refill":
            if not doctor:
                flash("Doctor login is required to resolve refill requests.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            refill_id = _safe_int(request.form.get("refill_id", "0"), 0)
            refill_status = (request.form.get("refill_status", "") or "").strip() or "Approved"
            row = conn.execute(
                "SELECT id, patient_id, appointment_id FROM consult_prescription_refills WHERE id=? AND doctor_id=?",
                (refill_id, doctor["id"]),
            ).fetchone()
            if not row:
                flash("Refill request not found for this doctor.", "danger")
            else:
                conn.execute(
                    "UPDATE consult_prescription_refills SET refill_status=?, resolved_at=? WHERE id=?",
                    (refill_status, _now_text(), row["id"]),
                )
                conn.commit()
                _queue_multi_channel_notification(
                    "patient",
                    row["patient_id"],
                    "Refill request updated",
                    f"Your refill request status is now: {refill_status}.",
                    appointment_id=row["appointment_id"],
                )
                _audit_log("refill_resolved", target=f"refill:{row['id']}", details=refill_status)
                flash("Refill request updated.", "success")

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
                if appointment_id:
                    _refresh_invoice_status_for_appointment(appointment_id)
                _queue_multi_channel_notification(
                    "patient",
                    patient["id"],
                    "Payment received",
                    f"Payment {tx_ref} for GHS {amount:.2f} was recorded successfully.",
                    appointment_id=appointment_id,
                )
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

        elif module_slug == "availability" and action == "add_slot":
            if not doctor:
                flash("Doctor login is required to manage availability slots.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            slot_date = (request.form.get("slot_date", "") or "").strip()
            slot_time = (request.form.get("slot_time", "") or "").strip()
            if not slot_date or not slot_time:
                flash("Date and time are required for slot creation.", "danger")
            else:
                try:
                    datetime.strptime(f"{slot_date} {slot_time}:00", "%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        """INSERT INTO consult_doctor_availability(doctor_id, slot_date, slot_time, is_available, source, created_at)
                           VALUES(?,?,?,1,'manual',?)
                           ON CONFLICT(doctor_id, slot_date, slot_time) DO UPDATE SET is_available=1, source='manual'""",
                        (doctor["id"], slot_date, slot_time, _now_text()),
                    )
                    conn.commit()
                    _audit_log("doctor_slot_added", target=f"doctor:{doctor['id']}", details=f"{slot_date} {slot_time}", actor_role="doctor", actor_id=doctor["id"])
                    flash("Availability slot added.", "success")
                except Exception:
                    flash("Invalid slot date/time.", "danger")

        elif module_slug == "availability" and action == "remove_slot":
            if not doctor:
                flash("Doctor login is required to manage availability slots.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            slot_id = _safe_int(request.form.get("slot_id", "0"), 0)
            conn.execute("DELETE FROM consult_doctor_availability WHERE id=? AND doctor_id=?", (slot_id, doctor["id"]))
            conn.commit()
            _audit_log("doctor_slot_removed", target=f"slot:{slot_id}", actor_role="doctor", actor_id=doctor["id"])
            flash("Availability slot removed.", "info")

        elif module_slug in ("users-roles", "admin-control") and action == "update_doctor_status":
            if not doctor:
                flash("Doctor/admin login is required to update doctor status.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            doctor_id = _safe_int(request.form.get("doctor_id", "0"), 0)
            status = (request.form.get("status", "") or "active").strip()
            verification_status = (request.form.get("verification_status", "") or "pending").strip()
            conn.execute(
                "UPDATE consult_doctors SET status=?, verification_status=? WHERE id=?",
                (status, verification_status, doctor_id),
            )
            conn.commit()
            _audit_log("doctor_status_updated", target=f"doctor:{doctor_id}", details=f"{status}|{verification_status}", actor_role="doctor", actor_id=doctor["id"])
            flash("Doctor status updated.", "success")

        elif module_slug in ("users-roles", "admin-control") and action == "update_module_permission":
            if not doctor:
                flash("Doctor/admin login is required to update permissions.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            role_name = (request.form.get("role_name", "") or "").strip().lower()
            permission_module_slug = (request.form.get("permission_module_slug", "") or "").strip().lower()
            can_view = 1 if (request.form.get("can_view", "0") == "1") else 0
            can_edit = 1 if (request.form.get("can_edit", "0") == "1") else 0
            if role_name and permission_module_slug:
                conn.execute(
                    """INSERT INTO consult_module_permissions(role_name, module_slug, can_view, can_edit, updated_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(role_name, module_slug)
                       DO UPDATE SET can_view=excluded.can_view, can_edit=excluded.can_edit, updated_at=excluded.updated_at""",
                    (role_name, permission_module_slug, can_view, can_edit, _now_text()),
                )
                conn.commit()
                _audit_log(
                    "module_permission_updated",
                    target=f"{role_name}:{permission_module_slug}",
                    details=f"view={can_view},edit={can_edit}",
                    actor_role="doctor",
                    actor_id=doctor["id"],
                )
                flash("Module permission updated.", "success")
            else:
                flash("Role and module are required.", "danger")

        elif module_slug in ("departments", "admin-control") and action == "add_department":
            if not doctor:
                flash("Doctor/admin login is required to manage departments.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            name = (request.form.get("name", "") or "").strip()
            description = (request.form.get("description", "") or "").strip()
            if not name:
                flash("Department name is required.", "danger")
            else:
                conn.execute(
                    """INSERT INTO consult_departments(name, description, is_active, created_at)
                       VALUES(?,?,1,?)
                       ON CONFLICT(name) DO UPDATE SET description=excluded.description""",
                    (name, description, _now_text()),
                )
                conn.commit()
                _audit_log("department_saved", target=name, details=description, actor_role="doctor", actor_id=doctor["id"])
                flash("Department saved.", "success")

        elif module_slug in ("departments", "admin-control") and action == "assign_doctor_department":
            if not doctor:
                flash("Doctor/admin login is required to assign departments.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            doctor_id = _safe_int(request.form.get("doctor_id", "0"), 0)
            department_id = _safe_int(request.form.get("department_id", "0"), 0)
            if doctor_id and department_id:
                conn.execute(
                    """INSERT INTO consult_doctor_departments(doctor_id, department_id, created_at)
                       VALUES(?,?,?)
                       ON CONFLICT(doctor_id, department_id) DO NOTHING""",
                    (doctor_id, department_id, _now_text()),
                )
                conn.commit()
                _audit_log("doctor_department_assigned", target=f"doctor:{doctor_id}", details=f"department:{department_id}", actor_role="doctor", actor_id=doctor["id"])
                flash("Doctor assigned to department.", "success")
            else:
                flash("Doctor and department are required.", "danger")

        elif module_slug in ("admin-control", "billing", "settings") and action == "set_pricing":
            if not doctor:
                flash("Doctor/admin login is required to update pricing.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            service_key = (request.form.get("service_key", "") or "").strip().lower()
            amount = _safe_float(request.form.get("amount", "0"), 0.0)
            if not service_key or amount < 0:
                flash("Service key and valid amount are required.", "danger")
            else:
                _upsert_setting(f"price_{service_key}", f"{amount:.2f}")
                _audit_log("pricing_updated", target=service_key, details=f"{amount:.2f}", actor_role="doctor", actor_id=doctor["id"])
                flash("Pricing updated.", "success")

        elif module_slug == "notifications" and action == "queue_notification":
            if not doctor:
                flash("Doctor/admin login is required to queue notifications.", "warning")
                return redirect(url_for("consult_doctor_login", next=url_for("consult_module_page", slug=module_slug)))
            channel = (request.form.get("channel", "") or "email").strip().lower()
            recipient_role = (request.form.get("recipient_role", "") or "patient").strip().lower()
            recipient_id = _safe_int(request.form.get("recipient_id", "0"), 0)
            appointment_id = _safe_int(request.form.get("appointment_id", "0"), 0)
            subject = (request.form.get("subject", "") or "").strip()
            message = (request.form.get("message", "") or "").strip()
            if not subject or not message:
                flash("Subject and message are required.", "danger")
            else:
                _queue_notification(
                    channel,
                    subject,
                    message,
                    recipient_role,
                    recipient_id if recipient_id else None,
                    appointment_id=appointment_id if appointment_id else None,
                )
                _audit_log("notification_queued", target=channel, details=subject, actor_role="doctor", actor_id=doctor["id"])
                flash("Notification queued.", "success")

        return redirect(url_for("consult_module_page", slug=module_slug))

    module_data = _module_dataset(module_slug, patient, doctor)
    return _render("consultation_module.html", module=module, module_slug=module_slug, module_data=module_data)


@app.route("/consultation/docs/<int:doc_id>")
def consultation_download_document(doc_id):
    if not (session.get("consult_patient_id") or session.get("consult_doctor_id")):
        flash("Please login to access documents.", "warning")
        return redirect(url_for("consult_patient_login", next=url_for("consultation_download_document", doc_id=doc_id)))
    conn = get_db()
    row = conn.execute("SELECT * FROM consult_documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        flash("Document not found.", "danger")
        return redirect(url_for("consultation_home"))
    if session.get("consult_patient_id") and row["patient_id"] != _safe_int(session.get("consult_patient_id"), 0):
        flash("You are not allowed to access this document.", "danger")
        return redirect(url_for("consultation_home"))
    if session.get("consult_doctor_id"):
        did = _safe_int(session.get("consult_doctor_id"), 0)
        if row["doctor_id"] and row["doctor_id"] != did:
            is_consult_doctor = conn.execute(
                "SELECT id FROM consult_appointments WHERE id=? AND doctor_id=?",
                (row["appointment_id"], did),
            ).fetchone()
            if not is_consult_doctor:
                flash("You are not allowed to access this document.", "danger")
                return redirect(url_for("consultation_home"))
    return send_from_directory(UPLOAD_DIR, row["stored_name"], as_attachment=True, download_name=row["original_name"])


@app.route("/consultation/room/<int:appointment_id>", methods=["GET", "POST"])
def consultation_video_room(appointment_id):
    if not (session.get("consult_patient_id") or session.get("consult_doctor_id")):
        flash("Login required to access consultation room.", "warning")
        return redirect(url_for("consult_patient_login", next=url_for("consultation_video_room", appointment_id=appointment_id)))
    appt = _load_session_appointment(appointment_id)
    if not appt:
        flash("Appointment room access denied or appointment not found.", "danger")
        return redirect(url_for("consultation_home"))
    conn = get_db()
    if request.method == "POST":
        action = (request.form.get("action", "") or "").strip().lower()
        if action == "send_message":
            message = (request.form.get("message", "") or "").strip()
            if message:
                sender_role = "doctor" if session.get("consult_doctor_id") else "patient"
                sender_id = _safe_int(session.get("consult_doctor_id") or session.get("consult_patient_id"), 0)
                sender_name = (current_doctor()["full_name"] if sender_role == "doctor" else current_patient()["full_name"])
                conn.execute(
                    """INSERT INTO consult_appointment_messages(appointment_id, sender_role, sender_id, sender_name, message, created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (appt["id"], sender_role, sender_id, sender_name, message, _now_text()),
                )
                conn.commit()
        elif action == "add_note":
            note = (request.form.get("note", "") or "").strip()
            visibility = (request.form.get("visibility", "") or "private").strip().lower()
            if note:
                author_role = "doctor" if session.get("consult_doctor_id") else "patient"
                author_id = _safe_int(session.get("consult_doctor_id") or session.get("consult_patient_id"), 0)
                conn.execute(
                    """INSERT INTO consult_appointment_notes(appointment_id, author_role, author_id, visibility, note, created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (appt["id"], author_role, author_id, visibility if visibility in ("private", "shared") else "private", note, _now_text()),
                )
                conn.commit()
        elif action == "start_call" and session.get("consult_doctor_id"):
            conn.execute("UPDATE consult_appointments SET status='In Progress', updated_at=? WHERE id=?", (_now_text(), appt["id"]))
            conn.commit()
            _audit_log("consultation_started", target=f"appointment:{appt['appointment_code']}", actor_role="doctor", actor_id=_safe_int(session.get("consult_doctor_id"), 0))
        return redirect(url_for("consultation_video_room", appointment_id=appointment_id))

    video_room = _create_video_room_for_appointment(appt)
    messages = conn.execute(
        "SELECT * FROM consult_appointment_messages WHERE appointment_id=? ORDER BY id ASC",
        (appt["id"],),
    ).fetchall()
    if session.get("consult_doctor_id"):
        notes = conn.execute(
            "SELECT * FROM consult_appointment_notes WHERE appointment_id=? ORDER BY id DESC",
            (appt["id"],),
        ).fetchall()
    else:
        notes = conn.execute(
            "SELECT * FROM consult_appointment_notes WHERE appointment_id=? AND visibility='shared' ORDER BY id DESC",
            (appt["id"],),
        ).fetchall()
    return _render(
        "consultation_video_room.html",
        appt=appt,
        video_room=video_room,
        messages=messages,
        notes=notes,
        is_doctor=bool(session.get("consult_doctor_id")),
    )


@app.route("/consultation/prescription/<int:appointment_id>/print")
def consultation_print_prescription(appointment_id):
    if not (session.get("consult_patient_id") or session.get("consult_doctor_id")):
        flash("Login required to view printable prescription.", "warning")
        return redirect(url_for("consult_patient_login", next=url_for("consultation_print_prescription", appointment_id=appointment_id)))
    appt = _load_session_appointment(appointment_id)
    if not appt:
        flash("Prescription record not found.", "danger")
        return redirect(url_for("consultation_home"))
    if not (appt["prescription"] or "").strip():
        flash("No prescription has been issued for this appointment yet.", "warning")
        return redirect(url_for("consultation_home"))
    refill_history = get_db().execute(
        """SELECT * FROM consult_prescription_refills
           WHERE appointment_id=?
           ORDER BY requested_at DESC, id DESC""",
        (appointment_id,),
    ).fetchall()
    return _render("consultation_prescription_print.html", appt=appt, refill_history=refill_history)


@app.route("/consultation/manifest.webmanifest")
def consultation_manifest():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "consultation-manifest.webmanifest")


@app.route("/consultation/sw.js")
def consultation_service_worker():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "consultation-sw.js")


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
    if not _doctor_allows_datetime(doctor, dt):
        suggestions = _suggest_next_slots(doctor, dt, limit=3)
        suggestion_text = ", ".join([s["scheduled_for"] for s in suggestions]) if suggestions else ""
        if suggestion_text:
            flash(f"Doctor is unavailable for the selected slot. Try: {suggestion_text}", "warning")
        else:
            flash("Doctor is unavailable for the selected slot. Please choose another time.", "warning")
        return redirect(url_for("consultation_home"))
    if _doctor_has_conflict(doctor["id"], scheduled_for):
        suggestions = _suggest_next_slots(doctor, dt + timedelta(minutes=_slot_interval_minutes()), limit=3)
        suggestion_text = ", ".join([s["scheduled_for"] for s in suggestions]) if suggestions else ""
        if suggestion_text:
            flash(f"Selected slot is already booked. Try these slots: {suggestion_text}", "warning")
        else:
            flash("Selected slot is already booked. Please choose another time.", "warning")
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
    _create_invoice_for_appointment(appt["id"])
    _queue_multi_channel_notification(
        "patient",
        patient["id"],
        "Appointment booked",
        f"Your appointment {code} with Dr. {doctor['full_name']} is booked for {scheduled_for}.",
        appointment_id=appt["id"],
    )
    _queue_multi_channel_notification(
        "doctor",
        doctor["id"],
        "New appointment request",
        f"New patient booking {code} scheduled for {scheduled_for}.",
        appointment_id=appt["id"],
    )
    _audit_log("appointment_booked", target=f"appointment:{code}", details=f"doctor_id={doctor['id']}", actor_role="patient", actor_id=patient["id"])
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
            _queue_multi_channel_notification(
                "patient",
                row["id"],
                "Welcome to JHIMS Consultation",
                "Your patient account is ready. You can now book consultations and access your records.",
            )
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
            dep_row = conn.execute("SELECT id FROM consult_departments WHERE name=?", (specialty,)).fetchone()
            if dep_row:
                conn.execute(
                    """INSERT INTO consult_doctor_departments(doctor_id, department_id, created_at)
                       VALUES(?,?,?)
                       ON CONFLICT(doctor_id, department_id) DO NOTHING""",
                    (row["id"], dep_row["id"], _now_text()),
                )
                conn.commit()
            session.clear()
            session["consult_doctor_id"] = row["id"]
            _audit_log("doctor_signup", target=f"doctor:{row['id']}", details=f"email={email}", actor_role="doctor", actor_id=row["id"])
            _queue_notification(
                "email",
                "New doctor signup",
                f"Doctor {row['full_name']} ({row['email']}) registered and may require approval review.",
                "system",
                None,
            )
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
    upcoming = [row for row in appointments if row["status"] in ("Requested", "Confirmed", "Reschedule Requested", "In Progress")]
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


def _load_session_appointment(appointment_id):
    conn = get_db()
    where = ["a.id=?"]
    params = [appointment_id]
    if session.get("consult_doctor_id"):
        where.append("a.doctor_id=?")
        params.append(_safe_int(session.get("consult_doctor_id"), 0))
    elif session.get("consult_patient_id"):
        where.append("a.patient_id=?")
        params.append(_safe_int(session.get("consult_patient_id"), 0))
    else:
        return None
    return conn.execute(
        f"""SELECT a.*, p.full_name AS patient_name, p.email AS patient_email, p.phone AS patient_phone,
                   d.full_name AS doctor_name, d.specialty, d.meeting_provider
            FROM consult_appointments a
            JOIN consult_patients p ON p.id=a.patient_id
            JOIN consult_doctors d ON d.id=a.doctor_id
            WHERE {' AND '.join(where)}""",
        params,
    ).fetchone()


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
                _queue_multi_channel_notification(
                    "doctor",
                    appt["doctor_id"],
                    "New patient message",
                    f"Patient sent a new message on appointment {appt['appointment_code']}.",
                    appointment_id=appt["id"],
                )
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
                _queue_multi_channel_notification(
                    "doctor",
                    appt["doctor_id"],
                    "Appointment cancelled by patient",
                    f"Appointment {appt['appointment_code']} was cancelled by the patient.",
                    appointment_id=appt["id"],
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
    upcoming = [row for row in appointments if row["status"] in ("Confirmed", "Reschedule Requested", "In Progress")]
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
                _queue_multi_channel_notification(
                    "patient",
                    appt["patient_id"],
                    "New doctor message",
                    f"Doctor sent you a message on appointment {appt['appointment_code']}.",
                    appointment_id=appt["id"],
                )
                flash("Message sent to patient.", "success")
            else:
                flash("Message cannot be empty.", "danger")
        elif action == "accept":
            if _doctor_has_conflict(doctor["id"], appt["scheduled_for"], exclude_appointment_id=appt["id"]):
                suggestions = _suggest_next_slots(doctor, datetime.now(), limit=3)
                suggestion_text = ", ".join([s["scheduled_for"] for s in suggestions]) if suggestions else ""
                if suggestion_text:
                    flash(f"Cannot confirm due to slot conflict. Suggested slots: {suggestion_text}", "warning")
                else:
                    flash("Cannot confirm due to slot conflict.", "warning")
                return redirect(url_for("consult_doctor_appointment_detail", appointment_id=appointment_id))
            conn.execute(
                "UPDATE consult_appointments SET status='Confirmed', updated_at=? WHERE id=?",
                (_now_text(), appt["id"]),
            )
            conn.commit()
            _create_invoice_for_appointment(appt["id"])
            _audit_log(
                "appointment_confirmed_by_doctor",
                target=f"appointment:{appt['appointment_code']}",
                actor_role="doctor",
                actor_id=doctor["id"],
            )
            _queue_multi_channel_notification(
                "patient",
                appt["patient_id"],
                "Appointment confirmed",
                f"Your appointment {appt['appointment_code']} has been confirmed by the doctor.",
                appointment_id=appt["id"],
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
                    dt_new = datetime.strptime(scheduled_for, "%Y-%m-%d %H:%M:%S")
                    if dt_new <= datetime.now():
                        flash("Reschedule time must be in the future.", "danger")
                        return redirect(url_for("consult_doctor_appointment_detail", appointment_id=appointment_id))
                    if not _doctor_allows_datetime(doctor, dt_new):
                        suggestions = _suggest_next_slots(doctor, dt_new + timedelta(minutes=_slot_interval_minutes()), limit=3)
                        suggestion_text = ", ".join([s["scheduled_for"] for s in suggestions]) if suggestions else ""
                        if suggestion_text:
                            flash(f"That time is outside your availability window. Suggestions: {suggestion_text}", "warning")
                        else:
                            flash("That time is outside your availability window.", "warning")
                        return redirect(url_for("consult_doctor_appointment_detail", appointment_id=appointment_id))
                    if _doctor_has_conflict(doctor["id"], scheduled_for, exclude_appointment_id=appt["id"]):
                        suggestions = _suggest_next_slots(doctor, dt_new + timedelta(minutes=_slot_interval_minutes()), limit=3)
                        suggestion_text = ", ".join([s["scheduled_for"] for s in suggestions]) if suggestions else ""
                        if suggestion_text:
                            flash(f"Selected time conflicts with another appointment. Suggestions: {suggestion_text}", "warning")
                        else:
                            flash("Selected time conflicts with another appointment.", "warning")
                        return redirect(url_for("consult_doctor_appointment_detail", appointment_id=appointment_id))
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
                    _queue_multi_channel_notification(
                        "patient",
                        appt["patient_id"],
                        "Appointment rescheduled",
                        f"Your appointment {appt['appointment_code']} has a new time: {scheduled_for}.",
                        appointment_id=appt["id"],
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
            _queue_multi_channel_notification(
                "patient",
                appt["patient_id"],
                "Appointment cancelled",
                f"Your appointment {appt['appointment_code']} was cancelled by the doctor.",
                appointment_id=appt["id"],
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
            _create_invoice_for_appointment(appt["id"])
            _refresh_invoice_status_for_appointment(appt["id"])
            _audit_log(
                "consultation_completed",
                target=f"appointment:{appt['appointment_code']}",
                details=f"follow_up_date={follow_up_date}",
                actor_role="doctor",
                actor_id=doctor["id"],
            )
            _queue_multi_channel_notification(
                "patient",
                appt["patient_id"],
                "Consultation completed",
                f"Your consultation {appt['appointment_code']} has been completed. Review notes in your portal.",
                appointment_id=appt["id"],
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
