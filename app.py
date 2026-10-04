import io
import os
import re
import uuid
import calendar as pycal
from datetime import datetime, date, timedelta
from functools import wraps

import mysql.connector
from dotenv import load_dotenv
from flask import (
    Flask, g, render_template, request, redirect,
    url_for, session, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
import qrcode.image.svg


# ==========================================================================
# APPLICATION CONFIGURATION
# ==========================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]

MYSQL_CONFIG = {
    "host": os.environ["MYSQL_HOST"],
    "port": int(os.environ.get("MYSQL_PORT", 3306)),
    "user": os.environ["MYSQL_USER"],
    "password": os.environ["MYSQL_PASSWORD"],
    "database": os.environ["MYSQL_DATABASE"],
}

CATEGORIES = [
    "Technical", "Workshop", "Hackathon", "Career",
    "Competition", "Cultural", "Sports"
]

EVENT_SELECT = """
SELECT
    e.*,
    u.name AS organizer_name,
    (
        SELECT COUNT(*)
        FROM registrations r
        WHERE r.event_id = e.id
    ) AS registered_count
FROM events e
JOIN users u ON u.id = e.organizer_id
"""


# ==========================================================================
# DATABASE HELPERS
# ==========================================================================

def get_db():
    if "db" not in g:
        g.db = mysql.connector.connect(**MYSQL_CONFIG)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None and db.is_connected():
        db.close()


def _mysql_sql(sql):
    return sql.replace("?", "%s")


def query(sql, args=(), one=False):
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(_mysql_sql(sql), args)
        rows = cur.fetchall()
        return rows[0] if rows and one else (None if one else rows)
    finally:
        cur.close()


def execute(sql, args=()):
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(_mysql_sql(sql), args)
        db.commit()
        return cur.lastrowid
    finally:
        cur.close()


def notify(user_id, title, message):
    execute(
        """
        INSERT INTO notifications (user_id, title, message)
        VALUES (?, ?, ?)
        """,
        (user_id, title, message),
    )


def init_db():
    db = mysql.connector.connect(**MYSQL_CONFIG)
    try:
        cur = db.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
    finally:
        db.close()


# ==========================================================================
# AUTHENTICATION
# ==========================================================================

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def roles_required(*roles):
    def deco(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if session.get("role") not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return deco


# ==========================================================================
# GLOBALS / FILTERS
# ==========================================================================

@app.context_processor
def inject_globals():
    unread = 0

    if session.get("user_id"):
        result = query(
            """
            SELECT COUNT(*) AS c
            FROM notifications
            WHERE user_id = ? AND is_read = 0
            """,
            (session["user_id"],),
            one=True,
        )
        unread = result["c"] if result else 0

    return {
        "unread_notifications": unread,
        "current_year": datetime.now().year,
        "today": date.today().isoformat(),
    }


@app.template_filter("pct")
def pct_filter(event):
    cap = event.get("capacity", 0) or 0
    if not cap:
        return 0
    return round(min(100, event["registered_count"] / cap * 100), 1)


@app.template_filter("prettydate")
def prettydate(value):
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d %b %Y")
    except (TypeError, ValueError):
        return value


def can_manage(event):
    return (
        session.get("role") == "admin"
        or event["organizer_id"] == session.get("user_id")
    )


# ==========================================================================
# HOME
# ==========================================================================

@app.route("/")
def index():
    events = query(
        EVENT_SELECT + """
        WHERE e.approval_status = 'approved'
          AND e.status = 'upcoming'
          AND e.event_date >= ?
        ORDER BY e.event_date, e.event_time
        LIMIT 6
        """,
        (date.today(),),
    )

    stats = {
        "events": query(
            "SELECT COUNT(*) AS c FROM events WHERE approval_status='approved'",
            one=True,
        )["c"],
        "students": query(
            "SELECT COUNT(*) AS c FROM users WHERE role='student'",
            one=True,
        )["c"],
        "registrations": query(
            "SELECT COUNT(*) AS c FROM registrations",
            one=True,
        )["c"],
    }

    return render_template("index.html", events=events, stats=stats)


# ==========================================================================
# LOGIN / SIGNUP / LOGOUT
# ==========================================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = query(
            "SELECT * FROM users WHERE email = ?",
            (email,),
            one=True,
        )

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session.update(
                user_id=user["id"],
                name=user["name"],
                role=user["role"],
            )
            flash(f"Welcome back, {user['name']}!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "student").strip().lower()
        department = request.form.get("department", "").strip()

        if not name:
            flash("Please enter your full name.", "error")
            return render_template("signup.html")

        if not re.fullmatch(r"[A-Za-z. ]+", name):
            flash("Name can contain only letters, spaces and dots.", "error")
            return render_template("signup.html")

        if len(name) < 2 or len(name) > 50:
            flash("Name must be between 2 and 50 characters.", "error")
            return render_template("signup.html")

        if not re.fullmatch(r"[A-Za-z0-9._%+-]+@kongu\.edu", email, re.I):
            flash(
                "Please use a valid Kongu Engineering College email ending with @kongu.edu.",
                "error",
            )
            return render_template("signup.html")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return render_template("signup.html")

        if role not in ("student", "organizer"):
            flash("Invalid account type selected.", "error")
            return render_template("signup.html")

        if not department:
            department = "IT"

        if not re.fullmatch(r"[A-Za-z. &/-]+", department):
            flash("Department contains invalid characters.", "error")
            return render_template("signup.html")

        if len(department) > 100:
            flash("Department name is too long.", "error")
            return render_template("signup.html")

        if query(
            "SELECT id FROM users WHERE email = ?",
            (email,),
            one=True,
        ):
            flash("An account with this email already exists.", "error")
            return render_template("signup.html")

        execute(
            """
            INSERT INTO users
            (name, email, password_hash, role, department)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                name,
                email,
                generate_password_hash(password, method="pbkdf2:sha256"),
                role,
                department,
            ),
        )

        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


# ==========================================================================
# DASHBOARDS
# ==========================================================================

@app.route("/dashboard")
@login_required
def dashboard():
    routes = {
        "student": "student_dashboard",
        "organizer": "organizer_dashboard",
        "admin": "admin_dashboard",
    }
    return redirect(url_for(routes[session["role"]]))


@app.route("/student")
@roles_required("student")
def student_dashboard():
    uid = session["user_id"]
    today = date.today()

    regs = query(
        """
        SELECT COUNT(*) AS c, COALESCE(SUM(attended),0) AS a
        FROM registrations
        WHERE student_id = ?
        """,
        (uid,),
        one=True,
    )

    upcoming = query(
        EVENT_SELECT + """
        WHERE e.approval_status='approved'
          AND e.status='upcoming'
          AND e.event_date >= ?
          AND e.id NOT IN (
              SELECT event_id FROM registrations WHERE student_id=?
          )
        ORDER BY e.event_date, e.event_time
        LIMIT 6
        """,
        (today, uid),
    )

    return render_template(
        "student_dashboard.html",
        registered_count=regs["c"],
        attended_count=regs["a"],
        upcoming_events=upcoming,
    )


@app.route("/organizer")
@roles_required("organizer")
def organizer_dashboard():
    events = query(
        EVENT_SELECT + """
        WHERE e.organizer_id=?
        ORDER BY e.event_date DESC, e.event_time DESC
        """,
        (session["user_id"],),
    )

    return render_template(
        "organizer_dashboard.html",
        events=events,
        total_events=len(events),
        total_registrations=sum(e["registered_count"] for e in events),
        total_capacity=sum(e["capacity"] for e in events),
        pending=sum(
            1 for e in events if e["approval_status"] == "pending"
        ),
    )


@app.route("/admin")
@roles_required("admin")
def admin_dashboard():
    def count(sql):
        return query(sql, one=True)["c"]

    stats = {
        "users": count("SELECT COUNT(*) AS c FROM users"),
        "events": count("SELECT COUNT(*) AS c FROM events"),
        "registrations": count("SELECT COUNT(*) AS c FROM registrations"),
        "pending": count(
            "SELECT COUNT(*) AS c FROM events WHERE approval_status='pending'"
        ),
    }

    pending = query(
        EVENT_SELECT + """
        WHERE e.approval_status='pending'
        ORDER BY e.created_at
        """
    )

    events = query(
        EVENT_SELECT + """
        ORDER BY e.event_date DESC, e.event_time DESC
        """
    )

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        pending=pending,
        events=events,
    )


# ==========================================================================
# ADMIN
# ==========================================================================

@app.route("/admin/events/<int:event_id>/<action>", methods=["POST"])
@roles_required("admin")
def review_event(event_id, action):
    if action not in ("approve", "reject"):
        abort(404)

    event = query(
        "SELECT * FROM events WHERE id=?",
        (event_id,),
        one=True,
    ) or abort(404)

    new_status = "approved" if action == "approve" else "rejected"

    execute(
        "UPDATE events SET approval_status=? WHERE id=?",
        (new_status, event_id),
    )

    notify(
        event["organizer_id"],
        f"Event {new_status}",
        f"Your event '{event['title']}' was {new_status} by the admin.",
    )

    flash(f"Event {new_status}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users")
@roles_required("admin")
def admin_users():
    users = query(
        """
        SELECT u.*,
            (SELECT COUNT(*) FROM registrations
             WHERE student_id=u.id) AS reg_count,
            (SELECT COUNT(*) FROM events
             WHERE organizer_id=u.id) AS event_count
        FROM users u
        ORDER BY u.role, u.name
        """
    )
    return render_template("admin_users.html", users=users)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@roles_required("admin")
def delete_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin_users"))

    execute("DELETE FROM users WHERE id=?", (user_id,))
    flash("User removed.", "success")
    return redirect(url_for("admin_users"))


# ==========================================================================
# EVENTS
# ==========================================================================

@app.route("/events")
@login_required
def events():
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    department = request.args.get("department", "").strip()

    sql = EVENT_SELECT + """
        WHERE e.approval_status='approved'
          AND e.status='upcoming'
    """
    args = []

    if search:
        sql += """
        AND (
            e.title LIKE ?
            OR e.description LIKE ?
            OR e.venue LIKE ?
        )
        """
        args.extend([f"%{search}%"] * 3)

    if category:
        sql += " AND e.category=?"
        args.append(category)

    if department:
        sql += " AND e.department LIKE ?"
        args.append(f"%{department}%")

    sql += " ORDER BY e.event_date, e.event_time"

    return render_template(
        "events.html",
        events=query(sql, args),
        search=search,
        selected_category=category,
        selected_department=department,
        categories=CATEGORIES,
    )


@app.route("/events/<int:event_id>")
@login_required
def event_details(event_id):
    event = query(
        EVENT_SELECT + " WHERE e.id=?",
        (event_id,),
        one=True,
    ) or abort(404)

    if event["approval_status"] != "approved" and not can_manage(event):
        abort(404)

    registration = None

    if session["role"] == "student":
        registration = query(
            """
            SELECT * FROM registrations
            WHERE event_id=? AND student_id=?
            """,
            (event_id, session["user_id"]),
            one=True,
        )

    is_past = event["event_date"] < date.today()

    return render_template(
        "event_details.html",
        event=event,
        registration=registration,
        is_past=is_past,
        manage=can_manage(event),
    )


@app.route("/events/<int:event_id>/register", methods=["POST"])
@roles_required("student")
def register_event(event_id):
    event = query(
        EVENT_SELECT + " WHERE e.id=?",
        (event_id,),
        one=True,
    ) or abort(404)

    if event["approval_status"] != "approved" or event["status"] != "upcoming":
        flash("This event is not open for registration.", "error")
    elif event["event_date"] < date.today():
        flash("This event has already taken place.", "error")
    elif event["registered_count"] >= event["capacity"]:
        flash("Sorry, this event is full.", "error")
    elif query(
        """
        SELECT id FROM registrations
        WHERE event_id=? AND student_id=?
        """,
        (event_id, session["user_id"]),
        one=True,
    ):
        flash("You are already registered.", "info")
    else:
        token = uuid.uuid4().hex

        execute(
            """
            INSERT INTO registrations(event_id,student_id,token)
            VALUES(?,?,?)
            """,
            (event_id, session["user_id"], token),
        )

        notify(
            session["user_id"],
            "Registration confirmed",
            f"You are registered for '{event['title']}'. Your QR ticket is in My Events.",
        )

        notify(
            event["organizer_id"],
            "New registration",
            f"{session['name']} registered for '{event['title']}'.",
        )

        flash("Registered successfully! Your QR ticket is ready.", "success")

    return redirect(url_for("event_details", event_id=event_id))


@app.route("/events/<int:event_id>/cancel", methods=["POST"])
@roles_required("student")
def cancel_registration(event_id):
    execute(
        """
        DELETE FROM registrations
        WHERE event_id=? AND student_id=?
        """,
        (event_id, session["user_id"]),
    )
    flash("Registration cancelled.", "info")
    return redirect(url_for("event_details", event_id=event_id))

@app.route("/my-events")
@roles_required("student")
def my_events():
    today = date.today()

    rows = query(
        """
        SELECT e.*, r.id AS reg_id, r.attended,
               u.name AS organizer_name,
               (
                   SELECT COUNT(*) FROM registrations rr
                   WHERE rr.event_id=e.id
               ) AS registered_count
        FROM events e
        JOIN registrations r ON r.event_id=e.id
        JOIN users u ON u.id=e.organizer_id
        WHERE r.student_id=?
        ORDER BY e.event_date, e.event_time
        """,
        (session["user_id"],),
    )

    return render_template(
        "my_events.html",
        events=rows,
        today=today,
    )

# ==========================================================================
# EVENT FORM / CREATE / EDIT / DELETE
# ==========================================================================

def parse_event_form(form):
    data = {
        key: form.get(key, "").strip()
        for key in (
            "title", "description", "category", "department",
            "event_date", "event_time", "venue"
        )
    }

    try:
        data["capacity"] = int(form.get("capacity", "0"))
        datetime.strptime(data["event_date"], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None, "Enter a valid date and capacity."

    if not all(data[key] for key in data):
        return None, "All fields are required and capacity must be at least 1."

    if data["capacity"] < 1:
        return None, "All fields are required and capacity must be at least 1."

    return data, None


@app.route("/events/create", methods=["GET", "POST"])
@roles_required("organizer", "admin")
def create_event():
    if request.method == "POST":
        data, err = parse_event_form(request.form)

        if err:
            flash(err, "error")
            return render_template(
                "create_event.html",
                edit_mode=False,
                event=request.form,
                categories=CATEGORIES,
            )

        approval = "approved" if session["role"] == "admin" else "pending"

        execute(
            """
            INSERT INTO events
            (title,description,category,department,event_date,event_time,
             venue,capacity,organizer_id,approval_status)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data["title"], data["description"], data["category"],
                data["department"], data["event_date"], data["event_time"],
                data["venue"], data["capacity"], session["user_id"], approval,
            ),
        )

        if approval == "pending":
            admins = query("SELECT id FROM users WHERE role='admin'")
            for admin_user in admins:
                notify(
                    admin_user["id"],
                    "Event awaiting approval",
                    f"'{data['title']}' needs your review.",
                )
            flash(
                "Event submitted. It will be visible once an admin approves it.",
                "success",
            )
        else:
            flash("Event created and published.", "success")

        return redirect(url_for("dashboard"))

    return render_template(
        "create_event.html",
        edit_mode=False,
        event=None,
        categories=CATEGORIES,
    )


@app.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@roles_required("organizer", "admin")
def edit_event(event_id):
    event = query(
        "SELECT * FROM events WHERE id=?",
        (event_id,),
        one=True,
    ) or abort(404)

    if not can_manage(event):
        abort(403)

    if request.method == "POST":
        data, err = parse_event_form(request.form)
        status = request.form.get("status", "upcoming")

        if err:
            flash(err, "error")
            return render_template(
                "create_event.html",
                edit_mode=True,
                event=event,
                categories=CATEGORIES,
            )

        if status not in ("upcoming", "cancelled", "completed"):
            status = "upcoming"

        execute(
            """
            UPDATE events SET
                title=?, description=?, category=?, department=?,
                event_date=?, event_time=?, venue=?, capacity=?, status=?
            WHERE id=?
            """,
            (
                data["title"], data["description"], data["category"],
                data["department"], data["event_date"], data["event_time"],
                data["venue"], data["capacity"], status, event_id,
            ),
        )

        registrations = query(
            "SELECT student_id FROM registrations WHERE event_id=?",
            (event_id,),
        )

        for registration in registrations:
            notify(
                registration["student_id"],
                "Event updated",
                f"'{data['title']}' details were updated or changed. Please review.",
            )

        flash("Event updated.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "create_event.html",
        edit_mode=True,
        event=event,
        categories=CATEGORIES,
    )


@app.route("/events/<int:event_id>/delete", methods=["POST"])
@roles_required("organizer", "admin")
def delete_event(event_id):
    event = query(
        "SELECT * FROM events WHERE id=?",
        (event_id,),
        one=True,
    ) or abort(404)

    if not can_manage(event):
        abort(403)

    execute("DELETE FROM events WHERE id=?", (event_id,))
    flash("Event deleted.", "success")
    return redirect(url_for("dashboard"))


# ==========================================================================
# REGISTRATIONS / ATTENDANCE
# ==========================================================================

@app.route("/registrations")
@roles_required("organizer", "admin")
def organizer_registrations():
    event_id = request.args.get("event_id", type=int)

    scope = ""
    scope_args = []

    if session["role"] == "organizer":
        scope = " AND e.organizer_id=? "
        scope_args = [session["user_id"]]

    event_list = query(
        """
        SELECT e.id,e.title
        FROM events e
        WHERE 1=1
        """ + scope + """
        ORDER BY e.event_date DESC
        """,
        scope_args,
    )

    sql = """
    SELECT r.id,r.attended,r.registered_at,r.token,
           u.name AS student_name,u.email AS student_email,
           u.department AS student_department,
           e.title AS event_title,e.event_date,e.id AS event_id
    FROM registrations r
    JOIN users u ON u.id=r.student_id
    JOIN events e ON e.id=r.event_id
    WHERE 1=1
    """ + scope

    args = list(scope_args)

    if event_id:
        sql += " AND e.id=?"
        args.append(event_id)

    sql += " ORDER BY e.event_date DESC,u.name"

    return render_template(
        "organizer_registrations.html",
        registrations=query(sql, args),
        event_list=event_list,
        selected_event=event_id,
    )


def registration_for_manager(reg_id):
    reg = query(
        """
        SELECT r.*,e.title,e.organizer_id,e.event_date,e.event_time,e.venue,
               u.name AS student_name,u.email AS student_email,
               u.department AS student_department
        FROM registrations r
        JOIN events e ON e.id=r.event_id
        JOIN users u ON u.id=r.student_id
        WHERE r.id=?
        """,
        (reg_id,),
        one=True,
    ) or abort(404)

    if not can_manage(reg):
        abort(403)

    return reg


@app.route("/attendance/<int:reg_id>", methods=["POST"])
@roles_required("organizer", "admin")
def toggle_attendance(reg_id):
    reg = registration_for_manager(reg_id)

    if reg["event_date"] > date.today():
        flash(
            f"Attendance cannot be marked before the event date "
            f"({reg['event_date'].strftime('%d-%m-%Y')}).",
            "error",
        )
        return redirect(
            request.referrer or url_for("organizer_registrations")
        )

    new_attendance = 0 if reg["attended"] else 1

    execute(
        "UPDATE registrations SET attended=? WHERE id=?",
        (new_attendance, reg_id),
    )

    if new_attendance:
        notify(
            reg["student_id"],
            "Attendance recorded",
            f"Your attendance for '{reg['title']}' was recorded. "
            "Your certificate is ready in My Events.",
        )

    flash(
        f"{reg['student_name']} marked "
        f"{'present' if new_attendance else 'absent'}.",
        "success",
    )

    return redirect(
        request.referrer or url_for("organizer_registrations")
    )


@app.route("/scan-attendance")
@roles_required("organizer", "admin")
def scan_attendance():
    return render_template("scan_attendance.html")


@app.route("/verify/<token>")
@roles_required("organizer", "admin")
def verify_ticket(token):
    reg = query(
        """
        SELECT r.id,r.attended,r.student_id,
               e.title,e.event_date,
               u.name AS student_name
        FROM registrations r
        JOIN events e ON e.id=r.event_id
        JOIN users u ON u.id=r.student_id
        WHERE r.token=?
        """,
        (token,),
        one=True,
    ) or abort(404)

    if reg["event_date"] > date.today():
        flash(
            f"Attendance cannot be marked before the event date "
            f"({reg['event_date'].strftime('%d-%m-%Y')}).",
            "error",
        )
        return render_template("verify.html", reg=reg)

    if not reg["attended"]:
        execute(
            "UPDATE registrations SET attended=1 WHERE id=?",
            (reg["id"],),
        )
        notify(
            reg["student_id"],
            "Attendance recorded",
            f"Your attendance for '{reg['title']}' was recorded. "
            "Your certificate is ready in My Events.",
        )
        flash(
            f"{reg['student_name']} marked present successfully.",
            "success",
        )
    else:
        flash(
            f"{reg['student_name']} is already marked present.",
            "info",
        )

    return render_template("verify.html", reg=reg)


# ==========================================================================
# TICKET / CERTIFICATE
# ==========================================================================

@app.route("/ticket/<int:reg_id>")
@roles_required("student")
def ticket(reg_id):
    reg = query(
        """
        SELECT r.*,e.title,e.event_date,e.event_time,e.venue
        FROM registrations r
        JOIN events e ON e.id=r.event_id
        WHERE r.id=? AND r.student_id=?
        """,
        (reg_id, session["user_id"]),
        one=True,
    ) or abort(404)

    qr_url = url_for(
        "verify_ticket",
        token=reg["token"],
        _external=True,
    )

    img = qrcode.make(
        qr_url,
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=10,
    )

    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    svg = svg[svg.index("<svg"):]

    return render_template(
        "ticket.html",
        reg=reg,
        qr_svg=svg,
    )


@app.route("/certificate/<int:reg_id>")
@roles_required("student", "organizer", "admin")
def certificate(reg_id):
    reg = query(
        """
        SELECT r.*,
               e.title,e.event_date,e.venue,e.category,e.organizer_id,
               u.name AS student_name,
               u.department AS student_department,
               o.name AS organizer_name
        FROM registrations r
        JOIN events e ON e.id=r.event_id
        JOIN users u ON u.id=r.student_id
        JOIN users o ON o.id=e.organizer_id
        WHERE r.id=?
        """,
        (reg_id,),
        one=True,
    )

    if not reg:
        abort(404)

    if (
        session.get("role") == "student"
        and reg["student_id"] != session.get("user_id")
    ):
        abort(403)

    if (
        session.get("role") == "organizer"
        and reg["organizer_id"] != session.get("user_id")
    ):
        abort(403)

    if not reg["attended"]:
        flash(
            "Certificate is available only after attendance is recorded.",
            "error",
        )
        return redirect(url_for("my_events"))

    return render_template("certificate.html", reg=reg)


# ==========================================================================
# PROFILE
# ==========================================================================

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = query(
        "SELECT * FROM users WHERE id=?",
        (session["user_id"],),
        one=True,
    ) or abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        department = request.form.get("department", "").strip()
        new_password = request.form.get("password", "")

        if not re.fullmatch(r"[A-Za-z. ]+", name):
            flash(
                "Name can contain only letters, spaces and dots.",
                "error",
            )
            return render_template("profile.html", user=user)

        if not 2 <= len(name) <= 50:
            flash("Name must be between 2 and 50 characters.", "error")
            return render_template("profile.html", user=user)

        if not department:
            department = "IT"

        if not re.fullmatch(r"[A-Za-z. &/-]+", department):
            flash("Department contains invalid characters.", "error")
            return render_template("profile.html", user=user)

        if new_password and len(new_password) < 8:
            flash("New password must contain at least 8 characters.", "error")
            return render_template("profile.html", user=user)

        if new_password:
            execute(
                """
                UPDATE users
                SET name=?,department=?,password_hash=?
                WHERE id=?
                """,
                (
                    name,
                    department,
                    generate_password_hash(
                        new_password,
                        method="pbkdf2:sha256",
                    ),
                    session["user_id"],
                ),
            )
        else:
            execute(
                """
                UPDATE users
                SET name=?,department=?
                WHERE id=?
                """,
                (name, department, session["user_id"]),
            )

        session["name"] = name
        flash("Profile updated successfully.", "success")

        user = query(
            "SELECT * FROM users WHERE id=?",
            (session["user_id"],),
            one=True,
        )

    return render_template("profile.html", user=user)


# ==========================================================================
# NOTIFICATIONS
# ==========================================================================

@app.route("/notifications")
@login_required
def notifications():
    rows = query(
        """
        SELECT *
        FROM notifications
        WHERE user_id=?
        ORDER BY created_at DESC
        """,
        (session["user_id"],),
    )

    execute(
        """
        UPDATE notifications
        SET is_read=1
        WHERE user_id=?
        """,
        (session["user_id"],),
    )

    return render_template("notifications.html", notifications=rows)


# ==========================================================================
# CALENDAR
# ==========================================================================

@app.route("/calendar")
@login_required
def calendar():
    today = date.today()

    events = query(
        """
        SELECT e.*,u.name AS organizer_name
        FROM events e
        JOIN users u ON u.id=e.organizer_id
        WHERE e.approval_status='approved'
        ORDER BY e.event_date,e.event_time
        """
    )

    grouped = {}

    for event in events:
        event_date = event["event_date"]

        if isinstance(event_date, datetime):
            event_date = event_date.date()

        key = event_date.strftime("%Y-%m-%d")
        grouped.setdefault(key, []).append(event)

    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month

    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    month_calendar = pycal.monthcalendar(year, month)

    return render_template(
        "calendar.html",
        events=events,
        grouped_events=grouped,
        calendar_data=month_calendar,
        calendar_month=month,
        calendar_year=year,
        today=today,
    )


# ==========================================================================
# ERROR HANDLERS
# ==========================================================================

@app.errorhandler(403)
def forbidden(_error):
    return render_template(
        "error.html",
        code=403,
        message="You do not have permission to access this page.",
    ), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template(
        "error.html",
        code=404,
        message="The page you requested was not found.",
    ), 404


@app.errorhandler(500)
def server_error(_error):
    return render_template(
        "error.html",
        code=500,
        message="Something went wrong on the server.",
    ), 500


# ==========================================================================
# RUN
# ==========================================================================

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
