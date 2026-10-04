"""
CampusConnect - Campus Event Management System
Roles   : student, organizer, admin
Features: auth, event creation + admin approval, registration, QR tickets,
          attendance marking (QR verify / manual), certificates, calendar,
          notifications, profile.
Run     : python app.py   ->  http://127.0.0.1:5000
"""
import io
import os
import mysql.connector
from dotenv import load_dotenv
import uuid
import calendar as pycal
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Flask, g, render_template, request, redirect,
                   url_for, session, flash, abort)
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
import qrcode.image.svg

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

CATEGORIES = ["Technical", "Workshop", "Hackathon", "Career", "Competition", "Cultural", "Sports"]



EVENT_SELECT = """
SELECT e.*, u.name AS organizer_name,
       (SELECT COUNT(*) FROM registrations r WHERE r.event_id = e.id) AS registered_count
FROM events e JOIN users u ON u.id = e.organizer_id
"""

# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
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
    # The existing CampusConnect code uses SQLite's ? placeholders.
    # MySQL Connector/Python uses %s, so convert them in one place.
    return sql.replace("?", "%s")


def query(sql, args=(), one=False):
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(_mysql_sql(sql), args)
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows
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
    execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
            (user_id, title, message))


def init_db():
    # The MySQL database and tables are created separately in MySQL Workbench.
    # We only verify that CampusConnect can connect successfully.
    db = mysql.connector.connect(**MYSQL_CONFIG)
    try:
        cur = db.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
    finally:
        db.close()



# --------------------------------------------------------------------------
# Auth helpers
# --------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("user_id"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def roles_required(*roles):
    def deco(f):
        @wraps(f)
        @login_required
        def wrapper(*a, **kw):
            if session.get("role") not in roles:
                abort(403)
            return f(*a, **kw)
        return wrapper
    return deco


@app.context_processor
def inject_globals():
    unread = 0
    if session.get("user_id"):
        unread = query("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0",
                       (session["user_id"],), one=True)["c"]
    return {"unread_notifications": unread, "current_year": datetime.now().year,
            "today": date.today().isoformat()}


@app.template_filter("pct")
def pct_filter(event):
    cap = event["capacity"] or 0
    return round(min(100, event["registered_count"] / cap * 100), 1) if cap else 0


@app.template_filter("prettydate")
def prettydate(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d %b %Y")
    except (TypeError, ValueError):
        return value


def can_manage(event):
    return session.get("role") == "admin" or event["organizer_id"] == session.get("user_id")


# --------------------------------------------------------------------------
# Public + auth routes
# --------------------------------------------------------------------------
@app.route("/")
def index():
    events = query(EVENT_SELECT + " WHERE e.approval_status='approved' AND e.status='upcoming' "
                   "AND e.event_date >= ? ORDER BY e.event_date, e.event_time LIMIT 6",
                   (date.today().isoformat(),))
    stats = {
        "events": query("SELECT COUNT(*) c FROM events WHERE approval_status='approved'", one=True)["c"],
        "students": query("SELECT COUNT(*) c FROM users WHERE role='student'", one=True)["c"],
        "registrations": query("SELECT COUNT(*) c FROM registrations", one=True)["c"],
    }
    return render_template("index.html", events=events, stats=stats)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = query("SELECT * FROM users WHERE email=?", (email,), one=True)
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session.update(user_id=user["id"], name=user["name"], role=user["role"])
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
        role = request.form.get("role", "student")
        department = request.form.get("department", "").strip() or "IT"
        if role not in ("student", "organizer"):
            role = "student"
        if not name or not email or len(password) < 6:
            flash("Fill all fields. Password must be at least 6 characters.", "error")
        elif query("SELECT id FROM users WHERE email=?", (email,), one=True):
            flash("An account with this email already exists.", "error")
        else:
            execute("INSERT INTO users (name,email,password_hash,role,department) VALUES (?,?,?,?,?)",
                    (name, email, generate_password_hash(password), role, department))
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    return redirect(url_for({"student": "student_dashboard", "organizer": "organizer_dashboard",
                             "admin": "admin_dashboard"}[session["role"]]))


# --------------------------------------------------------------------------
# Dashboards
# --------------------------------------------------------------------------
@app.route("/student")
@roles_required("student")
def student_dashboard():
    uid, today = session["user_id"], date.today().isoformat()
    regs = query("SELECT COUNT(*) c, COALESCE(SUM(attended),0) a FROM registrations WHERE student_id=?", (uid,), one=True)
    upcoming = query(EVENT_SELECT + " WHERE e.approval_status='approved' AND e.status='upcoming' AND e.event_date>=? "
                     "AND e.id NOT IN (SELECT event_id FROM registrations WHERE student_id=?) "
                     "ORDER BY e.event_date LIMIT 6", (today, uid))
    return render_template("student_dashboard.html", registered_count=regs["c"], attended_count=regs["a"],
                           upcoming_events=upcoming)


@app.route("/organizer")
@roles_required("organizer")
def organizer_dashboard():
    events = query(EVENT_SELECT + " WHERE e.organizer_id=? ORDER BY e.event_date DESC", (session["user_id"],))
    return render_template("organizer_dashboard.html", events=events,
                           total_events=len(events),
                           total_registrations=sum(e["registered_count"] for e in events),
                           total_capacity=sum(e["capacity"] for e in events),
                           pending=sum(1 for e in events if e["approval_status"] == "pending"))


@app.route("/admin")
@roles_required("admin")
def admin_dashboard():
    c = lambda sql: query(sql, one=True)["c"]
    stats = {"users": c("SELECT COUNT(*) c FROM users"),
             "events": c("SELECT COUNT(*) c FROM events"),
             "registrations": c("SELECT COUNT(*) c FROM registrations"),
             "pending": c("SELECT COUNT(*) c FROM events WHERE approval_status='pending'")}
    pending = query(EVENT_SELECT + " WHERE e.approval_status='pending' ORDER BY e.created_at")
    events = query(EVENT_SELECT + " ORDER BY e.event_date DESC")
    return render_template("admin_dashboard.html", stats=stats, pending=pending, events=events)


@app.route("/admin/events/<int:event_id>/<action>", methods=["POST"])
@roles_required("admin")
def review_event(event_id, action):
    if action not in ("approve", "reject"):
        abort(404)
    event = query("SELECT * FROM events WHERE id=?", (event_id,), one=True) or abort(404)
    new = "approved" if action == "approve" else "rejected"
    execute("UPDATE events SET approval_status=? WHERE id=?", (new, event_id))
    notify(event["organizer_id"], f"Event {new}", f"Your event '{event['title']}' was {new} by the admin.")
    flash(f"Event {new}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users")
@roles_required("admin")
def admin_users():
    users = query("SELECT u.*, (SELECT COUNT(*) FROM registrations WHERE student_id=u.id) AS reg_count, "
                  "(SELECT COUNT(*) FROM events WHERE organizer_id=u.id) AS event_count "
                  "FROM users u ORDER BY u.role, u.name")
    return render_template("admin_users.html", users=users)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@roles_required("admin")
def delete_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.", "error")
    else:
        for e in query("SELECT id FROM events WHERE organizer_id=?", (user_id,)):
            execute("DELETE FROM events WHERE id=?", (e["id"],))
        execute("DELETE FROM users WHERE id=?", (user_id,))
        flash("User removed.", "success")
    return redirect(url_for("admin_users"))


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------
@app.route("/events")
@login_required
def events():
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "")
    department = request.args.get("department", "").strip()
    sql, args = EVENT_SELECT + " WHERE e.approval_status='approved' AND e.status='upcoming'", []
    if search:
        sql += " AND (e.title LIKE ? OR e.description LIKE ? OR e.venue LIKE ?)"
        args += [f"%{search}%"] * 3
    if category:
        sql += " AND e.category=?"; args.append(category)
    if department:
        sql += " AND e.department LIKE ?"; args.append(f"%{department}%")
    sql += " ORDER BY e.event_date, e.event_time"
    return render_template("events.html", events=query(sql, args), search=search,
                           selected_category=category, selected_department=department,
                           categories=CATEGORIES)


@app.route("/events/<int:event_id>")
@login_required
def event_details(event_id):
    event = query(EVENT_SELECT + " WHERE e.id=?", (event_id,), one=True) or abort(404)
    if event["approval_status"] != "approved" and not can_manage(event):
        abort(404)
    registration = None
    if session["role"] == "student":
        registration = query("SELECT * FROM registrations WHERE event_id=? AND student_id=?",
                             (event_id, session["user_id"]), one=True)
    is_past = event["event_date"] < date.today()
    return render_template("event_details.html", event=event, registration=registration,
                           is_past=is_past, manage=can_manage(event))


@app.route("/events/<int:event_id>/register", methods=["POST"])
@roles_required("student")
def register_event(event_id):
    event = query(EVENT_SELECT + " WHERE e.id=?", (event_id,), one=True) or abort(404)
    if event["approval_status"] != "approved" or event["status"] != "upcoming":
        flash("This event is not open for registration.", "error")
    elif event["event_date"] < date.today():
        flash("This event has already taken place.", "error")
    elif event["registered_count"] >= event["capacity"]:
        flash("Sorry, this event is full.", "error")
    elif query("SELECT id FROM registrations WHERE event_id=? AND student_id=?", (event_id, session["user_id"]), one=True):
        flash("You are already registered.", "info")
    else:
        execute("INSERT INTO registrations (event_id,student_id,token) VALUES (?,?,?)",
                (event_id, session["user_id"], uuid.uuid4().hex))
        notify(session["user_id"], "Registration confirmed", f"You are registered for '{event['title']}'. Your QR ticket is in My Events.")
        notify(event["organizer_id"], "New registration", f"{session['name']} registered for '{event['title']}'.")
        flash("Registered successfully! Your QR ticket is ready.", "success")
    return redirect(url_for("event_details", event_id=event_id))


@app.route("/events/<int:event_id>/cancel", methods=["POST"])
@roles_required("student")
def cancel_registration(event_id):
    execute("DELETE FROM registrations WHERE event_id=? AND student_id=?", (event_id, session["user_id"]))
    flash("Registration cancelled.", "info")
    return redirect(url_for("event_details", event_id=event_id))


@app.route("/my-events")
@roles_required("student")
def my_events():
    rows = query(EVENT_SELECT.replace("SELECT e.*,", "SELECT e.*, r.id AS reg_id, r.attended AS attended,", 1)
                 .replace("FROM events e", "FROM events e JOIN registrations r ON r.event_id=e.id", 1)
                 + " WHERE r.student_id=? ORDER BY e.event_date", (session["user_id"],))
    return render_template("my_events.html", events=rows)


def parse_event_form(form):
    data = {k: form.get(k, "").strip() for k in
            ("title", "description", "category", "department", "event_date", "event_time", "venue")}
    try:
        data["capacity"] = int(form.get("capacity", "0"))
        datetime.strptime(data["event_date"], "%Y-%m-%d")
    except ValueError:
        return None, "Enter a valid date and capacity."
    if not all(data[k] for k in data) or data["capacity"] < 1:
        return None, "All fields are required and capacity must be at least 1."
    return data, None


@app.route("/events/create", methods=["GET", "POST"])
@roles_required("organizer", "admin")
def create_event():
    if request.method == "POST":
        data, err = parse_event_form(request.form)
        if err:
            flash(err, "error")
            return render_template("create_event.html", edit_mode=False, event=request.form, categories=CATEGORIES)
        approval = "approved" if session["role"] == "admin" else "pending"
        eid = execute("INSERT INTO events (title,description,category,department,event_date,event_time,venue,capacity,organizer_id,approval_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (data["title"], data["description"], data["category"], data["department"], data["event_date"],
                       data["event_time"], data["venue"], data["capacity"], session["user_id"], approval))
        if approval == "pending":
            for a in query("SELECT id FROM users WHERE role='admin'"):
                notify(a["id"], "Event awaiting approval", f"'{data['title']}' needs your review.")
            flash("Event submitted. It will be visible once an admin approves it.", "success")
        else:
            flash("Event created and published.", "success")
        return redirect(url_for("dashboard"))
    return render_template("create_event.html", edit_mode=False, event=None, categories=CATEGORIES)


@app.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@roles_required("organizer", "admin")
def edit_event(event_id):
    event = query("SELECT * FROM events WHERE id=?", (event_id,), one=True) or abort(404)
    if not can_manage(event):
        abort(403)
    if request.method == "POST":
        data, err = parse_event_form(request.form)
        status = request.form.get("status", "upcoming")
        if err:
            flash(err, "error")
            return render_template("create_event.html", edit_mode=True, event=event, categories=CATEGORIES)
        execute("UPDATE events SET title=?,description=?,category=?,department=?,event_date=?,event_time=?,venue=?,capacity=?,status=? WHERE id=?",
                (data["title"], data["description"], data["category"], data["department"], data["event_date"],
                 data["event_time"], data["venue"], data["capacity"],
                 status if status in ("upcoming", "cancelled") else "upcoming", event_id))
        for r in query("SELECT student_id FROM registrations WHERE event_id=?", (event_id,)):
            notify(r["student_id"], "Event updated", f"'{data['title']}' details were updated or changed. Please review.")
        flash("Event updated.", "success")
        return redirect(url_for("dashboard"))
    return render_template("create_event.html", edit_mode=True, event=event, categories=CATEGORIES)


@app.route("/events/<int:event_id>/delete", methods=["POST"])
@roles_required("organizer", "admin")
def delete_event(event_id):
    event = query("SELECT * FROM events WHERE id=?", (event_id,), one=True) or abort(404)
    if not can_manage(event):
        abort(403)
    execute("DELETE FROM events WHERE id=?", (event_id,))
    flash("Event deleted.", "success")
    return redirect(url_for("dashboard"))


# --------------------------------------------------------------------------
# Registrations, attendance, QR, certificates
# --------------------------------------------------------------------------
@app.route("/registrations")
@roles_required("organizer", "admin")
def organizer_registrations():
    event_id = request.args.get("event_id", type=int)
    scope, args = "", []
    if session["role"] == "organizer":
        scope, args = " AND e.organizer_id=?", [session["user_id"]]
    my_events_list = query("SELECT id,title FROM events e WHERE 1=1" + scope + " ORDER BY event_date DESC", args)
    sql = ("SELECT r.id, r.attended, r.registered_at, r.token, u.name AS student_name, u.email AS student_email, "
           "u.department AS student_department, e.title AS event_title, e.event_date, e.id AS event_id "
           "FROM registrations r JOIN users u ON u.id=r.student_id JOIN events e ON e.id=r.event_id WHERE 1=1" + scope)
    if event_id:
        sql += " AND e.id=?"; args.append(event_id)
    rows = query(sql + " ORDER BY e.event_date DESC, u.name", args)
    return render_template("organizer_registrations.html", registrations=rows,
                           event_list=my_events_list, selected_event=event_id)


def registration_for_manager(reg_id):
    reg = query("SELECT r.*, e.title, e.organizer_id, e.event_date, e.event_time, e.venue, u.name AS student_name, "
                "u.email AS student_email, u.department AS student_department "
                "FROM registrations r JOIN events e ON e.id=r.event_id JOIN users u ON u.id=r.student_id "
                "WHERE r.id=?", (reg_id,), one=True) or abort(404)
    if not can_manage(reg):
        abort(403)
    return reg


@app.route("/attendance/<int:reg_id>", methods=["POST"])
@roles_required("organizer", "admin")
def toggle_attendance(reg_id):
    reg = registration_for_manager(reg_id)
    new = 0 if reg["attended"] else 1
    execute("UPDATE registrations SET attended=? WHERE id=?", (new, reg_id))
    if new:
        notify(reg["student_id"], "Attendance recorded", f"Your attendance for '{reg['title']}' was recorded. Your certificate is ready in My Events.")
    flash(f"{reg['student_name']} marked {'present' if new else 'absent'}.", "success")
    return redirect(request.referrer or url_for("organizer_registrations"))
@app.route("/scan-attendance")
@roles_required("organizer", "admin")
def scan_attendance():
    return render_template("scan_attendance.html")
@app.route("/verify/<token>")
@roles_required("organizer", "admin")
def verify_ticket(token):
    reg = query(
        "SELECT id FROM registrations WHERE token=?",
        (token,),
        one=True
    ) or abort(404)

    reg = registration_for_manager(reg["id"])

    # Automatically mark attendance
    if not reg["attended"]:
        execute(
            "UPDATE registrations SET attended=1 WHERE id=?",
            (reg["id"],)
        )

        notify(
            reg["student_id"],
            "Attendance recorded",
            f"Your attendance for '{reg['title']}' was recorded. Your certificate is ready in My Events."
        )

        flash(
            f"{reg['student_name']} marked present successfully.",
            "success"
        )
    else:
        flash(
            f"{reg['student_name']} is already marked present.",
            "info"
        )

    return render_template("verify.html", reg=reg)

@app.route("/ticket/<int:reg_id>")
@roles_required("student")
def ticket(reg_id):
    reg = query("SELECT r.*, e.title, e.event_date, e.event_time, e.venue FROM registrations r "
                "JOIN events e ON e.id=r.event_id WHERE r.id=? AND r.student_id=?",
                (reg_id, session["user_id"]), one=True) or abort(404)
    img = qrcode.make(url_for("verify_ticket", token=reg["token"], _external=True),
                      image_factory=qrcode.image.svg.SvgPathImage, box_size=10)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    svg = svg[svg.index("<svg"):]
    return render_template("ticket.html", reg=reg, qr_svg=svg)


@app.route("/certificate/<int:reg_id>")
@roles_required("student", "organizer", "admin")
def certificate(reg_id):
    reg = query("SELECT r.*, e.title, e.event_date, e.venue, e.category, e.organizer_id, "
                "u.name AS student_name, u.department AS student_department, o.name AS organizer_name "
                "FROM registrations r JOIN events e ON e.id=r.event_id JOIN users u ON u.id=r.student_id "
                "JOIN users o ON o.id=e.organizer_id WHERE r.id=?", (reg_id,), one=True) or abort(404)
    if session["role"] == "student" and reg["student_id"] != session["user_id"]:
        abort(403)
    if session["role"] == "organizer" and reg["organizer_id"] != session["user_id"]:
        abort(403)
    if not reg["attended"]:
        flash("Certificate is available only after attendance is recorded.", "error")
        return redirect(url_for("dashboard"))
    return render_template("certificate.html", reg=reg)


# --------------------------------------------------------------------------
# Profile, notifications, calendar
# --------------------------------------------------------------------------
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    uid = session["user_id"]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        dept = request.form.get("department", "").strip()
        new_pw = request.form.get("new_password", "")
        if not name:
            flash("Name cannot be empty.", "error")
        else:
            execute("UPDATE users SET name=?, department=? WHERE id=?", (name, dept, uid))
            session["name"] = name
            if new_pw:
                if len(new_pw) < 6:
                    flash("New password must be at least 6 characters.", "error")
                    return redirect(url_for("profile"))
                execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_pw), uid))
            flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=query("SELECT * FROM users WHERE id=?", (uid,), one=True))


@app.route("/notifications")
@login_required
def notifications():
    uid = session["user_id"]
    items = query("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (uid,))
    execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (uid,))
    return render_template("notifications.html", notifications=items)


@app.route("/calendar")
@login_required
def calendar():
    m = request.args.get("m", date.today().strftime("%Y-%m"))
    try:
        first = datetime.strptime(m + "-01", "%Y-%m-%d").date()
    except ValueError:
        first = date.today().replace(day=1)
    prev_m = (first - timedelta(days=1)).strftime("%Y-%m")
    next_m = (first + timedelta(days=32)).replace(day=1).strftime("%Y-%m")
    rows = query(EVENT_SELECT + " WHERE e.approval_status='approved' AND e.status='upcoming' "
                 "AND substr(e.event_date,1,7)=? ORDER BY e.event_time", (first.strftime("%Y-%m"),))
    by_day = {}
    for e in rows:
        by_day.setdefault(e["event_date"], []).append(e)
    weeks = [[{"date": d, "in_month": d.month == first.month, "events": by_day.get(d.isoformat(), [])}
              for d in week] for week in pycal.Calendar(firstweekday=6).monthdatescalendar(first.year, first.month)]
    return render_template("calendar.html", weeks=weeks, month_label=first.strftime("%B %Y"),
                           prev_m=prev_m, next_m=next_m, events=rows)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
def error_page(code, msg):
    return render_template("error.html", error_code=code, error_message=msg), code


@app.errorhandler(403)
def e403(_): return error_page(403, "You don't have permission to view this page.")
@app.errorhandler(404)
def e404(_): return error_page(404, "The page you are looking for does not exist.")
@app.errorhandler(500)
def e500(_): return error_page(500, "Something went wrong on our side. Please try again.")


init_db()

if __name__ == "__main__":
    print("=" * 60)
    print("  CAMPUSCONNECT - Campus Event Management System")
    print("=" * 60)
    print("  http://127.0.0.1:5000\n")
    print("  Database: MySQL / campusconnect_production")
    print("=" * 60)
    app.run(
    host="0.0.0.0",
    port=5000,
    debug=False
)