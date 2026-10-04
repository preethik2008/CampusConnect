import sqlite3
from werkzeug.security import check_password_hash, generate_password_hash

db = sqlite3.connect("campusconnect.db")
row = db.execute(
    "select password_hash from users where email = ?", ("admin@kongu.edu",)
).fetchone()

if row is None:
    print("Admin user not found")
else:
    ok = check_password_hash(row[0], "Admin@123")
    print("Password check:", ok)
    if not ok:
        db.execute(
            "update users set password_hash = ? where email = ?",
            (generate_password_hash("Admin@123"), "admin@kongu.edu"),
        )
        db.commit()
        print("Password was wrong, so it has been reset to Admin@123")