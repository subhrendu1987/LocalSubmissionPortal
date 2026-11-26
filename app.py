import argparse, csv, difflib, hashlib, json, logging, os, re
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
DATA, GENERATED, SUBMISSIONS = BASE/"data", BASE/"generated", BASE/"submissions"
ROSTER, QUESTIONS = DATA/"students.csv", DATA/"questions.csv"
# Example: SAU/CS/BTECH/CS/2024/11 -> SAU_CS_BTECH_CSE_2024_11
ROLL_FILENAME_REPLACEMENTS = {"CS": "CSE"}
MAX_UPLOAD_MB = 5
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-before-deployment")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# ---------------------------------------------------------------- debug support
DEBUG = False

def setup_debug(enabled):
    """Turn verbose debug logging on/off (python app.py --debug, or APP_DEBUG=1)."""
    global DEBUG
    DEBUG = bool(enabled)
    logging.getLogger().setLevel(logging.DEBUG if DEBUG else logging.INFO)
    app.logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    if not app.logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        app.logger.addHandler(h)
    if DEBUG: app.logger.debug("Debug mode ON")

def dbg(msg, *args):
    if DEBUG: app.logger.debug(msg, *args)

def dbg_table(title, items):
    """Print key/value pairs as an aligned table in the log."""
    if not DEBUG: return
    items = dict(items)
    w = max((len(str(k)) for k in items), default=0)
    body = "\n".join(f"    {str(k):<{w}} : {v!r}" for k, v in items.items())
    app.logger.debug("%s\n%s", title, body)

def dbg_check(label, ok, detail=""):
    """Log a validation step as PASS/FAIL."""
    if DEBUG: app.logger.debug("  [%s] %s%s", "PASS" if ok else "FAIL", label, f" -> {detail}" if detail else "")

@app.before_request
def _log_request():
    if not DEBUG: return
    dbg("--> %s %s from %s", request.method, request.path, request.remote_addr)
    if request.method == "POST":
        dbg_table("    form fields", request.form.to_dict())
        for k, f in request.files.items(): dbg("    file field %r: filename=%r mimetype=%s", k, f.filename, f.mimetype)

@app.after_request
def _log_response(resp):
    dbg("<-- %s %s", resp.status, request.path)
    return resp
# ------------------------------------------------------------------------------

def rows(path):
    if not path.exists():
        dbg("CSV not found: %s", path); return []
    with path.open(newline="", encoding="utf-8-sig") as f: data = list(csv.DictReader(f))
    dbg("Loaded %d rows from %s", len(data), path.name)
    return data

def norm_roll(s): return re.sub(r"\s+", "", (s or "")).upper()
def roster(): return {norm_roll(r.get("roll_no")) for r in rows(ROSTER) if r.get("roll_no","").strip()}
def questions(): return {str(r.get("question_id","")).strip(): r for r in rows(QUESTIONS) if r.get("question_id","").strip()}
def filename_prefix(roll):
    return "_".join(ROLL_FILENAME_REPLACEMENTS.get(p,p) for p in norm_roll(roll).split("/") if p)
def required(value, label, limit=120):
    v=(value or "").strip()
    ok = bool(v) and len(v)<=limit and "\n" not in v and "\r" not in v
    dbg_check(f"{label}: non-empty, <= {limit} chars, single line", ok, repr(v))
    if not ok: raise ValueError(f"Enter a valid {label}.")
    return v
def email(value, label):
    v=required(value,label,254)
    ok = bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",v))
    dbg_check(f"{label}: email format", ok, repr(v))
    if not ok: raise ValueError(f"Enter a valid {label}.")
    return v
def protected_header(roll, section, name, mail, alt, system, qid, qtext):
    lines=[f"// {norm_roll(roll).replace('/','_')}",
           f"// {filename_prefix(roll)}_{section}-{system}",
           f"// {name}", f"// {mail}"]
    if alt: lines.append(f"// {alt}")
    lines += ["", "/*", f" * Question ID: {qid}", " * Question:"]
    lines += [" * "+line for line in qtext.splitlines()]
    lines += [" */", "", "// Student program starts here", ""]
    return "\n".join(lines)

@app.route("/", methods=["GET","POST"])
def index():
    qbank=questions()
    if request.method=="POST":
        try:
            dbg("Generate: validating form")
            roll=required(request.form.get("roll_no"),"roll number",80)
            in_roster = norm_roll(roll) in roster()
            dbg_check("roll number in roster", in_roster, f"normalized={norm_roll(roll)!r}")
            if not in_roster: raise ValueError("Roll number is not present in the official student database.")
            section=required(request.form.get("section"),"section",40)
            name=required(request.form.get("name"),"student name")
            mail=email(request.form.get("email"),"institutional email")
            alt=(request.form.get("alternate_email") or "").strip()
            if alt: alt=email(alt,"alternate email")
            else: dbg("  alternate email not provided (optional)")
            system=required(request.form.get("system_no"),"system number",20)
            ok = bool(re.fullmatch(r"[A-Za-z0-9-]+",system))
            dbg_check("system number charset [A-Za-z0-9-]", ok, repr(system))
            if not ok: raise ValueError("System number may contain only letters, numbers and hyphens.")
            qid=required(request.form.get("question_id"),"question selection",30)
            q=qbank.get(qid)
            ok = bool(q and (q.get("question") or "").strip())
            dbg_check("question exists and is non-empty", ok, f"qid={qid!r}, available={sorted(qbank)}")
            if not ok: raise ValueError("Select a valid question.")
            fname=f"{filename_prefix(roll)}_{system}.c"
            header=protected_header(roll,section,name,mail,alt,system,qid,q["question"].strip())
            student_key=hashlib.sha256(norm_roll(roll).encode()).hexdigest()[:16]
            record_id=hashlib.sha256(f"{student_key}|{section}|{system}|{qid}".encode()).hexdigest()[:20]
            outdir=GENERATED/student_key
            outdir.mkdir(parents=True,exist_ok=True)
            out=outdir/fname
            out.write_text(header,encoding="utf-8",newline="\n")
            record={"record_id":record_id,"roll_no":norm_roll(roll),"filename":fname,
                    "protected_block":header,"created_at":datetime.now(timezone.utc).isoformat()}
            (outdir/f"{record_id}.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
            dbg_table("Generated file", {"student_key":student_key,"record_id":record_id,"filename":fname,
                                         "path":str(out),"header_bytes":len(header.encode())})
            dbg("Protected header:\n%s", header)
            return send_file(out,as_attachment=True,download_name=fname)
        except ValueError as e:
            dbg("Generate rejected: %s", e); flash(str(e),"error")
        except Exception:
            app.logger.exception("Generation failed"); flash("Could not generate file. Contact the instructor.","error")
    return render_template("index.html",questions=qbank.values())

@app.route("/upload",methods=["GET","POST"])
def upload():
    if request.method=="POST":
        try:
            dbg("Upload: validating form")
            roll=required(request.form.get("roll_no"),"roll number",80)
            in_roster = norm_roll(roll) in roster()
            dbg_check("roll number in roster", in_roster, f"normalized={norm_roll(roll)!r}")
            if not in_roster: raise ValueError("Roll number is not present in the official student database.")
            f=request.files.get("source_file")
            dbg_check("file selected", bool(f and f.filename))
            if not f or not f.filename: raise ValueError("Select your completed .c file.")
            fname=secure_filename(f.filename)
            dbg("  filename: original=%r secured=%r", f.filename, fname)
            ok = fname.lower().endswith(".c")
            dbg_check("extension is .c", ok)
            if not ok: raise ValueError("Only .c files are accepted.")
            skey=hashlib.sha256(norm_roll(roll).encode()).hexdigest()[:16]
            sdir=GENERATED/skey
            dbg_check("generated dir exists", sdir.exists(), str(sdir))
            if not sdir.exists(): raise ValueError("Generate your assignment file before uploading.")
            records=[]
            for mp in sdir.glob("*.json"):
                try:
                    r=json.loads(mp.read_text(encoding="utf-8"))
                    if r.get("roll_no")==norm_roll(roll): records.append(r)
                except (OSError,json.JSONDecodeError) as ex: dbg("  skipped unreadable record %s: %s", mp.name, ex)
            dbg("  %d record(s) for this student: %s", len(records), [r.get("filename") for r in records])
            matches=[r for r in records if r.get("filename")==fname]
            dbg_check("filename matches a generated record", bool(matches), f"uploaded={fname!r}")
            if not matches:
                expected=", ".join(sorted({r["filename"] for r in records}))
                raise ValueError(f"Filename does not match. Expected: {expected or 'generate a file first'}.")
            rec=matches[-1]
            data=f.read()
            dbg("  read %d bytes", len(data))
            try: content=data.decode("utf-8")
            except UnicodeDecodeError:
                dbg_check("file is UTF-8", False); raise ValueError("File must be UTF-8 text.")
            dbg_check("file is UTF-8", True)
            intact = content.startswith(rec["protected_block"])
            dbg_check("protected block intact", intact, f"record_id={rec['record_id']}")
            if not intact:
                if DEBUG:
                    exp = rec["protected_block"]; got = content[:len(exp)]
                    idx = next((i for i,(a,b) in enumerate(zip(exp,got)) if a!=b), min(len(exp),len(got)))
                    dbg("  first difference at char %d: expected %r, got %r", idx, exp[idx:idx+30], got[idx:idx+30])
                    dbg("  diff (expected -> uploaded):\n%s", "\n".join(difflib.unified_diff(
                        exp.splitlines(), got.splitlines(), "expected", "uploaded", lineterm="", n=1)))
                raise ValueError("The protected student metadata or question comments were changed or removed.")
            body=content[len(rec["protected_block"]):]
            dbg_check("program body non-empty", bool(body.strip()), f"{len(body.strip())} non-whitespace-trimmed chars")
            if not body.strip(): raise ValueError("Program body is empty.")
            destdir=SUBMISSIONS/skey/rec["record_id"]
            destdir.mkdir(parents=True,exist_ok=True)
            version=len(list(destdir.glob("submission_v*.c")))+1
            dest=destdir/f"submission_v{version}.c"
            dest.write_bytes(data)
            receipt={"roll_no":norm_roll(roll),"filename":fname,"version":version,
                     "uploaded_at":datetime.now(timezone.utc).isoformat(),
                     "sha256":hashlib.sha256(data).hexdigest(),"validation":"passed"}
            (destdir/f"submission_v{version}.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
            dbg_table("Submission saved", {"path":str(dest), **receipt})
            flash(f"Submission accepted. Version {version} recorded.","success")
        except ValueError as e:
            dbg("Upload rejected: %s", e); flash(str(e),"error")
        except Exception:
            app.logger.exception("Upload failed"); flash("Could not process upload. Contact the instructor.","error")
        return redirect(url_for("upload"))
    return render_template("upload.html",max_mb=MAX_UPLOAD_MB)

@app.errorhandler(413)
def too_large(_):
    dbg("Rejected: request body exceeds %d MB", MAX_UPLOAD_MB)
    flash(f"File exceeds {MAX_UPLOAD_MB} MB.","error")
    return redirect(url_for("upload"))

# Also honour APP_DEBUG=1 when started through gunicorn / `flask run`
setup_debug(os.environ.get("APP_DEBUG","").lower() in {"1","true","yes","on"})

if __name__=="__main__":
    ap = argparse.ArgumentParser(description="Assignment generator/submission server")
    ap.add_argument("--debug", action="store_true",
                    help="verbose logs: form inputs, each validation step (PASS/FAIL), paths, diffs")
    args = ap.parse_args()
    if args.debug: setup_debug(True)
    dbg_table("Startup", {"BASE":str(BASE),"roster":str(ROSTER),"questions":str(QUESTIONS),"max_upload_mb":MAX_UPLOAD_MB})
    app.run(host="0.0.0.0",port=5000,debug=False)