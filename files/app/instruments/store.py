"""Save analyzer results exactly as received and link them to registrations.

Nothing here edits a received value: database triggers refuse it (see
db._init_instrument_tables). A run is linked to a registration when staff
typed its HR number on the analyzer (H33: Patient No.; 100plusV: Patient
Name), or by hand from the Instruments inbox.
"""

import json
import re
from datetime import datetime

import db

from . import h33_hl7


def data_dir():
    folder = db.DATA_DIR / "instrument_data"
    (folder / "images").mkdir(parents=True, exist_ok=True)
    (folder / "raw").mkdir(parents=True, exist_ok=True)
    return folder


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _key(text):
    """HR number as typed on an analyzer, reduced to a form both sides share.

    Dashes, spaces and case are ignored, the "HR" prefix is optional and the
    serial's leading zeros don't matter, so HR-20261009-0001, HR202610090001,
    20261009-1 and HR-20261009-001 (the H33's Patient No. holds 15 characters)
    all name the same registration.
    """
    plain = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    found = re.fullmatch(r"(?:HR)?(20\d{6})(\d{1,6})", plain)
    if found:
        return "HR{}-{}".format(found.group(1), int(found.group(2)))
    return plain


class DuplicateRun(ValueError):
    """The registration already has this test from this analyzer."""

    def __init__(self, existing):
        super().__init__("already linked")
        self.existing = existing


def instrument(conn, code):
    return conn.execute("SELECT * FROM instruments WHERE code = ?", (code,)).fetchone()


def _insert_run(conn, inst, run_key, kind, header, raw_file, values, images=()):
    """Insert one run with its values and images; returns the run id, or None if already stored."""
    if conn.execute("SELECT 1 FROM instrument_runs WHERE run_key = ?", (run_key,)).fetchone():
        return None
    cur = conn.execute(
        """
        INSERT INTO instrument_runs (
            instrument_id, run_key, kind, instrument_sample_id, patient_ref, patient_name,
            analyzed_at, received_at, header_json, raw_file
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            inst["id"], run_key, kind, header.get("instrument_sample_id", ""),
            header.get("patient_ref", ""), header.get("patient_name", ""),
            header.get("analyzed_at", ""), now(), json.dumps(header), raw_file,
        ),
    )
    run_id = cur.lastrowid
    conn.executemany(
        """
        INSERT INTO instrument_values (
            run_id, seq, code, name, value_text, value_num, unit, range_text,
            low_value, high_value, flag, flag_source, extra_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id, v["seq"], v["code"], v.get("name"), v.get("value_text"),
                v.get("value_num"), v.get("unit"), v.get("range_text"), v.get("low"),
                v.get("high"), v.get("flag"), v.get("flag_source"),
                json.dumps(v.get("extra") or {}),
            )
            for v in values
        ],
    )
    folder = data_dir() / "images"
    for name, data, mime, ext in images:
        path = folder / "{}_{}.{}".format(run_id, re.sub(r"\W", "", name), ext)
        path.write_bytes(data)
        conn.execute(
            "INSERT INTO instrument_images (run_id, name, path, mime) VALUES (?, ?, ?, ?)",
            (run_id, name, path.name, mime),
        )
    if kind == "patient":
        _auto_link(conn, run_id, header.get("patient_ref", ""))
    return run_id


# ---------------------------------------------------------------- H33

def ingest_h33(message, raw_file=""):
    """Store one H33 HL7 message (a patient sample or a QC run)."""
    with db.connect() as conn:
        inst = instrument(conn, "H33")
        if h33_hl7.is_qc(message):
            qc, histograms = h33_hl7.parse_qc(message)
            header = {k: v for k, v in qc.items() if k != "parameters"}
            header.update(analyzed_at=qc["run_time"], instrument_sample_id="QC file {}".format(qc["qc_file"]))
            run_key = "H33|qc|{}|{}|{}|{}".format(qc["qc_file"], qc["lot"], qc["level"], qc["run_time"])
            values = [
                {
                    "seq": i + 1, "code": p["code"], "name": p["name"], "value_text": p["value_text"],
                    "value_num": p["value"], "unit": p["unit"],
                    "range_text": "" if p["target"] is None else "{:.{d}f} ± {:.{d}f}".format(
                        p["target"], p["limit"], d=p["decimals"]),
                    "low": None if p["target"] is None else round(p["target"] - p["limit"], 6),
                    "high": None if p["target"] is None else round(p["target"] + p["limit"], 6),
                    "flag": None, "flag_source": None,
                    "extra": {"target": p["target"], "limit": p["limit"], "decimals": p["decimals"],
                              "group": p["group"]},
                }
                for i, p in enumerate(qc["parameters"])
            ]
            kind = "qc"
        else:
            result, histograms = h33_hl7.parse(message)
            header = {k: v for k, v in result.items() if k != "parameters"}
            header.update(
                analyzed_at=result["analysis_time"], instrument_sample_id=result["sample_id"],
                patient_ref=result["patient_no"], patient_name=result["patient_name"],
            )
            run_key = "H33|patient|{}|{}".format(result["analysis_time"] or result["message_time"], result["sample_id"])
            values = [
                {
                    "seq": i + 1, "code": p["code"], "name": p["name"], "value_text": p["value_text"],
                    "value_num": p["value"], "unit": p["unit"], "range_text": p["range"],
                    "low": p["range_low"], "high": p["range_high"], "flag": p["flag"] or None,
                    "flag_source": "analyzer", "extra": {"group": p["group"], "status": p["status"]},
                }
                for i, p in enumerate(result["parameters"])
            ]
            kind = "patient"
        images = [(name, data, "image/bmp", "bmp") for name, data in histograms.items()]
        run_id = _insert_run(conn, inst, run_key, kind, header, raw_file, values, images)
        conn.commit()
        return run_id


# ---------------------------------------------------------------- 100plusV

def ingest_u100(rows, ranges, raw_file=""):
    """Store 100plusV result rows (one test each). ranges: test name -> setup row from Patientdata.xlsx."""
    stored = []
    with db.connect() as conn:
        inst = instrument(conn, "U100")
        for r in rows:
            setup = ranges.get(r["test_name"], {})
            low, high = _number(setup.get("test_low")), _number(setup.get("test_high"))
            value = _number(r["result"])
            flag = None
            if value is not None and low is not None and value < low:
                flag = "L"
            elif value is not None and high is not None and value > high:
                flag = "H"
            header = {
                "row": r["row"], "method": r["method"], "date": r["date"],
                "analyzer_patient_id": r["patient_id"], "patient_name": r["patient_name"],
                "patient_ref": r["patient_name"], "instrument_sample_id": r["patient_id"],
                "analyzed_at": r["date"], "range_source": "100plusV test setup (Patientdata.xlsx)",
            }
            run_key = "U100|{}|{}|{}|{}|{}".format(r["row"], r["patient_id"], r["test_name"], r["result"], r["date"])
            values = [{
                "seq": 1, "code": r["test_name"], "name": r["test_name"], "value_text": r["result"],
                "value_num": value, "unit": r["unit"],
                "range_text": "" if low is None and high is None else "{} - {}".format(
                    setup.get("test_low", ""), setup.get("test_high", "")),
                "low": low, "high": high, "flag": flag,
                "flag_source": "analyzer range" if flag else None,
                "extra": {"blank_od": r["blank_od"], "std_od": r["std_od"], "sample_od": r["sample_od"],
                          "method": r["method"], "linearity": [setup.get("linearity_low"), setup.get("linearity_high")]},
            }]
            run_id = _insert_run(conn, inst, run_key, "patient", header, raw_file, values)
            if run_id:
                stored.append(run_id)
        conn.commit()
    return stored


# ---------------------------------------------------------------- linking

def match_sample(conn, typed):
    """Registration whose HR number matches what was typed, or None (none or ambiguous)."""
    key = _key(typed)
    if not key:
        return None
    matches = [row for row in conn.execute("SELECT id, accession_no FROM samples").fetchall()
               if _key(row["accession_no"]) == key]
    return matches[0] if len(matches) == 1 else None


def _tests(conn, run_id):
    """What a run measured: the H33 always sends one CBC panel, the 100plusV one test per run."""
    run = conn.execute(
        "SELECT i.code FROM instrument_runs r JOIN instruments i ON i.id = r.instrument_id WHERE r.id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        return None
    if run["code"] == "H33":
        return ("H33", "CBC")
    codes = sorted(v["code"] for v in conn.execute("SELECT code FROM instrument_values WHERE run_id = ?", (run_id,)))
    return (run["code"], ",".join(codes))


def duplicate_of(conn, run_id, sample_id):
    """Run already linked to sample_id with the same test from the same analyzer, or None."""
    wanted = _tests(conn, run_id)
    for other in conn.execute(
        "SELECT id, analyzed_at FROM instrument_runs WHERE sample_id = ? AND id != ? AND kind = 'patient'",
        (sample_id, run_id),
    ).fetchall():
        if _tests(conn, other["id"]) == wanted:
            return other
    return None


def _auto_link(conn, run_id, patient_ref):
    sample = match_sample(conn, patient_ref)
    # A repeat of a test the registration already has stays in the inbox for staff to decide.
    if sample is None or duplicate_of(conn, run_id, sample["id"]):
        return False
    conn.execute(
        """
        UPDATE instrument_runs SET sample_id = ?, link_method = 'auto', linked_by = 'HR no. on analyzer',
               linked_at = ?
        WHERE id = ? AND sample_id IS NULL
        """,
        (sample["id"], now(), run_id),
    )
    return True


def relink_unmatched(conn):
    """Try auto-linking runs that arrived before their registration existed."""
    count = 0
    for run in conn.execute(
        "SELECT id, patient_ref FROM instrument_runs WHERE sample_id IS NULL AND kind = 'patient'"
    ).fetchall():
        count += _auto_link(conn, run["id"], run["patient_ref"])
    return count


def link(conn, run_id, sample_id, user, replace=False):
    """Link a run by hand. A repeat of a linked test raises DuplicateRun unless replace,
    which sends the earlier run back to the inbox (its results stay unchanged)."""
    existing = duplicate_of(conn, run_id, sample_id)
    if existing is not None:
        if not replace:
            raise DuplicateRun(existing)
        unlink(conn, existing["id"])
    conn.execute(
        """
        UPDATE instrument_runs SET sample_id = ?, link_method = 'manual', linked_by = ?, linked_at = ?
        WHERE id = ? AND kind = 'patient'
        """,
        (sample_id, user, now(), run_id),
    )


def unlink(conn, run_id):
    conn.execute(
        "UPDATE instrument_runs SET sample_id = NULL, link_method = NULL, linked_by = NULL, linked_at = NULL WHERE id = ?",
        (run_id,),
    )


def inbox_hints(conn, runs):
    """For unlinked runs: the registration their typed HR no. points to, and the run it would repeat."""
    for run in runs:
        run["match"] = run["repeat_of"] = None
        if run["sample_id"] is None and run["patient_ref"]:
            sample = match_sample(conn, run["patient_ref"])
            if sample is not None:
                run["match"] = dict(sample)
                repeat = duplicate_of(conn, run["id"], sample["id"])
                run["repeat_of"] = dict(repeat) if repeat else None
    return runs


# ---------------------------------------------------------------- reading

def _with_details(conn, runs):
    out = []
    for run in runs:
        item = dict(run)
        item["header"] = json.loads(run["header_json"])
        item["values"] = [dict(v) for v in conn.execute(
            "SELECT * FROM instrument_values WHERE run_id = ? ORDER BY seq", (run["id"],))]
        item["images"] = [dict(i) for i in conn.execute(
            "SELECT * FROM instrument_images WHERE run_id = ? ORDER BY id", (run["id"],))]
        out.append(item)
    return out


RUN_SELECT = """
    SELECT r.*, i.code AS instrument_code, i.name AS instrument_name, i.department,
           s.accession_no
    FROM instrument_runs r
    JOIN instruments i ON i.id = r.instrument_id
    LEFT JOIN samples s ON s.id = r.sample_id
"""


def runs_for_sample(conn, sample_id):
    return _with_details(conn, conn.execute(
        RUN_SELECT + " WHERE r.sample_id = ? ORDER BY i.department, r.analyzed_at, r.id", (sample_id,)
    ).fetchall())


def run(conn, run_id):
    rows = _with_details(conn, conn.execute(RUN_SELECT + " WHERE r.id = ?", (run_id,)).fetchall())
    return rows[0] if rows else None


def runs(conn, kind="patient", linked=None, instrument_code=None, limit=200):
    sql = RUN_SELECT + " WHERE r.kind = ?"
    args = [kind]
    if linked is True:
        sql += " AND r.sample_id IS NOT NULL"
    elif linked is False:
        sql += " AND r.sample_id IS NULL"
    if instrument_code:
        sql += " AND i.code = ?"
        args.append(instrument_code)
    sql += " ORDER BY r.id DESC LIMIT ?"
    args.append(limit)
    return _with_details(conn, conn.execute(sql, args).fetchall())


def report_sections(runs):
    """Group a sample's analyzer runs for display: one section per instrument.

    Values are shown exactly as received; only their grouping is decided here.
    """
    sections = {}
    for run in runs:
        section = sections.setdefault(run["instrument_code"], {
            "title": run["department"] or run["instrument_name"],
            "instrument": run["instrument_name"],
            "code": run["instrument_code"],
            "analyzed": [],
            "rows": [],
            "images": [],
            "runs": [],
        })
        section["runs"].append(run)
        if run["analyzed_at"] and run["analyzed_at"] not in section["analyzed"]:
            section["analyzed"].append(run["analyzed_at"])
        for value in run["values"]:
            extra = json.loads(value["extra_json"] or "{}")
            section["rows"].append(dict(value, group=extra.get("group", ""), run_id=run["id"]))
        section["images"].extend(run["images"])
    return list(sections.values())
