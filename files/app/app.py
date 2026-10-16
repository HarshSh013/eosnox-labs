"""Eosnox Labs — local veterinary laboratory workstation."""

import calendar
import json
import os
import re
import sqlite3
from datetime import date, datetime
from urllib.parse import quote

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import db
from instruments import network, store, unics100, workers

app = Flask(
    __name__,
    template_folder=str(db.BASE_DIR / "templates"),
    static_folder=str(db.BASE_DIR / "static"),
)
app.secret_key = "vetlab-local-workstation"


def _clean(value):
    return (value or "").strip()


def _optional_float(value):
    text = _clean(value)
    if text == "":
        return None
    return float(text)


def _amount(value):
    """Rupee amount from a form field; blank is zero, negatives are refused."""
    number = _optional_float(value) or 0.0
    if number < 0:
        raise ValueError("Amounts cannot be negative.")
    return round(number, 2)


def _flag(value_num, low, high):
    if value_num is None or (low is None and high is None):
        return None
    if low is not None and value_num < low:
        return "L"
    if high is not None and value_num > high:
        return "H"
    return "N"


def _parse_result(raw):
    text = _clean(raw)
    if not text:
        raise ValueError("Enter a result.")
    try:
        return text, float(text)
    except ValueError:
        return text, None


def _qc_status(observed, mean, sd):
    if sd <= 0:
        raise ValueError("Standard deviation must be greater than zero.")
    z = (observed - mean) / sd
    absolute = abs(z)
    if absolute > 3:
        status = "Reject"
    elif absolute > 2:
        status = "Warning"
    else:
        status = "Accept"
    return round(z, 2), status


def _whatsapp_digits(number):
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def _valid_email(email):
    if not email:
        return True
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is not None


def _valid_whatsapp(number):
    if not number:
        return True
    digits = re.sub(r"\D", "", number)
    return 10 <= len(digits) <= 15


def _patient_from_form():
    data = {
        "animal_name": _clean(request.form.get("animal_name")),
        "owner_name": _clean(request.form.get("owner_name")),
        "patient_type": _clean(request.form.get("patient_type")),
        "species": _clean(request.form.get("species")),
        "species_other": _clean(request.form.get("species_other")),
        "breed": _clean(request.form.get("breed")),
        "sex": _clean(request.form.get("sex")),
        "age": _clean(request.form.get("age")),
        "age_unit": _clean(request.form.get("age_unit")) or "years",
        "whatsapp_no": _clean(request.form.get("whatsapp_no")),
        "email": _clean(request.form.get("email")),
        "referring_doctor": _clean(request.form.get("referring_doctor")),
        "hospital_name": _clean(request.form.get("hospital_name")),
    }
    if data["species"] == "Other":
        data["species"] = data["species_other"]
    errors = []
    if not data["animal_name"]:
        errors.append("Animal name is required.")
    if data["patient_type"] not in db.PATIENT_TYPES:
        errors.append("Choose a patient type.")
    if not data["species"]:
        errors.append("Species is required.")
    if not _valid_whatsapp(data["whatsapp_no"]):
        errors.append("Mobile number should contain 10 to 15 digits.")
    if not _valid_email(data["email"]):
        errors.append("Email address looks invalid.")
    return data, errors


def _range_from_form():
    data = {
        "test_code": _clean(request.form.get("test_code")).upper(),
        "test_name": _clean(request.form.get("test_name")),
        "species": _clean(request.form.get("species")),
        "species_other": _clean(request.form.get("species_other")),
        "department": _clean(request.form.get("department")),
        "unit": _clean(request.form.get("unit")),
        "unit_other": _clean(request.form.get("unit_other")),
        "interpretation": _clean(request.form.get("interpretation")),
        "notes": _clean(request.form.get("notes")),
    }
    if data["species"] == "Other":
        data["species"] = data["species_other"]
    if data["unit"] == "Other":
        data["unit"] = data["unit_other"]
    errors = []
    try:
        data["low_value"] = _optional_float(request.form.get("low_value"))
        data["high_value"] = _optional_float(request.form.get("high_value"))
    except ValueError:
        errors.append("Low and high limits must be numbers.")
        data["low_value"] = None
        data["high_value"] = None
    try:
        data["rate"] = _amount(request.form.get("rate"))
    except ValueError:
        errors.append("Rate must be a number of rupees, zero or more.")
        data["rate"] = 0
    if not data["test_code"]:
        errors.append("Short name is required.")
    if not data["test_name"]:
        errors.append("Test name is required.")
    if not data["species"]:
        errors.append("Species is required.")
    if not data["unit"]:
        errors.append("Unit is required.")
    low, high = data.get("low_value"), data.get("high_value")
    if low is not None and high is not None and low > high:
        errors.append("Low limit cannot be above the high limit.")
    if low is None and high is None:
        errors.append("Enter at least one end of the normal range.")
    return data, errors


def _next_accession(conn, on_date):
    prefix = "HR-{}-".format(on_date.strftime("%Y%m%d"))
    row = conn.execute(
        """
        SELECT accession_no FROM samples
        WHERE accession_no LIKE ?
        ORDER BY accession_no DESC LIMIT 1
        """,
        (prefix + "%",),
    ).fetchone()
    seq = 1
    if row:
        seq = int(row["accession_no"].rsplit("-", 1)[-1]) + 1
    return "{}{:04d}".format(prefix, seq)


def _catalog_tests(conn):
    return conn.execute(
        """
        SELECT test_code, MIN(test_name) AS test_name, MIN(department) AS department
        FROM reference_ranges
        GROUP BY test_code
        ORDER BY department, test_name
        """
    ).fetchall()


def _apply_panel(conn, sample, panel_id):
    panel = conn.execute("SELECT * FROM panels WHERE id = ?", (panel_id,)).fetchone()
    if panel is None:
        return None
    tests = conn.execute(
        """
        SELECT test_code, sort_order FROM panel_tests
        WHERE panel_id = ?
        ORDER BY sort_order, id
        """,
        (panel_id,),
    ).fetchall()
    added = []
    already = []
    unavailable = []
    for item in tests:
        code = item["test_code"]
        exists = conn.execute(
            "SELECT id FROM results WHERE sample_id = ? AND test_code = ?",
            (sample["id"], code),
        ).fetchone()
        if exists:
            already.append(code)
            continue
        ref = conn.execute(
            """
            SELECT * FROM reference_ranges
            WHERE test_code = ? AND species = ?
            """,
            (code, sample["species"]),
        ).fetchone()
        if ref is None:
            unavailable.append(code)
            continue
        conn.execute(
            """
            INSERT INTO results (
                sample_id, test_code, test_name, department, value_text, value_num,
                unit, low_value, high_value, flag, comment, panel_name, sort_order,
                interpretation
            ) VALUES (?, ?, ?, ?, '', NULL, ?, ?, ?, NULL, '', ?, ?, ?)
            """,
            (
                sample["id"],
                code,
                ref["test_name"],
                ref["department"],
                ref["unit"],
                ref["low_value"],
                ref["high_value"],
                panel["name"],
                item["sort_order"],
                ref["interpretation"],
            ),
        )
        added.append(ref["test_name"])
    if added and sample["status"] == "Received":
        conn.execute(
            "UPDATE samples SET status = 'In progress' WHERE id = ?",
            (sample["id"],),
        )
    return {
        "name": panel["name"],
        "rate": panel["rate"],
        "added": added,
        "already": already,
        "unavailable": unavailable,
    }


def _report_text(settings, sample, patient, results, machine_runs=()):
    lines = [
        settings.get("lab_name") or "Eosnox Labs",
        "Laboratory report, HR No. {}".format(sample["accession_no"]),
        "Patient: {} ({})".format(patient["animal_name"], patient["species"]),
    ]
    if patient["owner_name"]:
        lines.append("Owner: {}".format(patient["owner_name"]))
    if sample["referring_doctor"]:
        lines.append("Referring doctor: {}".format(sample["referring_doctor"]))
    if sample["hospital_name"]:
        lines.append("Hospital: {}".format(sample["hospital_name"]))
    lines.append("")
    for result in results:
        flag = {"H": "High", "L": "Low", "N": "Normal"}.get(result["flag"], "")
        span = _range_label(result["low_value"], result["high_value"])
        shown = result["value_text"] if (result["value_text"] or "").strip() else "Pending"
        lines.append(
            "{}: {} {}  range {} {}".format(
                result["test_name"],
                shown,
                result["unit"] or "",
                span,
                flag,
            ).rstrip()
        )
    for run in machine_runs:
        lines.append("")
        lines.append("{} ({})".format(run["department"] or run["instrument_name"], run["instrument_name"]))
        for value in run["values"]:
            flag = {"H": "High", "L": "Low"}.get(value["flag"], "")
            lines.append(
                "{}: {} {}  range {} {}".format(
                    value["name"] or value["code"], value["value_text"], value["unit"] or "",
                    value["range_text"] or "-", flag,
                ).rstrip()
            )
    return "\n".join(lines)


def _range_label(low, high):
    if low is None and high is None:
        return "—"
    if low is None:
        return "< {}".format(_trim(high))
    if high is None:
        return "> {}".format(_trim(low))
    return "{} – {}".format(_trim(low), _trim(high))


def _trim(number):
    if number is None:
        return ""
    if isinstance(number, str):
        # A form being redisplayed after a typing mistake.
        try:
            number = float(number)
        except ValueError:
            return number
    text = "{:.4f}".format(number).rstrip("0").rstrip(".")
    return text


def _money(number):
    """Indian digit grouping: 123456.5 -> 1,23,456.50."""
    number = float(number or 0)
    sign = "-" if number < 0 else ""
    whole, frac = "{:.2f}".format(abs(number)).split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return "{}{}.{}".format(sign, whole, frac)


app.jinja_env.filters["trimnum"] = _trim
app.jinja_env.filters["rangelabel"] = _range_label
app.jinja_env.filters["money"] = _money
app.jinja_env.filters["fromjson"] = lambda text: json.loads(text or "{}")

PUBLIC_ENDPOINTS = {"login", "static"}


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.full_path))
    return None


@app.context_processor
def inject_nav():
    with db.connect() as conn:
        settings = db.get_settings(conn)
        unlinked = conn.execute(
            "SELECT COUNT(*) AS n FROM instrument_runs WHERE sample_id IS NULL AND kind = 'patient'"
        ).fetchone()["n"]
    name = settings.get("lab_name") or "Eosnox Labs"
    initials = "".join(word[0] for word in name.split()[:2]).upper() or "EL"
    return {
        "lab_year": date.today().year,
        "instrument_unlinked": unlinked,
        "lab": settings,
        "lab_initials": initials,
        "current_user": session.get("username"),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    target = request.args.get("next") or ""
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("dashboard")
    if request.method == "POST":
        username = _clean(request.form.get("username"))
        password = request.form.get("password") or ""
        with db.connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(target)
        flash("Username or password is incorrect.", "error")
    return render_template("login.html", next=target)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/")
def dashboard():
    with db.connect() as conn:
        settings = db.get_settings(conn)
        today = date.today().isoformat()
        dues = sum(row["due"] for row in _registration_rows(conn) if row["due"] > 0)
        counts = {
            "today_registrations": conn.execute(
                "SELECT COUNT(*) AS n FROM samples WHERE collected_on = ?", (today,)
            ).fetchone()["n"],
            "today_collected": conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS n FROM payments WHERE paid_on = ?",
                (today,),
            ).fetchone()["n"],
            "open_samples": conn.execute(
                "SELECT COUNT(*) AS n FROM samples WHERE status != 'Reported'"
            ).fetchone()["n"],
            "dues": dues,
        }
        recent = conn.execute(
            """
            SELECT s.*, p.animal_name, p.species, p.patient_type
            FROM samples s
            JOIN patients p ON p.id = s.patient_id
            ORDER BY s.id DESC LIMIT 6
            """
        ).fetchall()
    return render_template(
        "dashboard.html", settings=settings, counts=counts, recent=recent
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    with db.connect() as conn:
        current = db.get_settings(conn)
        if request.method == "POST" and request.form.get("action") == "password":
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?", (session.get("user_id"),)
            ).fetchone()
            new = request.form.get("new_password") or ""
            if user is None or not check_password_hash(
                user["password_hash"], request.form.get("current_password") or ""
            ):
                flash("Current password is incorrect.", "error")
            elif len(new) < 4:
                flash("New password must be at least 4 characters.", "error")
            elif new != request.form.get("confirm_password"):
                flash("New passwords do not match.", "error")
            else:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new), user["id"]),
                )
                conn.commit()
                flash("Password changed.")
            return redirect(url_for("settings"))
        if request.method == "POST" and request.form.get("action") != "report_layout":
            db.save_settings(
                conn,
                {
                    "lab_name": _clean(request.form.get("lab_name")) or "Eosnox Labs",
                    "lab_tagline": _clean(request.form.get("lab_tagline")),
                    "lab_address": _clean(request.form.get("lab_address")),
                    "lab_phone": _clean(request.form.get("lab_phone")),
                    "lab_email": _clean(request.form.get("lab_email")),
                },
            )
            conn.commit()
            flash("Laboratory details saved.")
            return redirect(url_for("settings"))
        if request.method == "POST" and request.form.get("action") == "report_layout":
            db.save_settings(
                conn,
                {
                    "report_header_note": request.form.get("report_header_note") or "",
                    "report_footer": request.form.get("report_footer") or "",
                    "report_sign_left": request.form.get("report_sign_left") or "",
                    "report_sign_right": request.form.get("report_sign_right") or "",
                    "report_show_histograms": "1" if request.form.get("report_show_histograms") else "0",
                },
            )
            conn.commit()
            flash("Report layout saved.")
            return redirect(url_for("settings"))
    return render_template("settings.html", settings=current)


@app.route("/patients")
def patients():
    q = _clean(request.args.get("q"))
    species = _clean(request.args.get("species"))
    patient_type = _clean(request.args.get("patient_type"))
    sql = "SELECT * FROM patients WHERE 1=1"
    params = []
    if q:
        like = "%{}%".format(q)
        sql += """
            AND (
                animal_name LIKE ? OR owner_name LIKE ? OR whatsapp_no LIKE ?
                OR email LIKE ? OR referring_doctor LIKE ? OR hospital_name LIKE ?
                OR breed LIKE ?
            )
        """
        params.extend([like] * 7)
    if species:
        sql += " AND species = ?"
        params.append(species)
    if patient_type:
        sql += " AND patient_type = ?"
        params.append(patient_type)
    sql += " ORDER BY id DESC"
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        species_options = [
            row["species"]
            for row in conn.execute(
                "SELECT DISTINCT species FROM patients ORDER BY species"
            ).fetchall()
        ]
    return render_template(
        "patients.html",
        patients=rows,
        q=q,
        species=species,
        patient_type=patient_type,
        species_options=species_options,
        patient_types=db.PATIENT_TYPES,
    )


@app.route("/patients/new")
def patient_new():
    return redirect(url_for("registration_new"))


@app.route("/patients/<int:patient_id>/edit", methods=["GET", "POST"])
def patient_edit(patient_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()
        if row is None:
            flash("Patient not found.", "error")
            return redirect(url_for("patients"))
        if request.method == "POST":
            data, errors = _patient_from_form()
            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                conn.execute(
                    """
                    UPDATE patients SET
                        animal_name=?, owner_name=?, patient_type=?, species=?, breed=?,
                        sex=?, age=?, age_unit=?, whatsapp_no=?, email=?,
                        referring_doctor=?, hospital_name=?
                    WHERE id=?
                    """,
                    (
                        data["animal_name"],
                        data["owner_name"],
                        data["patient_type"],
                        data["species"],
                        data["breed"],
                        data["sex"],
                        data["age"],
                        data["age_unit"],
                        data["whatsapp_no"],
                        data["email"],
                        data["referring_doctor"],
                        data["hospital_name"],
                        patient_id,
                    ),
                )
                conn.commit()
                flash("Patient details updated.")
                return redirect(url_for("patient_edit", patient_id=patient_id))
            patient = data
        else:
            patient = dict(row)
    doctors, hospitals = _contact_suggestions()
    species_known = patient.get("species") in db.SPECIES
    return render_template(
        "patient_form.html",
        patient=patient,
        patient_id=patient_id,
        action=url_for("patient_edit", patient_id=patient_id),
        title="Edit patient",
        patient_types=db.PATIENT_TYPES,
        species_list=db.SPECIES,
        sexes=db.SEXES,
        age_units=db.AGE_UNITS,
        doctors=doctors,
        hospitals=hospitals,
        species_known=species_known,
    )


@app.route("/patients/<int:patient_id>/delete", methods=["POST"])
def patient_delete(patient_id):
    with db.connect() as conn:
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM samples WHERE patient_id = ?", (patient_id,)
        ).fetchone()["n"]
        if used:
            flash("This patient has laboratory samples and cannot be deleted.", "error")
            return redirect(url_for("patient_edit", patient_id=patient_id))
        conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
        conn.commit()
    flash("Patient removed.")
    return redirect(url_for("patients"))


def _contact_suggestions():
    with db.connect() as conn:
        doctors = [
            row["name"]
            for row in conn.execute("SELECT name FROM ref_doctors ORDER BY name")
        ]
        hospitals = [
            row["name"]
            for row in conn.execute("SELECT name FROM ref_hospitals ORDER BY name")
        ]
    return doctors, hospitals


@app.route("/ranges")
def ranges():
    species = _clean(request.args.get("species"))
    q = _clean(request.args.get("q"))
    sql = "SELECT * FROM reference_ranges WHERE 1=1"
    params = []
    if species:
        sql += " AND species = ?"
        params.append(species)
    if q:
        sql += " AND (test_code LIKE ? OR test_name LIKE ? OR department LIKE ?)"
        like = "%{}%".format(q)
        params.extend([like, like, like])
    sql += " ORDER BY species, department, test_name"
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        species_options = [
            row["species"]
            for row in conn.execute(
                "SELECT DISTINCT species FROM reference_ranges ORDER BY species"
            ).fetchall()
        ]
    return render_template(
        "ranges.html",
        ranges=rows,
        species=species,
        q=q,
        species_options=species_options,
    )


@app.route("/ranges/new", methods=["GET", "POST"])
def range_new():
    data = {
        "test_code": "",
        "test_name": "",
        "species": "Canine",
        "department": "Chemistry",
        "unit": "",
        "low_value": "",
        "high_value": "",
        "interpretation": "",
        "rate": "",
        "notes": "",
    }
    if request.method == "POST":
        data, errors = _range_from_form()
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            try:
                with db.connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO reference_ranges (
                            test_code, test_name, species, department, unit,
                            low_value, high_value, notes, interpretation, rate
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            data["test_code"],
                            data["test_name"],
                            data["species"],
                            data["department"],
                            data["unit"],
                            data["low_value"],
                            data["high_value"],
                            data["notes"],
                            data["interpretation"],
                            data["rate"],
                        ),
                    )
                    conn.commit()
                flash("Test {} saved for {}.".format(data["test_code"], data["species"]))
                return redirect(url_for("ranges", species=data["species"]))
            except sqlite3.IntegrityError:
                flash(
                    "Test {} already exists for {}.".format(
                        data["test_code"], data["species"]
                    ),
                    "error",
                )
    return render_template(
        "range_form.html",
        item=data,
        action=url_for("range_new"),
        title="Create test",
        species_list=db.SPECIES,
        units=db.UNITS,
    )


@app.route("/ranges/<int:range_id>/edit", methods=["GET", "POST"])
def range_edit(range_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM reference_ranges WHERE id = ?", (range_id,)
        ).fetchone()
        if row is None:
            flash("Test not found.", "error")
            return redirect(url_for("ranges"))
        if request.method == "POST":
            data, errors = _range_from_form()
            if errors:
                for error in errors:
                    flash(error, "error")
                item = data
            else:
                try:
                    conn.execute(
                        """
                        UPDATE reference_ranges SET
                            test_code=?, test_name=?, species=?, department=?, unit=?,
                            low_value=?, high_value=?, notes=?, interpretation=?, rate=?
                        WHERE id=?
                        """,
                        (
                            data["test_code"],
                            data["test_name"],
                            data["species"],
                            data["department"],
                            data["unit"],
                            data["low_value"],
                            data["high_value"],
                            data["notes"],
                            data["interpretation"],
                            data["rate"],
                            range_id,
                        ),
                    )
                    conn.commit()
                    flash("Test updated. Existing reports keep the range and rate that applied when they were registered.")
                    return redirect(url_for("ranges", species=data["species"]))
                except sqlite3.IntegrityError:
                    flash(
                        "Test {} already exists for {}.".format(
                            data["test_code"], data["species"]
                        ),
                        "error",
                    )
                    item = data
        else:
            item = dict(row)
    return render_template(
        "range_form.html",
        item=item,
        action=url_for("range_edit", range_id=range_id),
        title="Edit test",
        species_list=db.SPECIES,
        species_known=item.get("species") in db.SPECIES,
        units=db.UNITS,
    )


@app.route("/ranges/<int:range_id>/delete", methods=["POST"])
def range_delete(range_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM reference_ranges WHERE id = ?", (range_id,))
        conn.commit()
    flash("Test removed.")
    return redirect(url_for("ranges"))


def _panel_tests_from_form(conn):
    chosen = []
    seen = set()
    for code in request.form.getlist("test_codes"):
        code = _clean(code).upper()
        if not code or code in seen:
            continue
        seen.add(code)
        chosen.append(code)
    return chosen


@app.route("/panels")
def panels():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM panels ORDER BY sort_order, name"
        ).fetchall()
        grouped = []
        for row in rows:
            tests = conn.execute(
                """
                SELECT pt.test_code, MIN(r.test_name) AS test_name
                FROM panel_tests pt
                LEFT JOIN reference_ranges r ON r.test_code = pt.test_code
                WHERE pt.panel_id = ?
                GROUP BY pt.test_code
                ORDER BY pt.sort_order, pt.id
                """,
                (row["id"],),
            ).fetchall()
            grouped.append({"panel": row, "tests": tests})
    return render_template("panels.html", grouped=grouped)


@app.route("/panels/new", methods=["GET", "POST"])
def panel_new():
    with db.connect() as conn:
        catalog = _catalog_tests(conn)
        draft = {"name": "", "department": "", "notes": "", "rate": ""}
        selected = set()
        if request.method == "POST":
            draft = {
                "name": _clean(request.form.get("name")),
                "department": _clean(request.form.get("department")),
                "notes": _clean(request.form.get("notes")),
                "rate": _clean(request.form.get("rate")),
            }
            codes = _panel_tests_from_form(conn)
            selected = set(codes)
            try:
                rate = _amount(draft["rate"])
            except ValueError:
                rate = None
            if not draft["name"]:
                flash("Profile name is required.", "error")
            elif not codes:
                flash("Choose at least one test.", "error")
            elif rate is None:
                flash("Rate must be a number of rupees, zero or more.", "error")
            else:
                try:
                    cur = conn.execute(
                        "INSERT INTO panels (name, department, notes, rate) VALUES (?, ?, ?, ?)",
                        (draft["name"], draft["department"], draft["notes"], rate),
                    )
                    conn.executemany(
                        """
                        INSERT INTO panel_tests (panel_id, test_code, sort_order)
                        VALUES (?, ?, ?)
                        """,
                        [
                            (cur.lastrowid, code, index * 10)
                            for index, code in enumerate(codes)
                        ],
                    )
                    conn.commit()
                    flash("Profile {} saved.".format(draft["name"]))
                    return redirect(url_for("panels"))
                except sqlite3.IntegrityError:
                    flash("A profile named {} already exists.".format(draft["name"]), "error")
    return render_template(
        "panel_form.html",
        title="Create profile",
        action=url_for("panel_new"),
        panel=draft,
        catalog=catalog,
        selected=selected,
    )


@app.route("/panels/<int:panel_id>/edit", methods=["GET", "POST"])
def panel_edit(panel_id):
    with db.connect() as conn:
        panel = conn.execute(
            "SELECT * FROM panels WHERE id = ?", (panel_id,)
        ).fetchone()
        if panel is None:
            flash("Profile not found.", "error")
            return redirect(url_for("panels"))
        catalog = _catalog_tests(conn)
        if request.method == "POST":
            name = _clean(request.form.get("name"))
            department = _clean(request.form.get("department"))
            notes = _clean(request.form.get("notes"))
            rate_text = _clean(request.form.get("rate"))
            codes = _panel_tests_from_form(conn)
            try:
                rate = _amount(rate_text)
            except ValueError:
                rate = None
            if not name:
                flash("Profile name is required.", "error")
            elif not codes:
                flash("Choose at least one test.", "error")
            elif rate is None:
                flash("Rate must be a number of rupees, zero or more.", "error")
            else:
                try:
                    conn.execute(
                        """
                        UPDATE panels SET name = ?, department = ?, notes = ?, rate = ?
                        WHERE id = ?
                        """,
                        (name, department, notes, rate, panel_id),
                    )
                    conn.execute(
                        "DELETE FROM panel_tests WHERE panel_id = ?", (panel_id,)
                    )
                    conn.executemany(
                        """
                        INSERT INTO panel_tests (panel_id, test_code, sort_order)
                        VALUES (?, ?, ?)
                        """,
                        [
                            (panel_id, code, index * 10)
                            for index, code in enumerate(codes)
                        ],
                    )
                    conn.commit()
                    flash("Profile {} updated.".format(name))
                    return redirect(url_for("panels"))
                except sqlite3.IntegrityError:
                    flash("A profile named {} already exists.".format(name), "error")
            panel = {
                "id": panel_id,
                "name": name,
                "department": department,
                "notes": notes,
                "rate": rate_text,
            }
            selected = set(codes)
        else:
            selected = {
                row["test_code"]
                for row in conn.execute(
                    "SELECT test_code FROM panel_tests WHERE panel_id = ?",
                    (panel_id,),
                )
            }
    return render_template(
        "panel_form.html",
        title="Edit profile",
        action=url_for("panel_edit", panel_id=panel_id),
        panel=panel,
        panel_id=panel_id,
        catalog=catalog,
        selected=selected,
    )


@app.route("/panels/<int:panel_id>/delete", methods=["POST"])
def panel_delete(panel_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM panels WHERE id = ?", (panel_id,))
        conn.commit()
    flash("Profile removed. Registrations that already used it keep their tests and bill.")
    return redirect(url_for("panels"))


@app.route("/samples")
def samples():
    status = _clean(request.args.get("status"))
    q = _clean(request.args.get("q"))
    sql = """
        SELECT s.*, p.animal_name, p.species, p.patient_type, p.owner_name
        FROM samples s
        JOIN patients p ON p.id = s.patient_id
        WHERE 1=1
    """
    params = []
    if status:
        sql += " AND s.status = ?"
        params.append(status)
    if q:
        like = "%{}%".format(q)
        sql += """
            AND (
                s.accession_no LIKE ? OR p.animal_name LIKE ? OR p.owner_name LIKE ?
                OR s.hospital_name LIKE ? OR s.referring_doctor LIKE ?
            )
        """
        params.extend([like] * 5)
    sql += " ORDER BY s.id DESC"
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return render_template(
        "samples.html",
        samples=rows,
        status=status,
        q=q,
        statuses=db.SAMPLE_STATUSES,
    )


@app.route("/samples/new")
def sample_new():
    patient_id = request.args.get("patient_id", type=int)
    return redirect(url_for("registration_new", patient_id=patient_id))


@app.route("/samples/<int:sample_id>", methods=["GET", "POST"])
def sample_detail(sample_id):
    with db.connect() as conn:
        sample = conn.execute(
            """
            SELECT s.*, p.animal_name, p.owner_name, p.patient_type, p.species,
                   p.breed, p.sex, p.age, p.age_unit, p.whatsapp_no, p.email
            FROM samples s
            JOIN patients p ON p.id = s.patient_id
            WHERE s.id = ?
            """,
            (sample_id,),
        ).fetchone()
        if sample is None:
            flash("Sample not found.", "error")
            return redirect(url_for("samples"))

        if request.method == "POST" and request.form.get("action") == "add_panel":
            summary = _apply_panel(conn, sample, request.form.get("panel_id", type=int))
            if summary is None:
                flash("Choose a test panel.", "error")
            else:
                if summary["added"]:
                    _add_bill_item(
                        conn, sample_id, "Profile", str(request.form.get("panel_id")),
                        summary["name"], summary["rate"],
                    )
                conn.commit()
                if summary["added"]:
                    flash(
                        "{} added {} tests and Rs {} to the bill.".format(
                            summary["name"], len(summary["added"]), _money(summary["rate"])
                        )
                    )
                if summary["already"]:
                    flash(
                        "Already on this sample: {}.".format(
                            ", ".join(summary["already"])
                        )
                    )
                if summary["unavailable"]:
                    flash(
                        "No {} range for: {}.".format(
                            sample["species"], ", ".join(summary["unavailable"])
                        ),
                        "error",
                    )
                if not summary["added"] and not summary["unavailable"] and not summary["already"]:
                    flash("That panel has no tests.", "error")
            return redirect(url_for("sample_detail", sample_id=sample_id))

        if request.method == "POST" and request.form.get("action") == "save_result":
            result_id = request.form.get("result_id", type=int)
            row = conn.execute(
                "SELECT * FROM results WHERE id = ? AND sample_id = ?",
                (result_id, sample_id),
            ).fetchone()
            if row is None:
                flash("Result not found.", "error")
                return redirect(url_for("sample_detail", sample_id=sample_id))
            try:
                value_text, value_num = _parse_result(request.form.get("value_text"))
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("sample_detail", sample_id=sample_id))
            flag = _flag(value_num, row["low_value"], row["high_value"])
            conn.execute(
                """
                UPDATE results
                SET value_text = ?, value_num = ?, flag = ?, comment = ?
                WHERE id = ?
                """,
                (
                    value_text,
                    value_num,
                    flag,
                    _clean(request.form.get("comment")),
                    result_id,
                ),
            )
            conn.commit()
            flash("{} saved.".format(row["test_name"]))
            return redirect(url_for("sample_detail", sample_id=sample_id))

        if request.method == "POST" and request.form.get("action") == "payment":
            bill = _billing(conn, sample_id)
            mode = _clean(request.form.get("mode"))
            try:
                amount = _amount(request.form.get("amount"))
            except ValueError:
                amount = 0
            if mode not in db.PAYMENT_MODES:
                flash("Choose Cash or UPI.", "error")
            elif amount <= 0:
                flash("Enter the amount received.", "error")
            elif amount > bill["due"]:
                flash("Amount is more than the due of Rs {}.".format(_money(bill["due"])), "error")
            else:
                conn.execute(
                    "INSERT INTO payments (sample_id, paid_on, mode, amount) VALUES (?, ?, ?, ?)",
                    (sample_id, date.today().isoformat(), mode, amount),
                )
                conn.commit()
                flash("Received Rs {} by {}.".format(_money(amount), mode))
            return redirect(url_for("sample_detail", sample_id=sample_id))

        if request.method == "POST" and request.form.get("action") == "discount":
            bill = _billing(conn, sample_id)
            try:
                discount = _amount(request.form.get("discount"))
            except ValueError:
                discount = None
            if discount is None or discount > bill["total"]:
                flash("Discount must be between 0 and the total amount.", "error")
            elif bill["total"] - discount < bill["paid"]:
                flash("That discount would make the net less than what was already received.", "error")
            else:
                conn.execute(
                    "UPDATE samples SET discount = ? WHERE id = ?", (discount, sample_id)
                )
                conn.commit()
                flash("Discount set to Rs {}.".format(_money(discount)))
            return redirect(url_for("sample_detail", sample_id=sample_id))

        if request.method == "POST" and request.form.get("action") == "remove_bill_item":
            item_id = request.form.get("item_id", type=int)
            bill = _billing(conn, sample_id)
            item = next((row for row in bill["items"] if row["id"] == item_id), None)
            if item is None:
                flash("Bill line not found.", "error")
            elif bill["total"] - item["amount"] - bill["discount"] < bill["paid"]:
                flash("Removing this line would leave less than what was already received.", "error")
            else:
                conn.execute("DELETE FROM bill_items WHERE id = ?", (item_id,))
                conn.commit()
                flash("{} removed from the bill. Its result lines are unchanged.".format(item["name"]))
            return redirect(url_for("sample_detail", sample_id=sample_id))

        if request.method == "POST" and request.form.get("action") == "status":
            status = _clean(request.form.get("status"))
            if status not in db.SAMPLE_STATUSES:
                flash("Unknown status.", "error")
            else:
                conn.execute(
                    "UPDATE samples SET status = ? WHERE id = ?", (status, sample_id)
                )
                conn.commit()
                flash("Sample marked {}.".format(status))
            return redirect(url_for("sample_detail", sample_id=sample_id))

        if request.method == "POST" and request.form.get("action") == "add_result":
            test_code = _clean(request.form.get("test_code")).upper()
            comment = _clean(request.form.get("comment"))
            manual = test_code in ("", "__MANUAL__")
            if manual:
                test_code = _clean(request.form.get("manual_code")).upper()
            try:
                value_text, value_num = _parse_result(request.form.get("value_text"))
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("sample_detail", sample_id=sample_id))

            ref = None
            if test_code and not manual:
                ref = conn.execute(
                    """
                    SELECT * FROM reference_ranges
                    WHERE test_code = ? AND species = ?
                    """,
                    (test_code, sample["species"]),
                ).fetchone()
            manual_name = _clean(request.form.get("test_name"))
            manual_unit = _clean(request.form.get("unit"))
            if ref is None and (not test_code or not manual_name):
                flash("Choose a test, or enter a code and name for a test without a stored range.", "error")
                return redirect(url_for("sample_detail", sample_id=sample_id))

            test_name = ref["test_name"] if ref else manual_name
            department = ref["department"] if ref else _clean(request.form.get("department"))
            unit = ref["unit"] if ref else manual_unit
            low = ref["low_value"] if ref else None
            high = ref["high_value"] if ref else None
            if ref is None:
                try:
                    low = _optional_float(request.form.get("low_value"))
                    high = _optional_float(request.form.get("high_value"))
                except ValueError:
                    flash("Manual range limits must be numbers.", "error")
                    return redirect(url_for("sample_detail", sample_id=sample_id))
            flag = _flag(value_num, low, high)
            try:
                conn.execute(
                    """
                    INSERT INTO results (
                        sample_id, test_code, test_name, department, value_text, value_num,
                        unit, low_value, high_value, flag, comment, panel_name, sort_order,
                        interpretation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sample_id,
                        test_code or "MISC",
                        test_name,
                        department,
                        value_text,
                        value_num,
                        unit,
                        low,
                        high,
                        flag,
                        comment,
                        None,
                        500,
                        ref["interpretation"] if ref else None,
                    ),
                )
                if ref is not None and ref["rate"]:
                    _add_bill_item(conn, sample_id, "Test", test_code, test_name, ref["rate"])
                if sample["status"] == "Received":
                    conn.execute(
                        "UPDATE samples SET status = 'In progress' WHERE id = ?",
                        (sample_id,),
                    )
                conn.commit()
                flash("{} recorded.".format(test_name))
            except sqlite3.IntegrityError:
                flash("That test is already on this sample. Edit the existing line.", "error")
            return redirect(url_for("sample_detail", sample_id=sample_id))

        results = conn.execute(
            """
            SELECT * FROM results WHERE sample_id = ?
            ORDER BY department, sort_order, test_name
            """,
            (sample_id,),
        ).fetchall()
        bill = _billing(conn, sample_id)
        panels = conn.execute(
            "SELECT * FROM panels ORDER BY sort_order, name"
        ).fetchall()
        catalog = conn.execute(
            """
            SELECT * FROM reference_ranges
            WHERE species = ?
            ORDER BY department, test_name
            """,
            (sample["species"],),
        ).fetchall()
        sample = conn.execute(
            """
            SELECT s.*, p.animal_name, p.owner_name, p.patient_type, p.species,
                   p.breed, p.sex, p.age, p.age_unit, p.whatsapp_no, p.email, p.id AS patient_id
            FROM samples s
            JOIN patients p ON p.id = s.patient_id
            WHERE s.id = ?
            """,
            (sample_id,),
        ).fetchone()
        machine_runs = store.runs_for_sample(conn, sample_id)
        machine_sections = store.report_sections(machine_runs)
        waiting = conn.execute(
            "SELECT COUNT(*) AS n FROM instrument_runs WHERE sample_id IS NULL AND kind = 'patient'"
        ).fetchone()["n"]
    return render_template(
        "sample_detail.html",
        sample=sample,
        machine_runs=machine_runs,
        machine_sections=machine_sections,
        waiting_runs=waiting,
        results=results,
        catalog=catalog,
        panels=panels,
        statuses=db.SAMPLE_STATUSES,
        bill=bill,
        payment_modes=db.PAYMENT_MODES,
    )


@app.route("/results/<int:result_id>/delete", methods=["POST"])
def result_delete(result_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT sample_id FROM results WHERE id = ?", (result_id,)
        ).fetchone()
        if row is None:
            flash("Result not found.", "error")
            return redirect(url_for("samples"))
        conn.execute("DELETE FROM results WHERE id = ?", (result_id,))
        conn.commit()
        sample_id = row["sample_id"]
    flash("Result removed.")
    return redirect(url_for("sample_detail", sample_id=sample_id))


@app.route("/samples/<int:sample_id>/report")
def sample_report(sample_id):
    with db.connect() as conn:
        settings = db.get_settings(conn)
        sample = conn.execute(
            """
            SELECT s.*, p.animal_name, p.owner_name, p.patient_type, p.species,
                   p.breed, p.sex, p.age, p.age_unit, p.whatsapp_no, p.email, p.id AS patient_id
            FROM samples s
            JOIN patients p ON p.id = s.patient_id
            WHERE s.id = ?
            """,
            (sample_id,),
        ).fetchone()
        if sample is None:
            flash("Sample not found.", "error")
            return redirect(url_for("samples"))
        results = conn.execute(
            """
            SELECT r.*, COALESCE(r.panel_name, r.department, 'Other tests') AS section
            FROM results r
            LEFT JOIN panels p ON p.name = r.panel_name
            WHERE r.sample_id = ?
            ORDER BY r.panel_name IS NULL, COALESCE(p.sort_order, 999), r.panel_name,
                     r.department, r.sort_order, r.test_name
            """,
            (sample_id,),
        ).fetchall()
        patient = sample
        machine_runs = store.runs_for_sample(conn, sample_id)
        machine_sections = store.report_sections(machine_runs)
    body = _report_text(settings, sample, patient, results, machine_runs)
    whatsapp = None
    digits = _whatsapp_digits(sample["whatsapp_no"])
    if digits:
        whatsapp = "https://wa.me/{}?text={}".format(digits, quote(body))
    email_link = None
    if sample["email"]:
        email_link = "mailto:{}?subject={}&body={}".format(
            quote(sample["email"]),
            quote("Lab report {}".format(sample["accession_no"])),
            quote(body),
        )
    return render_template(
        "report.html",
        settings=settings,
        sample=sample,
        machine_sections=machine_sections,
        results=results,
        whatsapp=whatsapp,
        email_link=email_link,
    )


# ---------------------------------------------------------------------------
# Billing


def _add_bill_item(conn, sample_id, item_type, code, name, amount):
    conn.execute(
        """
        INSERT INTO bill_items (sample_id, item_type, code, name, amount)
        VALUES (?, ?, ?, ?, ?)
        """,
        (sample_id, item_type, code, name, amount or 0),
    )


def _add_test_result(conn, sample_id, ref, value_text="", value_num=None, comment=""):
    conn.execute(
        """
        INSERT INTO results (
            sample_id, test_code, test_name, department, value_text, value_num,
            unit, low_value, high_value, flag, comment, panel_name, sort_order,
            interpretation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 500, ?)
        """,
        (
            sample_id,
            ref["test_code"],
            ref["test_name"],
            ref["department"],
            value_text,
            value_num,
            ref["unit"],
            ref["low_value"],
            ref["high_value"],
            _flag(value_num, ref["low_value"], ref["high_value"]),
            comment,
            ref["interpretation"],
        ),
    )


def _billing(conn, sample_id):
    items = conn.execute(
        "SELECT * FROM bill_items WHERE sample_id = ? ORDER BY id", (sample_id,)
    ).fetchall()
    payments = conn.execute(
        "SELECT * FROM payments WHERE sample_id = ? ORDER BY paid_on, id",
        (sample_id,),
    ).fetchall()
    discount = conn.execute(
        "SELECT discount FROM samples WHERE id = ?", (sample_id,)
    ).fetchone()["discount"] or 0
    total = round(sum(item["amount"] for item in items), 2)
    net = round(max(total - discount, 0), 2)
    paid = round(sum(payment["amount"] for payment in payments), 2)
    return {
        "items": items,
        "payments": payments,
        "total": total,
        "discount": discount,
        "net": net,
        "paid": paid,
        "due": round(net - paid, 2),
    }


def _registration_rows(conn, date_from=None, date_to=None, doctor=None, hospital=None):
    """One row per registration with its bill worked out."""
    sql = """
        SELECT s.id, s.accession_no, s.collected_on, s.referring_doctor,
               s.hospital_name, s.discount, s.payment_mode, s.status,
               p.animal_name, p.owner_name, p.species,
               (SELECT COALESCE(SUM(amount), 0) FROM bill_items b
                 WHERE b.sample_id = s.id) AS total,
               (SELECT COALESCE(SUM(amount), 0) FROM payments y
                 WHERE y.sample_id = s.id) AS paid,
               (SELECT COUNT(*) FROM results r WHERE r.sample_id = s.id) AS tests
        FROM samples s
        JOIN patients p ON p.id = s.patient_id
        WHERE 1=1
    """
    params = []
    if date_from:
        sql += " AND s.collected_on >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND s.collected_on <= ?"
        params.append(date_to)
    if doctor is not None:
        sql += " AND s.referring_doctor = ?"
        params.append(doctor)
    if hospital is not None:
        sql += " AND s.hospital_name = ?"
        params.append(hospital)
    sql += " ORDER BY s.collected_on, s.accession_no"
    rows = []
    for row in conn.execute(sql, params).fetchall():
        item = dict(row)
        item["net"] = round(max(item["total"] - (item["discount"] or 0), 0), 2)
        item["due"] = round(item["net"] - item["paid"], 2)
        rows.append(item)
    return rows


def _totals(rows):
    keys = ("tests", "total", "discount", "net", "paid", "due")
    totals = {key: round(sum(row[key] or 0 for row in rows), 2) for key in keys}
    totals["count"] = len(rows)
    return totals


def _payments_by_mode(conn, date_from, date_to):
    rows = conn.execute(
        """
        SELECT mode, COUNT(*) AS n, COALESCE(SUM(amount), 0) AS amount
        FROM payments
        WHERE paid_on >= ? AND paid_on <= ?
        GROUP BY mode
        """,
        (date_from, date_to),
    ).fetchall()
    found = {row["mode"]: row for row in rows}
    modes = [
        {
            "mode": mode,
            "count": found[mode]["n"] if mode in found else 0,
            "amount": found[mode]["amount"] if mode in found else 0,
        }
        for mode in db.PAYMENT_MODES
    ]
    return modes, round(sum(mode["amount"] for mode in modes), 2)


def _iso_or(value, default):
    try:
        return date.fromisoformat(_clean(value)).isoformat()
    except ValueError:
        return default


def _date_window():
    today = date.today()
    date_from = _iso_or(request.args.get("from"), today.replace(day=1).isoformat())
    date_to = _iso_or(request.args.get("to"), today.isoformat())
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


# ---------------------------------------------------------------------------
# Registration


def _registration_defaults():
    return {
        "animal_name": "",
        "owner_name": "",
        "species": "Canine",
        "sex": "Unknown",
        "age": "",
        "age_unit": "years",
        "whatsapp_no": "",
        "email": "",
        "referring_doctor": "",
        "hospital_name": "",
        "collected_on": date.today().isoformat(),
        "discount": "",
        "payment_mode": "Cash",
        "received": "",
    }


def _registration_choices(conn):
    tests = conn.execute(
        """
        SELECT * FROM reference_ranges
        ORDER BY department, test_name
        """
    ).fetchall()
    profiles = []
    for panel in conn.execute(
        "SELECT * FROM panels ORDER BY sort_order, name"
    ).fetchall():
        codes = [
            row["test_code"]
            for row in conn.execute(
                "SELECT test_code FROM panel_tests WHERE panel_id = ? ORDER BY sort_order, id",
                (panel["id"],),
            )
        ]
        profiles.append({"panel": panel, "codes": codes})
    doctors = conn.execute("SELECT name FROM ref_doctors ORDER BY name").fetchall()
    hospitals = conn.execute("SELECT name FROM ref_hospitals ORDER BY name").fetchall()
    return tests, profiles, doctors, hospitals


@app.route("/registrations/new", methods=["GET", "POST"])
def registration_new():
    patient_id = request.values.get("patient_id", type=int)
    data = _registration_defaults()
    selected_tests = set()
    selected_profiles = set()
    with db.connect() as conn:
        existing = None
        if patient_id:
            existing = conn.execute(
                "SELECT * FROM patients WHERE id = ?", (patient_id,)
            ).fetchone()
            if existing is None:
                patient_id = None
            elif request.method == "GET":
                for key in (
                    "animal_name", "owner_name", "species", "sex", "age",
                    "age_unit", "whatsapp_no", "email", "referring_doctor",
                    "hospital_name",
                ):
                    data[key] = existing[key] or data[key]

        if request.method == "POST":
            for key in data:
                data[key] = _clean(request.form.get(key))
            data["age_unit"] = data["age_unit"] or "years"
            data["sex"] = data["sex"] or "Unknown"
            if data["species"] == "Other":
                data["species"] = _clean(request.form.get("species_other"))
            selected_tests = {
                int(value) for value in request.form.getlist("test_ids") if value.isdigit()
            }
            selected_profiles = {
                int(value) for value in request.form.getlist("panel_ids") if value.isdigit()
            }
            errors = []
            if not data["animal_name"]:
                errors.append("Patient name is required.")
            if not data["species"]:
                errors.append("Species is required.")
            if not _valid_whatsapp(data["whatsapp_no"]):
                errors.append("Mobile number should contain 10 to 15 digits.")
            if not _valid_email(data["email"]):
                errors.append("Email address looks invalid.")
            if data["payment_mode"] not in db.BILLING_MODES:
                errors.append("Choose Cash, UPI, or Credit.")
            try:
                collected = date.fromisoformat(data["collected_on"])
            except ValueError:
                errors.append("Registration date must be a real date.")
                collected = date.today()

            tests = []
            for test_id in sorted(selected_tests):
                ref = conn.execute(
                    "SELECT * FROM reference_ranges WHERE id = ?", (test_id,)
                ).fetchone()
                if ref is None:
                    continue
                if ref["species"] != data["species"]:
                    errors.append(
                        "{} is set up for {}, not {}.".format(
                            ref["test_name"], ref["species"], data["species"]
                        )
                    )
                    continue
                tests.append(ref)
            profiles = [
                row
                for row in (
                    conn.execute("SELECT * FROM panels WHERE id = ?", (panel_id,)).fetchone()
                    for panel_id in sorted(selected_profiles)
                )
                if row is not None
            ]
            if not tests and not profiles:
                errors.append("Select at least one test or profile.")

            gross = round(
                sum(ref["rate"] for ref in tests) + sum(row["rate"] for row in profiles), 2
            )
            try:
                discount = _amount(data["discount"])
                received = _amount(data["received"])
            except ValueError:
                errors.append("Discount and amount received must be numbers, zero or more.")
                discount = received = 0
            if data["payment_mode"] == "Credit":
                received = 0
            if discount > gross:
                errors.append("Discount cannot be more than the total amount.")
            net = round(max(gross - discount, 0), 2)
            if received > net:
                errors.append(
                    "Amount received ({}) is more than the net amount ({}).".format(
                        _money(received), _money(net)
                    )
                )

            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                patient_values = (
                    data["animal_name"],
                    data["owner_name"],
                    db.SPECIES_TYPES.get(data["species"], "Exotic")
                    if existing is None or existing["species"] != data["species"]
                    else existing["patient_type"],
                    data["species"],
                    data["sex"],
                    data["age"],
                    data["age_unit"],
                    data["whatsapp_no"],
                    data["email"],
                    data["referring_doctor"],
                    data["hospital_name"],
                )
                if existing is not None:
                    conn.execute(
                        """
                        UPDATE patients SET
                            animal_name=?, owner_name=?, patient_type=?, species=?,
                            sex=?, age=?, age_unit=?, whatsapp_no=?, email=?,
                            referring_doctor=?, hospital_name=?
                        WHERE id=?
                        """,
                        patient_values + (existing["id"],),
                    )
                    new_patient_id = existing["id"]
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO patients (
                            animal_name, owner_name, patient_type, species, sex,
                            age, age_unit, whatsapp_no, email, referring_doctor,
                            hospital_name
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        patient_values,
                    )
                    new_patient_id = cur.lastrowid

                accession = _next_accession(conn, collected)
                cur = conn.execute(
                    """
                    INSERT INTO samples (
                        accession_no, patient_id, collected_on, status,
                        referring_doctor, hospital_name, clinical_notes,
                        discount, payment_mode
                    ) VALUES (?, ?, ?, 'Received', ?, ?, '', ?, ?)
                    """,
                    (
                        accession,
                        new_patient_id,
                        collected.isoformat(),
                        data["referring_doctor"],
                        data["hospital_name"],
                        discount,
                        data["payment_mode"],
                    ),
                )
                sample_id = cur.lastrowid
                sample = conn.execute(
                    """
                    SELECT s.*, p.species FROM samples s
                    JOIN patients p ON p.id = s.patient_id
                    WHERE s.id = ?
                    """,
                    (sample_id,),
                ).fetchone()

                skipped = []
                for profile in profiles:
                    summary = _apply_panel(conn, sample, profile["id"])
                    if summary["added"]:
                        _add_bill_item(
                            conn, sample_id, "Profile", str(profile["id"]),
                            profile["name"], profile["rate"],
                        )
                    if summary["unavailable"]:
                        skipped.append(
                            "{} ({})".format(profile["name"], ", ".join(summary["unavailable"]))
                        )
                covered = []
                for ref in tests:
                    exists = conn.execute(
                        "SELECT id FROM results WHERE sample_id = ? AND test_code = ?",
                        (sample_id, ref["test_code"]),
                    ).fetchone()
                    if exists:
                        covered.append(ref["test_name"])
                        continue
                    _add_test_result(conn, sample_id, ref)
                    _add_bill_item(
                        conn, sample_id, "Test", ref["test_code"], ref["test_name"], ref["rate"]
                    )
                # Re-check the bill after skipping tests already covered by a profile.
                billed = _billing(conn, sample_id)
                if discount > billed["total"]:
                    conn.execute(
                        "UPDATE samples SET discount = ? WHERE id = ?",
                        (billed["total"], sample_id),
                    )
                    billed = _billing(conn, sample_id)
                received = min(received, billed["net"])
                if received > 0:
                    conn.execute(
                        """
                        INSERT INTO payments (sample_id, paid_on, mode, amount)
                        VALUES (?, ?, ?, ?)
                        """,
                        (sample_id, date.today().isoformat(), data["payment_mode"], received),
                    )
                conn.execute(
                    "UPDATE samples SET status = 'In progress' WHERE id = ? AND status = 'Received'",
                    (sample_id,),
                )
                conn.commit()
                flash("Registration {} saved.".format(accession))
                if covered:
                    flash(
                        "Not billed twice, already in a selected profile: {}.".format(
                            ", ".join(covered)
                        )
                    )
                if skipped:
                    flash(
                        "Left out tests with no {} setup: {}.".format(
                            data["species"], "; ".join(skipped)
                        ),
                        "error",
                    )
                return redirect(url_for("sample_bill", sample_id=sample_id))

        tests, profiles, doctors, hospitals = _registration_choices(conn)
        next_hr = _next_accession(conn, date.today())
    species_list = list(db.SPECIES)
    for ref in tests:
        if ref["species"] not in species_list:
            species_list.append(ref["species"])
    return render_template(
        "registration_form.html",
        item=data,
        patient_id=patient_id,
        tests=tests,
        profiles=profiles,
        doctors=doctors,
        hospitals=hospitals,
        selected_tests=selected_tests,
        selected_profiles=selected_profiles,
        species_list=species_list,
        sexes=db.SEXES,
        age_units=db.AGE_UNITS,
        modes=db.BILLING_MODES,
        next_hr=next_hr,
    )


def _sample_with_patient(conn, sample_id):
    return conn.execute(
        """
        SELECT s.*, p.animal_name, p.owner_name, p.patient_type, p.species,
               p.breed, p.sex, p.age, p.age_unit, p.whatsapp_no, p.email,
               p.id AS patient_id
        FROM samples s
        JOIN patients p ON p.id = s.patient_id
        WHERE s.id = ?
        """,
        (sample_id,),
    ).fetchone()


@app.route("/samples/<int:sample_id>/bill")
def sample_bill(sample_id):
    with db.connect() as conn:
        sample = _sample_with_patient(conn, sample_id)
        if sample is None:
            flash("Registration not found.", "error")
            return redirect(url_for("samples"))
        bill = _billing(conn, sample_id)
    return render_template("bill.html", sample=sample, bill=bill)


# ---------------------------------------------------------------------------
# Referring doctor and hospital masters


def _contact_kind(kind):
    if kind not in db.CONTACT_KINDS:
        return None
    table, singular, plural = db.CONTACT_KINDS[kind]
    column = "referring_doctor" if kind == "doctors" else "hospital_name"
    return {
        "kind": kind,
        "table": table,
        "singular": singular,
        "plural": plural,
        "column": column,
    }


def _contact_from_form():
    data = {
        "name": _clean(request.form.get("name")),
        "address": _clean(request.form.get("address")),
        "mobile": _clean(request.form.get("mobile")),
        "email": _clean(request.form.get("email")),
    }
    errors = []
    if not data["name"]:
        errors.append("Name is required.")
    if not _valid_whatsapp(data["mobile"]):
        errors.append("Mobile number should contain 10 to 15 digits.")
    if not _valid_email(data["email"]):
        errors.append("Email address looks invalid.")
    return data, errors


@app.route("/masters/<kind>")
def contacts(kind):
    info = _contact_kind(kind)
    if info is None:
        return redirect(url_for("dashboard"))
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*,
                   (SELECT COUNT(*) FROM samples s WHERE s.{column} = c.name) AS registrations
            FROM {table} c
            ORDER BY c.name
            """.format(column=info["column"], table=info["table"])
        ).fetchall()
    return render_template("contacts.html", info=info, rows=rows)


@app.route("/masters/<kind>/new", methods=["GET", "POST"])
@app.route("/masters/<kind>/<int:contact_id>/edit", methods=["GET", "POST"])
def contact_form(kind, contact_id=None):
    info = _contact_kind(kind)
    if info is None:
        return redirect(url_for("dashboard"))
    with db.connect() as conn:
        current = None
        if contact_id is not None:
            current = conn.execute(
                "SELECT * FROM {} WHERE id = ?".format(info["table"]), (contact_id,)
            ).fetchone()
            if current is None:
                flash("{} not found.".format(info["singular"]), "error")
                return redirect(url_for("contacts", kind=kind))
        item = dict(current) if current else {"name": "", "address": "", "mobile": "", "email": ""}
        if request.method == "POST":
            item, errors = _contact_from_form()
            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                try:
                    if current is None:
                        conn.execute(
                            "INSERT INTO {} (name, address, mobile, email) VALUES (?, ?, ?, ?)".format(
                                info["table"]
                            ),
                            (item["name"], item["address"], item["mobile"], item["email"]),
                        )
                    else:
                        conn.execute(
                            "UPDATE {} SET name = ?, address = ?, mobile = ?, email = ? WHERE id = ?".format(
                                info["table"]
                            ),
                            (item["name"], item["address"], item["mobile"], item["email"], contact_id),
                        )
                        if current["name"] != item["name"]:
                            # Keep earlier registrations attached to the renamed entry.
                            for table in ("samples", "patients"):
                                conn.execute(
                                    "UPDATE {table} SET {column} = ? WHERE {column} = ?".format(
                                        table=table, column=info["column"]
                                    ),
                                    (item["name"], current["name"]),
                                )
                    conn.commit()
                    flash("{} {} saved.".format(info["singular"], item["name"]))
                    return redirect(url_for("contacts", kind=kind))
                except sqlite3.IntegrityError:
                    flash("{} is already in the list.".format(item["name"]), "error")
    return render_template(
        "contact_form.html", info=info, item=item, contact_id=contact_id
    )


@app.route("/masters/<kind>/<int:contact_id>/delete", methods=["POST"])
def contact_delete(kind, contact_id):
    info = _contact_kind(kind)
    if info is None:
        return redirect(url_for("dashboard"))
    with db.connect() as conn:
        conn.execute("DELETE FROM {} WHERE id = ?".format(info["table"]), (contact_id,))
        conn.commit()
    flash("{} removed. Past registrations keep the name.".format(info["singular"]))
    return redirect(url_for("contacts", kind=kind))


# ---------------------------------------------------------------------------
# Reports


@app.route("/reports/registration")
def report_lookup():
    hr = _clean(request.args.get("hr")).upper()
    matches = []
    if hr:
        with db.connect() as conn:
            exact = conn.execute(
                "SELECT id FROM samples WHERE UPPER(accession_no) = ?", (hr,)
            ).fetchone()
            if exact:
                return redirect(url_for("sample_report", sample_id=exact["id"]))
            matches = conn.execute(
                """
                SELECT s.*, p.animal_name, p.species
                FROM samples s JOIN patients p ON p.id = s.patient_id
                WHERE UPPER(s.accession_no) LIKE ? OR p.animal_name LIKE ?
                ORDER BY s.id DESC LIMIT 50
                """,
                ("%{}%".format(hr), "%{}%".format(hr)),
            ).fetchall()
    return render_template("report_lookup.html", hr=hr, matches=matches)


PERIODS = {
    "daily": "Daily collection",
    "monthly": "Monthly collection",
    "yearly": "Yearly collection",
}


@app.route("/reports/collection/<period>")
def collection_report(period):
    if period not in PERIODS:
        return redirect(url_for("collection_report", period="daily"))
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month
    year = min(max(year, 2000), 2100)
    month = min(max(month, 1), 12)
    day = _iso_or(request.args.get("day"), today.isoformat())
    if period == "daily":
        date_from = date_to = day
        label = date.fromisoformat(day).strftime("%d %b %Y")
    elif period == "monthly":
        date_from = date(year, month, 1).isoformat()
        date_to = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        label = date(year, month, 1).strftime("%B %Y")
    else:
        date_from = date(year, 1, 1).isoformat()
        date_to = date(year, 12, 31).isoformat()
        label = str(year)
    with db.connect() as conn:
        rows = _registration_rows(conn, date_from, date_to)
        modes, collected = _payments_by_mode(conn, date_from, date_to)
    return render_template(
        "collection_report.html",
        period=period,
        heading=PERIODS[period],
        label=label,
        day=day,
        year=year,
        month=month,
        months=[(index, calendar.month_name[index]) for index in range(1, 13)],
        rows=rows,
        totals=_totals(rows),
        modes=modes,
        collected=collected,
    )


@app.route("/reports/referral/<kind>")
def referral_report(kind):
    info = _contact_kind(kind)
    if info is None:
        return redirect(url_for("dashboard"))
    date_from, date_to = _date_window()
    name = _clean(request.args.get("name"))
    with db.connect() as conn:
        names = [
            row["name"]
            for row in conn.execute(
                """
                SELECT name FROM {table}
                UNION SELECT {column} FROM samples WHERE TRIM({column}) != ''
                ORDER BY 1
                """.format(table=info["table"], column=info["column"])
            )
        ]
        filters = {"doctor": name} if kind == "doctors" else {"hospital": name}
        rows = _registration_rows(conn, date_from, date_to, **filters) if name else []
        summary = []
        if not name:
            for row in _registration_rows(conn, date_from, date_to):
                summary.append(row)
    grouped = {}
    for row in summary:
        key = row["referring_doctor" if kind == "doctors" else "hospital_name"] or "Direct / none"
        grouped.setdefault(key, []).append(row)
    overview = sorted(
        ({"name": key, **_totals(items)} for key, items in grouped.items()),
        key=lambda item: -item["net"],
    )
    return render_template(
        "referral_report.html",
        info=info,
        names=names,
        name=name,
        date_from=date_from,
        date_to=date_to,
        rows=rows,
        totals=_totals(rows),
        overview=overview,
        overview_totals=_totals(summary),
    )


# ---------------------------------------------------------------------------
# MIS


@app.route("/mis/cash")
def mis_cash():
    date_from, date_to = _date_window()
    with db.connect() as conn:
        rows = _registration_rows(conn, date_from, date_to)
        modes, collected = _payments_by_mode(conn, date_from, date_to)
        payments = conn.execute(
            """
            SELECT paid_on, mode, SUM(amount) AS amount
            FROM payments
            WHERE paid_on >= ? AND paid_on <= ?
            GROUP BY paid_on, mode
            """,
            (date_from, date_to),
        ).fetchall()
    days = {}
    for row in rows:
        day = days.setdefault(
            row["collected_on"],
            {"day": row["collected_on"], "registrations": 0, "tests": 0, "net": 0, "Cash": 0, "UPI": 0},
        )
        day["registrations"] += 1
        day["tests"] += row["tests"]
        day["net"] += row["net"]
    for payment in payments:
        day = days.setdefault(
            payment["paid_on"],
            {"day": payment["paid_on"], "registrations": 0, "tests": 0, "net": 0, "Cash": 0, "UPI": 0},
        )
        day[payment["mode"]] = day.get(payment["mode"], 0) + payment["amount"]
    daily = [days[key] for key in sorted(days)]
    return render_template(
        "mis_cash.html",
        date_from=date_from,
        date_to=date_to,
        totals=_totals(rows),
        modes=modes,
        collected=collected,
        daily=daily,
    )


@app.route("/mis/species")
def mis_species():
    date_from, date_to = _date_window()
    with db.connect() as conn:
        rows = _registration_rows(conn, date_from, date_to)
    grouped = {}
    for row in rows:
        grouped.setdefault(row["species"], []).append(row)
    species_rows = sorted(
        ({"species": key, **_totals(items)} for key, items in grouped.items()),
        key=lambda item: -item["net"],
    )
    return render_template(
        "mis_species.html",
        date_from=date_from,
        date_to=date_to,
        rows=species_rows,
        totals=_totals(rows),
    )


@app.route("/qc")
def qc_list():
    test = _clean(request.args.get("test"))
    status = _clean(request.args.get("status"))
    sql = "SELECT * FROM qc_runs WHERE 1=1"
    params = []
    if test:
        sql += " AND test_code = ?"
        params.append(test)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY run_on DESC, id DESC"
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        tests = [
            row["test_code"]
            for row in conn.execute(
                "SELECT DISTINCT test_code FROM qc_runs ORDER BY test_code"
            ).fetchall()
        ]
    return render_template(
        "qc.html", runs=rows, test=test, status=status, tests=tests
    )


@app.route("/qc/new", methods=["GET", "POST"])
def qc_new():
    data = {
        "test_code": "",
        "test_name": "",
        "control_level": "Level 1",
        "lot_number": "",
        "instrument": "",
        "operator_name": "",
        "unit": "",
        "run_on": date.today().isoformat(),
        "notes": "",
        "expected_mean": "",
        "expected_sd": "",
    }
    if request.method == "GET":
        # Prefill from an H33 QC run (Instruments > QC): the H33 sends target and
        # limit but never the observed value.
        for key in data:
            if request.args.get(key):
                data[key] = _clean(request.args.get(key))
    if request.method == "POST":
        data.update(
            {
                "test_code": _clean(request.form.get("test_code")).upper(),
                "test_name": _clean(request.form.get("test_name")),
                "control_level": _clean(request.form.get("control_level")),
                "lot_number": _clean(request.form.get("lot_number")),
                "instrument": _clean(request.form.get("instrument")),
                "operator_name": _clean(request.form.get("operator_name")),
                "unit": _clean(request.form.get("unit")),
                "run_on": _clean(request.form.get("run_on")) or date.today().isoformat(),
                "notes": _clean(request.form.get("notes")),
            }
        )
        errors = []
        if not data["test_code"] or not data["test_name"]:
            errors.append("Test code and test name are required.")
        if data["control_level"] not in ("Level 1", "Level 2", "Level 3"):
            errors.append("Choose a control level.")
        try:
            mean = float(_clean(request.form.get("expected_mean")))
            sd = float(_clean(request.form.get("expected_sd")))
            observed = float(_clean(request.form.get("observed_value")))
            z, status = _qc_status(observed, mean, sd)
        except ValueError:
            errors.append("Mean, SD, and observed value must be numbers, and SD must be greater than zero.")
            mean = sd = observed = z = status = None
        if errors:
            for error in errors:
                flash(error, "error")
        else:
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO qc_runs (
                        test_code, test_name, control_level, lot_number, instrument,
                        operator_name, expected_mean, expected_sd, observed_value, unit,
                        z_score, status, run_on, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["test_code"],
                        data["test_name"],
                        data["control_level"],
                        data["lot_number"],
                        data["instrument"],
                        data["operator_name"],
                        mean,
                        sd,
                        observed,
                        data["unit"],
                        z,
                        status,
                        data["run_on"],
                        data["notes"],
                    ),
                )
                conn.commit()
            flash("QC run saved as {} (z = {}).".format(status, z))
            return redirect(url_for("qc_list"))
    with db.connect() as conn:
        catalog = conn.execute(
            """
            SELECT DISTINCT test_code, test_name, unit, department
            FROM reference_ranges
            ORDER BY test_name
            """
        ).fetchall()
    return render_template("qc_form.html", item=data, catalog=catalog)


@app.route("/qc/<int:run_id>/delete", methods=["POST"])
def qc_delete(run_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM qc_runs WHERE id = ?", (run_id,))
        conn.commit()
    flash("QC run removed.")
    return redirect(url_for("qc_list"))


@app.route("/qc/report")
def qc_report():
    date_from = _clean(request.args.get("from")) or date.today().replace(day=1).isoformat()
    date_to = _clean(request.args.get("to")) or date.today().isoformat()
    with db.connect() as conn:
        settings = db.get_settings(conn)
        runs = conn.execute(
            """
            SELECT * FROM qc_runs
            WHERE run_on >= ? AND run_on <= ?
            ORDER BY test_name, control_level, run_on
            """,
            (date_from, date_to),
        ).fetchall()
    total = len(runs)
    accepted = sum(1 for run in runs if run["status"] == "Accept")
    warnings = sum(1 for run in runs if run["status"] == "Warning")
    rejected = sum(1 for run in runs if run["status"] == "Reject")
    return render_template(
        "qc_report.html",
        settings=settings,
        runs=runs,
        date_from=date_from,
        date_to=date_to,
        total=total,
        accepted=accepted,
        warnings=warnings,
        rejected=rejected,
    )


# ---------------------------------------------------------------------------
# Instruments: results received from the analyzers


def _instrument_counts(conn):
    return {
        row["code"]: dict(row) for row in conn.execute(
            """
            SELECT i.code, COUNT(r.id) AS total,
                   SUM(CASE WHEN r.kind = 'patient' AND r.sample_id IS NULL THEN 1 ELSE 0 END) AS unlinked,
                   SUM(CASE WHEN r.kind = 'qc' THEN 1 ELSE 0 END) AS qc,
                   MAX(r.received_at) AS last_received
            FROM instruments i LEFT JOIN instrument_runs r ON r.instrument_id = i.id
            GROUP BY i.code
            """
        )
    }


@app.route("/instruments", methods=["GET", "POST"])
def instruments():
    if request.method == "POST":
        action = request.form.get("action")
        code = request.form.get("code")
        try:
            with db.connect() as conn:
                if action == "save" and code in ("H33", "U100"):
                    host = _clean(request.form.get("host"))
                    port = request.form.get("port", type=int)
                    enabled = 1 if request.form.get("enabled") else 0
                    if not host:
                        raise ValueError("Enter the address.")
                    if code == "H33" and not (port and 1 <= port <= 65535):
                        raise ValueError("Enter a port between 1 and 65535.")
                    username = _clean(request.form.get("username")) or None
                    conn.execute(
                        """
                        UPDATE instruments SET host = ?, port = COALESCE(?, port), enabled = ?,
                               username = COALESCE(?, username)
                        WHERE code = ?
                        """,
                        (host, port, enabled, username, code),
                    )
                    conn.commit()
                    password = request.form.get("password") or ""
                    if code == "U100" and password:
                        unics100.set_password(username or "pi", password)
                    workers.wake(code)
                    flash("{} settings saved.".format("Unics H33" if code == "H33" else "Unics 100plusV"))
                elif action == "add_address":
                    network.add_address(request.form.get("device"), request.form.get("ip"))
                    workers.wake("H33")
                    flash("Address {} added to {}.".format(request.form.get("ip"), request.form.get("device")))
                elif action == "remove_address":
                    network.remove_address(request.form.get("device"), request.form.get("ip"))
                    workers.wake("H33")
                    flash("Address {} removed.".format(request.form.get("ip")))
                elif action == "poll":
                    workers.wake("U100")
                    flash("Reading the 100plusV now.")
        except (ValueError, RuntimeError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("instruments"))
    with db.connect() as conn:
        rows = {row["code"]: dict(row) for row in conn.execute("SELECT * FROM instruments")}
        counts = _instrument_counts(conn)
    for code, row in rows.items():
        row["has_password"] = bool(row["username"] and unics100.get_password(row["username"])) if code == "U100" else None
    adapters = network.adapters()
    for adapter in adapters:
        adapter["cabled"] = network.is_cabled(adapter["port"])
    return render_template(
        "instruments.html",
        instruments=rows,
        counts=counts,
        status=workers.status,
        running=workers.running(),
        adapters=adapters,
        h33_holder=network.holder_of(rows["H33"]["host"]),
        windows=network.WINDOWS,
    )


@app.route("/instruments/status")
def instruments_status():
    with db.connect() as conn:
        counts = _instrument_counts(conn)
        latest = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS id, COALESCE(MAX(linked_at), '') AS linked FROM instrument_runs"
        ).fetchone()
    unlinked = sum(c["unlinked"] or 0 for c in counts.values())
    response = jsonify(status=workers.status, running=workers.running(), unlinked=unlinked, counts=counts,
                       version="{}|{}".format(latest["id"], latest["linked"]), checked=datetime.now().strftime("%H:%M:%S"))
    # The desktop window (WebKit) would otherwise reuse a cached answer.
    response.headers["Cache-Control"] = "no-store"
    return response


def _recent_samples(conn):
    return conn.execute(
        """
        SELECT s.id, s.accession_no, s.collected_on, p.animal_name, p.species
        FROM samples s JOIN patients p ON p.id = s.patient_id
        ORDER BY s.id DESC LIMIT 200
        """
    ).fetchall()


@app.route("/instruments/inbox", methods=["GET", "POST"])
def instrument_inbox():
    if request.method == "POST":
        run_id = request.form.get("run_id", type=int)
        action = request.form.get("action")
        with db.connect() as conn:
            if action == "link":
                typed = _clean(request.form.get("accession_no"))
                sample = store.match_sample(conn, typed)
                if sample is None:
                    flash("No registration {}.".format(typed or "chosen"), "error")
                else:
                    try:
                        store.link(conn, run_id, sample["id"], session.get("username") or "",
                                   replace=request.form.get("replace") == "1")
                        conn.commit()
                        if request.form.get("replace") == "1":
                            flash("Linked to {}. The earlier run of this test went back to the inbox, unchanged.".format(
                                sample["accession_no"]))
                        else:
                            flash("Linked to {}.".format(sample["accession_no"]))
                    except store.DuplicateRun as dup:
                        flash("{} already has this test from this analyzer (run of {}). Use Replace to use this run "
                              "instead; the earlier one goes back to the inbox.".format(
                                  sample["accession_no"], dup.existing["analyzed_at"] or "earlier"), "error")
            elif action == "unlink":
                store.unlink(conn, run_id)
                conn.commit()
                flash("Link removed. The analyzer results are unchanged and back in the inbox.")
            elif action == "relink":
                found = store.relink_unmatched(conn)
                conn.commit()
                flash("{} run(s) matched to registrations.".format(found))
        return redirect(request.form.get("next") or url_for("instrument_inbox"))
    show = request.args.get("show", "unlinked")
    instrument_code = request.args.get("instrument") or None
    with db.connect() as conn:
        runs = store.runs(
            conn, kind="patient", linked={"unlinked": False, "linked": True}.get(show),
            instrument_code=instrument_code,
        )
        store.inbox_hints(conn, runs)
        samples = _recent_samples(conn)
    return render_template(
        "instrument_inbox.html", runs=runs, samples=samples, show=show, instrument_code=instrument_code,
    )


@app.route("/instruments/qc")
def instrument_qc():
    with db.connect() as conn:
        runs = store.runs(conn, kind="qc")
    return render_template("instrument_qc.html", runs=runs, today=date.today().isoformat())


@app.route("/instruments/runs/<int:run_id>")
def instrument_run(run_id):
    with db.connect() as conn:
        item = store.run(conn, run_id)
        samples = _recent_samples(conn)
    if item is None:
        abort(404)
    return render_template("instrument_run.html", run=item, samples=samples)


@app.route("/instruments/images/<int:image_id>")
def instrument_image(image_id):
    with db.connect() as conn:
        image = conn.execute("SELECT * FROM instrument_images WHERE id = ?", (image_id,)).fetchone()
    if image is None:
        abort(404)
    return send_from_directory(store.data_dir() / "images", image["path"], mimetype=image["mime"])


def start_instruments():
    """Start receiving analyzer results (once per process)."""
    workers.start_all()


db.init_db()


if __name__ == "__main__":
    # With the debug reloader, only the serving child receives analyzer results.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_instruments()
    app.run(debug=True, port=5050)
