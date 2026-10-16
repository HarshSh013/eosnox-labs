"""SQLite storage for the veterinary laboratory workstation."""

import os
import shutil
import sqlite3
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash


def bundle_dir():
    """Folder that holds templates and other files shipped with the app."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def data_dir():
    """Writable folder for the laboratory database."""
    # Set by the Windows installer's launcher, so updates never touch the data.
    override = os.environ.get("EOSNOX_DATA_DIR")
    if override:
        folder = Path(override)
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent / "data"
    if sys.platform == "darwin":
        folder = Path.home() / "Library" / "Application Support" / "VetLab"
    elif sys.platform == "win32":
        root = Path.home() / "AppData" / "Local"
        folder = root / "VetLab"
    else:
        folder = Path.home() / ".local" / "share" / "vetlab"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "vetlab.db"
    bundled = bundle_dir() / "data" / "vetlab.db"
    if not target.exists() and bundled.exists():
        shutil.copy2(bundled, target)
    return folder


BASE_DIR = bundle_dir()
DATA_DIR = data_dir()
DB_PATH = DATA_DIR / "vetlab.db"

PATIENT_TYPES = (
    "Companion",
    "Livestock",
    "Equine",
    "Avian",
    "Exotic",
    "Wildlife",
)

SPECIES = (
    "Canine",
    "Feline",
    "Equine",
    "Bovine",
    "Caprine",
    "Ovine",
    "Porcine",
    "Avian",
    "Rabbit",
    "Reptile",
)

SEXES = (
    "Male",
    "Female",
    "Male neutered",
    "Female spayed",
    "Unknown",
)

AGE_UNITS = ("years", "months", "weeks", "days")

SAMPLE_STATUSES = ("Received", "In progress", "Reported")

# Patient type is inferred from species at registration; staff can still
# change it on the patient record.
SPECIES_TYPES = {
    "Canine": "Companion",
    "Feline": "Companion",
    "Rabbit": "Companion",
    "Equine": "Equine",
    "Bovine": "Livestock",
    "Caprine": "Livestock",
    "Ovine": "Livestock",
    "Porcine": "Livestock",
    "Avian": "Avian",
    "Reptile": "Exotic",
}

UNITS = (
    "g/dL",
    "mg/dL",
    "mmol/L",
    "U/L",
    "%",
    "10^9/L",
    "10^12/L",
    "fL",
    "pg",
    "µg/dL",
    "ng/mL",
    "mEq/L",
    "IU/L",
)

# Credit means nothing is received at registration; the whole net amount is due.
BILLING_MODES = ("Cash", "UPI", "Credit")
PAYMENT_MODES = ("Cash", "UPI")

CONTACT_KINDS = {
    "doctors": ("ref_doctors", "Referring doctor", "Referring doctors"),
    "hospitals": ("ref_hospitals", "Referring hospital", "Referring hospitals"),
}

# Illustrative adult reference intervals staff can edit. Sources are typical
# textbook ranges used as starting defaults, not a substitute for this lab's
# own validated intervals.
DEFAULT_RANGES = [
    # test_code, test_name, species, unit, low, high, department
    ("WBC", "White blood cells", "Canine", "10^9/L", 6.0, 17.0, "Hematology"),
    ("RBC", "Red blood cells", "Canine", "10^12/L", 5.5, 8.5, "Hematology"),
    ("HGB", "Hemoglobin", "Canine", "g/dL", 12.0, 18.0, "Hematology"),
    ("HCT", "Hematocrit", "Canine", "%", 37.0, 55.0, "Hematology"),
    ("PLT", "Platelets", "Canine", "10^9/L", 200, 500, "Hematology"),
    ("GLU", "Glucose", "Canine", "mg/dL", 74, 143, "Chemistry"),
    ("BUN", "Blood urea nitrogen", "Canine", "mg/dL", 7, 27, "Chemistry"),
    ("CREA", "Creatinine", "Canine", "mg/dL", 0.5, 1.8, "Chemistry"),
    ("ALT", "Alanine aminotransferase", "Canine", "U/L", 10, 125, "Chemistry"),
    ("ALP", "Alkaline phosphatase", "Canine", "U/L", 23, 212, "Chemistry"),
    ("TP", "Total protein", "Canine", "g/dL", 5.2, 8.2, "Chemistry"),
    ("ALB", "Albumin", "Canine", "g/dL", 2.3, 4.0, "Chemistry"),
    ("WBC", "White blood cells", "Feline", "10^9/L", 5.5, 19.5, "Hematology"),
    ("RBC", "Red blood cells", "Feline", "10^12/L", 5.0, 10.0, "Hematology"),
    ("HGB", "Hemoglobin", "Feline", "g/dL", 8.0, 15.0, "Hematology"),
    ("HCT", "Hematocrit", "Feline", "%", 30.0, 45.0, "Hematology"),
    ("PLT", "Platelets", "Feline", "10^9/L", 300, 800, "Hematology"),
    ("GLU", "Glucose", "Feline", "mg/dL", 74, 159, "Chemistry"),
    ("BUN", "Blood urea nitrogen", "Feline", "mg/dL", 16, 36, "Chemistry"),
    ("CREA", "Creatinine", "Feline", "mg/dL", 0.8, 2.4, "Chemistry"),
    ("ALT", "Alanine aminotransferase", "Feline", "U/L", 12, 130, "Chemistry"),
    ("ALP", "Alkaline phosphatase", "Feline", "U/L", 14, 111, "Chemistry"),
    ("TP", "Total protein", "Feline", "g/dL", 5.7, 8.9, "Chemistry"),
    ("ALB", "Albumin", "Feline", "g/dL", 2.2, 4.0, "Chemistry"),
    ("WBC", "White blood cells", "Equine", "10^9/L", 5.4, 14.3, "Hematology"),
    ("RBC", "Red blood cells", "Equine", "10^12/L", 6.8, 12.9, "Hematology"),
    ("HGB", "Hemoglobin", "Equine", "g/dL", 11.0, 19.0, "Hematology"),
    ("HCT", "Hematocrit", "Equine", "%", 32.0, 53.0, "Hematology"),
    ("PLT", "Platelets", "Equine", "10^9/L", 100, 400, "Hematology"),
    ("GLU", "Glucose", "Equine", "mg/dL", 75, 115, "Chemistry"),
    ("BUN", "Blood urea nitrogen", "Equine", "mg/dL", 10, 24, "Chemistry"),
    ("CREA", "Creatinine", "Equine", "mg/dL", 1.2, 1.9, "Chemistry"),
    ("AST", "Aspartate aminotransferase", "Equine", "U/L", 226, 366, "Chemistry"),
    ("TP", "Total protein", "Equine", "g/dL", 5.2, 7.9, "Chemistry"),
    ("ALB", "Albumin", "Equine", "g/dL", 2.6, 3.7, "Chemistry"),
    ("WBC", "White blood cells", "Bovine", "10^9/L", 4.0, 12.0, "Hematology"),
    ("RBC", "Red blood cells", "Bovine", "10^12/L", 5.0, 10.0, "Hematology"),
    ("HGB", "Hemoglobin", "Bovine", "g/dL", 8.0, 15.0, "Hematology"),
    ("HCT", "Hematocrit", "Bovine", "%", 24.0, 46.0, "Hematology"),
    ("PLT", "Platelets", "Bovine", "10^9/L", 100, 800, "Hematology"),
    ("GLU", "Glucose", "Bovine", "mg/dL", 45, 75, "Chemistry"),
    ("BUN", "Blood urea nitrogen", "Bovine", "mg/dL", 6, 27, "Chemistry"),
    ("CREA", "Creatinine", "Bovine", "mg/dL", 1.0, 2.0, "Chemistry"),
    ("TP", "Total protein", "Bovine", "g/dL", 6.7, 7.5, "Chemistry"),
    ("ALB", "Albumin", "Bovine", "g/dL", 2.5, 3.8, "Chemistry"),
    ("SGOT", "SGOT", "Canine", "U/L", 15, 66, "Chemistry"),
    ("SGPT", "SGPT", "Canine", "U/L", 10, 125, "Chemistry"),
    ("TBIL", "Bil-Total", "Canine", "mg/dL", 0.1, 0.5, "Chemistry"),
    ("DBIL", "Bil-Direct", "Canine", "mg/dL", 0.0, 0.2, "Chemistry"),
    ("UREA", "Urea", "Canine", "mg/dL", 15, 57, "Chemistry"),
    ("CA", "Calcium", "Canine", "mg/dL", 8.9, 11.4, "Chemistry"),
    ("NA", "Sodium", "Canine", "mmol/L", 144, 160, "Chemistry"),
    ("K", "Potassium", "Canine", "mmol/L", 3.5, 5.8, "Chemistry"),
    ("CL", "Chloride", "Canine", "mmol/L", 109, 122, "Chemistry"),
    ("HBA1C", "HbA1c", "Canine", "%", 2.5, 4.5, "Chemistry"),
    ("SGOT", "SGOT", "Feline", "U/L", 10, 60, "Chemistry"),
    ("SGPT", "SGPT", "Feline", "U/L", 12, 130, "Chemistry"),
    ("TBIL", "Bil-Total", "Feline", "mg/dL", 0.1, 0.4, "Chemistry"),
    ("DBIL", "Bil-Direct", "Feline", "mg/dL", 0.0, 0.1, "Chemistry"),
    ("UREA", "Urea", "Feline", "mg/dL", 34, 77, "Chemistry"),
    ("CA", "Calcium", "Feline", "mg/dL", 8.2, 10.8, "Chemistry"),
    ("NA", "Sodium", "Feline", "mmol/L", 147, 156, "Chemistry"),
    ("K", "Potassium", "Feline", "mmol/L", 4.0, 5.8, "Chemistry"),
    ("CL", "Chloride", "Feline", "mmol/L", 107, 120, "Chemistry"),
    ("HBA1C", "HbA1c", "Feline", "%", 2.0, 4.0, "Chemistry"),
    ("SGOT", "SGOT", "Equine", "U/L", 226, 366, "Chemistry"),
    ("SGPT", "SGPT", "Equine", "U/L", 3, 25, "Chemistry"),
    ("ALP", "ALP", "Equine", "U/L", 143, 395, "Chemistry"),
    ("TBIL", "Bil-Total", "Equine", "mg/dL", 0.5, 2.1, "Chemistry"),
    ("DBIL", "Bil-Direct", "Equine", "mg/dL", 0.0, 0.4, "Chemistry"),
    ("UREA", "Urea", "Equine", "mg/dL", 21, 51, "Chemistry"),
    ("CA", "Calcium", "Equine", "mg/dL", 10.4, 12.9, "Chemistry"),
    ("NA", "Sodium", "Equine", "mmol/L", 132, 146, "Chemistry"),
    ("K", "Potassium", "Equine", "mmol/L", 2.4, 4.7, "Chemistry"),
    ("CL", "Chloride", "Equine", "mmol/L", 99, 109, "Chemistry"),
    ("HBA1C", "HbA1c", "Equine", "%", 2.0, 5.0, "Chemistry"),
    ("SGOT", "SGOT", "Bovine", "U/L", 60, 125, "Chemistry"),
    ("SGPT", "SGPT", "Bovine", "U/L", 11, 40, "Chemistry"),
    ("ALP", "ALP", "Bovine", "U/L", 18, 153, "Chemistry"),
    ("TBIL", "Bil-Total", "Bovine", "mg/dL", 0.1, 0.5, "Chemistry"),
    ("DBIL", "Bil-Direct", "Bovine", "mg/dL", 0.0, 0.2, "Chemistry"),
    ("UREA", "Urea", "Bovine", "mg/dL", 13, 58, "Chemistry"),
    ("CA", "Calcium", "Bovine", "mg/dL", 9.7, 12.4, "Chemistry"),
    ("NA", "Sodium", "Bovine", "mmol/L", 132, 152, "Chemistry"),
    ("K", "Potassium", "Bovine", "mmol/L", 3.9, 5.8, "Chemistry"),
    ("CL", "Chloride", "Bovine", "mmol/L", 97, 111, "Chemistry"),
    ("HBA1C", "HbA1c", "Bovine", "%", 2.0, 5.0, "Chemistry"),
]


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                animal_name TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                patient_type TEXT NOT NULL,
                species TEXT NOT NULL,
                breed TEXT,
                sex TEXT,
                age TEXT,
                age_unit TEXT,
                whatsapp_no TEXT,
                email TEXT,
                referring_doctor TEXT NOT NULL,
                hospital_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS reference_ranges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_code TEXT NOT NULL,
                test_name TEXT NOT NULL,
                species TEXT NOT NULL,
                department TEXT,
                unit TEXT NOT NULL,
                low_value REAL,
                high_value REAL,
                notes TEXT,
                UNIQUE (test_code, species)
            );

            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                accession_no TEXT NOT NULL UNIQUE,
                patient_id INTEGER NOT NULL REFERENCES patients(id),
                collected_on TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Received',
                referring_doctor TEXT NOT NULL,
                hospital_name TEXT NOT NULL,
                clinical_notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
                test_code TEXT NOT NULL,
                test_name TEXT NOT NULL,
                department TEXT,
                value_text TEXT NOT NULL,
                value_num REAL,
                unit TEXT,
                low_value REAL,
                high_value REAL,
                flag TEXT,
                comment TEXT,
                UNIQUE (sample_id, test_code)
            );

            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                department TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS panel_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                panel_id INTEGER NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
                test_code TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE (panel_id, test_code)
            );

            CREATE TABLE IF NOT EXISTS qc_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_code TEXT NOT NULL,
                test_name TEXT NOT NULL,
                control_level TEXT NOT NULL,
                lot_number TEXT,
                instrument TEXT,
                operator_name TEXT,
                expected_mean REAL NOT NULL,
                expected_sd REAL NOT NULL,
                observed_value REAL NOT NULL,
                unit TEXT,
                z_score REAL,
                status TEXT NOT NULL,
                run_on TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS ref_doctors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                address TEXT,
                mobile TEXT,
                email TEXT
            );

            CREATE TABLE IF NOT EXISTS ref_hospitals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                address TEXT,
                mobile TEXT,
                email TEXT
            );

            CREATE TABLE IF NOT EXISTS bill_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
                item_type TEXT NOT NULL,
                code TEXT,
                name TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
                paid_on TEXT NOT NULL,
                mode TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            """
        )
        _ensure_column(conn, "results", "panel_name", "TEXT")
        _ensure_column(conn, "results", "sort_order", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "results", "interpretation", "TEXT")
        _ensure_column(conn, "panels", "sort_order", "INTEGER NOT NULL DEFAULT 100")
        _ensure_column(conn, "panels", "rate", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "reference_ranges", "rate", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "reference_ranges", "interpretation", "TEXT")
        _ensure_column(conn, "samples", "discount", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "samples", "payment_mode", "TEXT")
        _init_instrument_tables(conn)
        _seed_settings(conn)
        _seed_ranges(conn)
        _seed_panels(conn)
        _seed_users(conn)
        _seed_contacts(conn)
        conn.commit()


def _init_instrument_tables(conn):
    """Tables for results received from analyzers.

    What an analyzer sends is stored as received and never edited: triggers
    refuse any change to a value, and to a run's received data. Only the link
    from a run to a registration can change.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS instruments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            model TEXT,
            department TEXT,
            connection TEXT NOT NULL,
            host TEXT,
            port INTEGER,
            username TEXT,
            enabled INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS instrument_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instrument_id INTEGER NOT NULL REFERENCES instruments(id),
            run_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            instrument_sample_id TEXT,
            patient_ref TEXT,
            patient_name TEXT,
            analyzed_at TEXT,
            received_at TEXT NOT NULL,
            header_json TEXT NOT NULL,
            raw_file TEXT,
            sample_id INTEGER REFERENCES samples(id) ON DELETE SET NULL,
            link_method TEXT,
            linked_by TEXT,
            linked_at TEXT
        );

        CREATE INDEX IF NOT EXISTS instrument_runs_sample ON instrument_runs(sample_id);

        CREATE TABLE IF NOT EXISTS instrument_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES instrument_runs(id),
            seq INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            value_text TEXT,
            value_num REAL,
            unit TEXT,
            range_text TEXT,
            low_value REAL,
            high_value REAL,
            flag TEXT,
            flag_source TEXT,
            extra_json TEXT
        );

        CREATE INDEX IF NOT EXISTS instrument_values_run ON instrument_values(run_id);

        CREATE TABLE IF NOT EXISTS instrument_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES instrument_runs(id),
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            mime TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS instrument_values_read_only
        BEFORE UPDATE ON instrument_values
        BEGIN
            SELECT RAISE(ABORT, 'Analyzer results are read-only');
        END;

        CREATE TRIGGER IF NOT EXISTS instrument_values_keep
        BEFORE DELETE ON instrument_values
        BEGIN
            SELECT RAISE(ABORT, 'Analyzer results cannot be deleted');
        END;

        CREATE TRIGGER IF NOT EXISTS instrument_images_keep
        BEFORE UPDATE ON instrument_images
        BEGIN
            SELECT RAISE(ABORT, 'Analyzer images are read-only');
        END;

        CREATE TRIGGER IF NOT EXISTS instrument_runs_received_data
        BEFORE UPDATE ON instrument_runs
        WHEN NEW.instrument_id IS NOT OLD.instrument_id
          OR NEW.run_key IS NOT OLD.run_key
          OR NEW.kind IS NOT OLD.kind
          OR NEW.instrument_sample_id IS NOT OLD.instrument_sample_id
          OR NEW.patient_ref IS NOT OLD.patient_ref
          OR NEW.patient_name IS NOT OLD.patient_name
          OR NEW.analyzed_at IS NOT OLD.analyzed_at
          OR NEW.received_at IS NOT OLD.received_at
          OR NEW.header_json IS NOT OLD.header_json
          OR NEW.raw_file IS NOT OLD.raw_file
        BEGIN
            SELECT RAISE(ABORT, 'Data received from an analyzer is read-only');
        END;

        CREATE TRIGGER IF NOT EXISTS instrument_runs_keep
        BEFORE DELETE ON instrument_runs
        BEGIN
            SELECT RAISE(ABORT, 'Analyzer runs cannot be deleted');
        END;
        """
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO instruments
            (code, name, model, department, connection, host, port, username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("H33", "Unics H33", "Hemax 33 (Biotoo HA-5000)", "Hematology",
             "hl7-listen", "192.168.19.104", 8001, None),
            ("U100", "Unics 100plusV", "100plusV semi-auto chemistry", "Chemistry",
             "ssh-poll", "raspberrypi.local", 22, "pi"),
        ],
    )


def _ensure_column(conn, table, column, definition):
    names = {
        row["name"]
        for row in conn.execute("PRAGMA table_info({})".format(table))
    }
    if column not in names:
        conn.execute(
            "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, definition)
        )


PANEL_SET = "lft-rft-cbc-hba1c"

OBSOLETE_PANELS = (
    "Complete blood count",
    "Biochemistry",
    "Liver",
    "Renal",
)

DEFAULT_PANELS = (
    (
        "Liver function test",
        "Chemistry",
        "SGOT, SGPT, ALP, bilirubin, total protein, and albumin.",
        ("SGOT", "SGPT", "ALP", "TBIL", "DBIL", "TP", "ALB"),
        10,
    ),
    (
        "Renal function test",
        "Chemistry",
        "Urea, creatinine, calcium, and electrolytes.",
        ("UREA", "CREA", "CA", "NA", "K", "CL"),
        20,
    ),
    (
        "CBC",
        "Hematology",
        "White cells, red cells, hemoglobin, hematocrit, and platelets.",
        ("WBC", "RBC", "HGB", "HCT", "PLT"),
        30,
    ),
    (
        "HbA1c",
        "Chemistry",
        "Glycated hemoglobin.",
        ("HBA1C",),
        40,
    ),
)


def _seed_settings(conn):
    defaults = {
        "lab_name": "Eosnox Labs",
        "lab_tagline": "Veterinary diagnostic laboratory",
        "lab_address": "Set your laboratory address in Settings",
        "lab_phone": "",
        "lab_email": "",
        # Printed report layout (Settings > Report layout).
        "report_header_note": "",
        "report_footer": "Results relate only to the sample tested. "
                         "Correlate clinically before treatment.",
        "report_sign_left": "Technician",
        "report_sign_right": "Pathologist",
        "report_show_histograms": "1",
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    # Rename the old placeholder lab name; a name staff chose is left alone.
    conn.execute(
        "UPDATE settings SET value = 'Eosnox Labs' WHERE key = 'lab_name' AND value = 'VetLab'"
    )


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"


def _seed_users(conn):
    if conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]:
        return
    conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (DEFAULT_USERNAME, generate_password_hash(DEFAULT_PASSWORD)),
    )


def _seed_contacts(conn):
    """Copy doctor and hospital names already typed on patients into the masters, once."""
    done = conn.execute(
        "SELECT value FROM settings WHERE key = 'contacts_seeded'"
    ).fetchone()
    if done:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO ref_doctors (name)
        SELECT referring_doctor FROM patients WHERE TRIM(referring_doctor) != ''
        UNION SELECT referring_doctor FROM samples WHERE TRIM(referring_doctor) != ''
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO ref_hospitals (name)
        SELECT hospital_name FROM patients WHERE TRIM(hospital_name) != ''
        UNION SELECT hospital_name FROM samples WHERE TRIM(hospital_name) != ''
        """
    )
    conn.execute("INSERT INTO settings (key, value) VALUES ('contacts_seeded', '1')")


def _seed_ranges(conn):
    conn.executemany(
        """
        INSERT OR IGNORE INTO reference_ranges
            (test_code, test_name, species, unit, low_value, high_value, department)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        DEFAULT_RANGES,
    )


def _seed_panels(conn):
    current = conn.execute(
        "SELECT value FROM settings WHERE key = 'panel_set'"
    ).fetchone()
    if current and current["value"] == PANEL_SET:
        return
    for name in OBSOLETE_PANELS:
        conn.execute("DELETE FROM panels WHERE name = ?", (name,))
    for name, department, notes, codes, sort_order in DEFAULT_PANELS:
        existing = conn.execute(
            "SELECT id FROM panels WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            panel_id = existing["id"]
            conn.execute(
                """
                UPDATE panels
                SET department = ?, notes = ?, sort_order = ?
                WHERE id = ?
                """,
                (department, notes, sort_order, panel_id),
            )
            conn.execute("DELETE FROM panel_tests WHERE panel_id = ?", (panel_id,))
        else:
            cur = conn.execute(
                """
                INSERT INTO panels (name, department, notes, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                (name, department, notes, sort_order),
            )
            panel_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO panel_tests (panel_id, test_code, sort_order)
            VALUES (?, ?, ?)
            """,
            [(panel_id, code, index * 10) for index, code in enumerate(codes)],
        )
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES ('panel_set', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (PANEL_SET,),
    )


def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def save_settings(conn, values):
    for key, value in values.items():
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value.strip()),
        )
