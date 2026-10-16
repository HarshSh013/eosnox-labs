"""Parse one Unics H33 (Biotoo HA-5000 / Hemax33) result message.

The H33 sends HL7 2.3.1 ORU^R01 over MLLP, one message per sample. Its layout
differs from standard HL7: segments end with CR LF, and in OBX the parameter
name and value sit one field later than the standard places them. Field
positions below are the H33's own, as observed on 3 Oct 2026 (n = index after
splitting the segment on "|", so MSH is counted like any other segment).

  MSH  3 software (Biotoo)  4 model (HA-5000)  7 send time  9 control ID (always 9999)
       17 character set (UNICODE, sent as UTF-8)
  PV1  2 mode (VWB+CBC+3DIFF)  3 remark  7 department (Dept., e.g. Nursery)
  PV1  1 patient type code (provisional, see below)
  PID  1 same code as PV1 1  2 patient number (Patient No.)  3 always 00000000 (the H33
       has no patient ID field)  4 bed number  5 name  7 gender (M/F)  17 not yet known
  OBR  3 sample ID  5 instrument (Biotoo^Hemax33)  7 sampling time
       8 sending time (19700101080000 = not entered)  9 analysis time  10, 11 always 0
       12 age unit code (provisional)  17 operator  18 age number (provisional)  19 clinician
  OBX  NM: 1 seq  4 parameter  6 value (*** = not computable)  7 unit
           8 reference range for the sample's Group (e.g. Newborns)  9 flag (up arrow =
           high, down arrow = low)  12 status (F = final)  17 department  18 operator
       ED: 3 histogram name  5 HA-5000^Image^png^Base64^<data> (data is a BMP)

Confirmed on 3 Oct 2026 against the H33's Edit Info screen (samples 1001, 1004).
Provisional, agreed with the user on 3 Oct 2026 pending a sample in other units:
  OBR 18 = age number, OBR 12 = age unit code (1 = Years; Months, Days, Hours
  unconfirmed, assumed 2, 3, 4), PV1 1 = patient type code counted from 1
  (1 Outpatient Clinic, 2 Hospitalization, 3 Medical Examination, 4 Emergency).
Not transmitted at all: blood type, group (only visible through the ranges it
selects), and the WBC/RBC/PLT flag messages (e.g. "PLT increase").
"""

from __future__ import annotations

import base64
from datetime import datetime

# Parameter code -> (full name, group). Codes are exactly as the H33 sends them.
PARAMETERS = {
    "WBC": ("White blood cells", "WBC"),
    "LYM%": ("Lymphocytes %", "WBC"),
    "MID%": ("Mid-size cells %", "WBC"),
    "GRA%": ("Granulocytes %", "WBC"),
    "LYM#": ("Lymphocytes count", "WBC"),
    "MID#": ("Mid-size cells count", "WBC"),
    "GRA#": ("Granulocytes count", "WBC"),
    "RBC": ("Red blood cells", "RBC"),
    "HGB": ("Hemoglobin", "RBC"),
    "HCT": ("Hematocrit", "RBC"),
    "MCV": ("Mean corpuscular volume", "RBC"),
    "MCH": ("Mean corpuscular hemoglobin", "RBC"),
    "MCHC": ("Mean corpuscular hemoglobin concentration", "RBC"),
    "RDW-CV": ("Red cell distribution width CV", "RBC"),
    "RDW-SD": ("Red cell distribution width SD", "RBC"),
    "PLT": ("Platelets", "PLT"),
    "MPV": ("Mean platelet volume", "PLT"),
    "PDW": ("Platelet distribution width", "PLT"),
    "PCT": ("Plateletcrit", "PLT"),
    "P-LCR": ("Platelet large cell ratio", "PLT"),
    "P-LCC": ("Platelet large cell count", "PLT"),
}

FLAGS = {"↑": "H", "↓": "L", "H": "H", "L": "L", "": ""}
NO_TIME = "19700101080000"

# Field positions understood per segment; any other non-empty field is reported as unmapped.
KNOWN = {
    "MSH": {1, 2, 3, 4, 6, 8, 9, 10, 11, 17},
    "PV1": {1, 2, 3, 7},
    "PID": {1, 2, 3, 4, 5, 7, 17},
    "OBR": {1, 3, 5, 7, 8, 9, 10, 11, 12, 17, 18, 19},
    "OBX": {1, 2, 4, 6, 7, 8, 9, 12, 17, 18},
}
TO_CONFIRM = {("PID", 17): "PID-17"}

# Provisional code tables (see module notes).
AGE_UNITS = {"1": "Years", "2": "Months", "3": "Days", "4": "Hours"}
PATIENT_TYPES = {"1": "Outpatient Clinic", "2": "Hospitalization", "3": "Medical Examination", "4": "Emergency"}


def _time(value: str) -> str:
    """HL7 timestamp -> ISO text; '' when empty or the H33's 'not entered' value."""
    if not value or value.startswith("1970"):
        return ""
    try:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S").isoformat(sep=" ")
    except ValueError:
        return value


def _number(value: str):
    try:
        return float(value)
    except ValueError:
        return None


def _range(text: str) -> tuple:
    low, sep, high = text.partition("-")
    return (_number(low), _number(high)) if sep else (None, None)


def segments(message: str) -> list[list[str]]:
    return [s.strip("\n").split("|") for s in message.split("\r") if s.strip("\n")]


def parse(message: str) -> tuple[dict, dict]:
    """Return (result, histograms) where histograms maps 'WBC' -> BMP bytes."""
    result = {
        "control_id": "", "message_time": "", "software": "", "model": "", "mode": "",
        "department": "", "sample_id": "", "patient_no": "", "patient_id": "", "patient_name": "",
        "gender": "", "age": "", "bed_no": "", "sample_time": "", "sending_time": "", "analysis_time": "",
        "operator": "", "clinician": "", "remark": "", "age": "", "age_unit": "", "patient_type": "",
        "provisional": ["age", "age_unit", "patient_type"],
        "parameters": [], "to_confirm": {}, "unmapped": [],
    }
    histograms: dict[str, bytes] = {}
    for f in segments(message):
        get = lambda i: f[i] if len(f) > i else ""
        kind = f[0]
        for i, value in enumerate(f):
            if (kind, i) in TO_CONFIRM and value:
                result["to_confirm"][TO_CONFIRM[(kind, i)]] = value
        if kind in KNOWN and not (kind == "OBX" and get(2) == "ED"):
            extra = [(i, v) for i, v in enumerate(f) if i and v.strip("^") and i not in KNOWN[kind]]
            entry = {"segment": kind, "fields": extra}
            if extra and entry not in result["unmapped"]:
                result["unmapped"].append(entry)
        if kind == "MSH":
            result.update(software=get(2), model=get(3), message_time=_time(get(6)), control_id=get(9))
        elif kind == "PV1":
            result.update(mode=get(2), remark=get(3), department=get(7),
                          patient_type=PATIENT_TYPES.get(get(1), "" if get(1) in ("", "0") else f"code {get(1)}"))
        elif kind == "PID":
            pid = get(3)
            result["patient_id"] = "" if set(pid) <= {"0"} else pid
            result["patient_name"] = " ".join(p for p in get(5).split("^") if p)
            result.update(patient_no=get(2), bed_no=get(4), gender=get(7))
        elif kind == "OBR":
            result.update(sample_id=get(3), sample_time=_time(get(7)), sending_time=_time(get(8)),
                          analysis_time=_time(get(9)), operator=get(17), clinician=get(19))
            if get(18) not in ("", "0"):
                result["age"] = get(18)
                result["age_unit"] = AGE_UNITS.get(get(12), f"code {get(12)}")
        elif kind == "OBX" and get(2) == "ED":
            name = get(3).replace("Histogram", "")
            data = get(5).split("^")[-1]
            try:
                histograms[name] = base64.b64decode(data)
            except ValueError:
                result["unmapped"].append({"segment": "OBX", "fields": [(3, get(3)), (5, "undecodable image")]})
        elif kind == "OBX":
            code = get(4) or get(3)
            raw = get(6) or get(5)
            low, high = _range(get(8))
            full, group = PARAMETERS.get(code, (code, "Other"))
            result["parameters"].append({
                "seq": get(1), "code": code, "name": full, "group": group,
                "value": _number(raw), "value_text": raw, "unit": get(7),
                "range": get(8), "range_low": low, "range_high": high,
                "flag": FLAGS.get(get(9), get(9)), "status": get(12),
            })
            if not result["department"]:
                result["department"] = get(17)
            if code not in PARAMETERS:
                result["unmapped"].append({"segment": "OBX", "fields": [(4, code)]})
        elif kind not in KNOWN:
            result["unmapped"].append({"segment": kind, "fields": [(i, v) for i, v in enumerate(f) if v.strip("^") and i]})
    return result, histograms


# ---------------------------------------------------------------- QC
#
# A QC run arrives as the same ORU^R01, told apart by OBR-3 = "<file>^QC<method>"
# (seen: "2^QCLJ" = QC file 2, Levey-Jennings). Its layout, from one message on
# 3 Oct 2026; every field is provisional until checked against the H33's QC screen:
#
#   OBR  3 file^method  9 run time  19 lot number  20 level (1, 2; 0 = not set)
#        21 unknown (always 0)  22 expiry date as Unix seconds (1793808000 =
#        5 Nov 2026 00:00 at UTC+8)
#   OBX  NM: 4 parameter  6 value  7 unit  8 target  9 limit (+/-)  13 status
#        Target and limit are whole numbers with the decimal point removed: each
#        parameter keeps the decimals it shows on screen (DECIMALS), so P-LCR
#        1300 = 13.00 and HGB 187 = 18.7. Confirmed by the user on 3 Oct 2026.
#   PV1-2 is "****+****" and PID-7 repeats the last patient's gender; both ignored.

QC_TIMEZONE_HOURS = 8  # the H33's clock and dates look like UTC+8

# Decimal places per parameter, as the H33 prints results (and scales QC targets).
DECIMALS = {
    "WBC": 2, "LYM%": 2, "MID%": 2, "GRA%": 2, "LYM#": 2, "MID#": 2, "GRA#": 2,
    "RBC": 2, "HGB": 1, "HCT": 1, "MCV": 1, "MCH": 1, "MCHC": 1, "RDW-CV": 1, "RDW-SD": 1,
    "PLT": 0, "MPV": 1, "PDW": 1, "PCT": 2, "P-LCR": 2, "P-LCC": 0,
}


def _scaled(value: str, code: str):
    number = _number(value)
    return None if number is None else round(number / 10 ** DECIMALS.get(code, 0), DECIMALS.get(code, 0))


def is_qc(message: str) -> bool:
    obr = next((f for f in segments(message) if f[0] == "OBR"), [])
    return len(obr) > 3 and "^QC" in obr[3]


def _expiry(value: str) -> str:
    try:
        seconds = int(value)
    except ValueError:
        return ""
    if seconds <= 0:
        return ""
    return datetime.utcfromtimestamp(seconds + QC_TIMEZONE_HOURS * 3600).strftime("%Y-%m-%d")


def parse_qc(message: str) -> tuple[dict, dict]:
    qc = {
        "kind": "qc", "control_id": "", "message_time": "", "software": "", "model": "",
        "qc_file": "", "qc_method": "", "run_time": "", "lot": "", "level": "", "expiry": "",
        "obr_21": "", "parameters": [],
        "provisional": ["lot", "level", "expiry"],
    }
    histograms: dict[str, bytes] = {}
    for f in segments(message):
        get = lambda i: f[i] if len(f) > i else ""
        kind = f[0]
        if kind == "MSH":
            qc.update(software=get(2), model=get(3), message_time=_time(get(6)), control_id=get(9))
        elif kind == "OBR":
            file_no, _, method = get(3).partition("^")
            qc.update(qc_file=file_no, qc_method=method.replace("QC", "", 1), run_time=_time(get(9)),
                      lot=get(19), level="" if get(20) in ("", "0") else get(20), obr_21=get(21),
                      expiry=_expiry(get(22)))
        elif kind == "OBX" and get(2) == "ED":
            try:
                histograms[get(3).replace("Histogram", "")] = base64.b64decode(get(5).split("^")[-1])
            except ValueError:
                pass
        elif kind == "OBX":
            code = get(4) or get(3)
            value, target, limit = _number(get(6)), _scaled(get(8), code), _scaled(get(9), code)
            set_up = target is not None and limit is not None and limit > 0
            in_control = None
            if set_up and value is not None:
                in_control = abs(value - target) <= limit
            full, group = PARAMETERS.get(code, (code, "Other"))
            qc["parameters"].append({
                "code": code, "name": full, "group": group, "value": value, "value_text": get(6),
                "unit": get(7), "target": target if set_up else None, "limit": limit if set_up else None,
                "decimals": DECIMALS.get(code, 0),
                "in_control": in_control, "status": get(13),
            })
    return qc, histograms
