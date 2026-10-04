# CampusConnect - Campus Event Management System

Flask + SQLite + glassmorphism UI. Roles: Student, Organizer, Admin.

## Run (Windows PowerShell)
```
cd CampusConnect_Final
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000  (the database is created and seeded automatically on first run;
delete campusconnect.db any time to reset).

## Demo logins
| Role      | Email              | Password      |
|-----------|--------------------|---------------|
| Admin     | admin@kongu.edu    | Admin@123     |
| Organizer | events@kongu.edu   | Organizer@123 |
| Student   | student@kongu.edu  | Student@123   |

## Flow
1. Organizer creates an event -> status "pending".
2. Admin approves it on the Admin dashboard -> visible to students.
3. Student registers -> gets a QR ticket (My Events -> Ticket).
4. Organizer/Admin marks attendance (Registrations page, or scan the QR which opens /verify/<token>).
5. Student gets a printable certificate (Print -> Save as PDF).
