import csv, hashlib, json, os, re
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

def rows(path):
    if not path.exists(): return []
    with path.open(newline="", encoding="utf-8-sig") as f: return list(csv.DictReader(f))

def norm_roll(s): return re.sub(r"\s+", "", (s or "")).upper()
def roster(): return {norm_roll(r.get("roll_no")) for r in rows(ROSTER) if r.get("roll_no","").strip()}
def questions(): return {str(r.get("question_id","")).strip(): r for r in rows(QUESTIONS) if r.get("question_id","").strip()}
def filename_prefix(roll):
    return "_".join(ROLL_FILENAME_REPLACEMENTS.get(p,p) for p in norm_roll(roll).split("/") if p)
def required(value, label, limit=120):
    v=(value or "").strip()
    if not v or len(v)>limit or "\\n" in v or "\\r" in v: raise ValueError(f"Enter a valid {label}.")
    return v
def email(value, label):
    v=required(value,label,254)
    if not re.fullmatch(r"[^@\\s]+@[^@\\s]+\\.[^@\\s]+",v): raise ValueError(f"Enter a valid {label}.")
    return v
def protected_header(roll, section, name, mail, alt, system, qid, qtext):
    lines=[f"// {norm_roll(roll).replace('/','_')}",
           f"// {filename_prefix(roll)}_{section}-{system}",
           f"// {name}", f"// {mail}"]
    if alt: lines.append(f"// {alt}")
    lines += ["", "/*", f" * Question ID: {qid}", " * Question:"]
    lines += [" * "+line for line in qtext.splitlines()]
    lines += [" */", "", "// Student program starts here", ""]
    return "\\n".join(lines)

@app.route("/", methods=["GET","POST"])
def index():
    qbank=questions()
    if request.method=="POST":
        try:
            roll=required(request.form.get("roll_no"),"roll number",80)
            if norm_roll(roll) not in roster(): raise ValueError("Roll number is not present in the official student database.")
            section=required(request.form.get("section"),"section",40)
            name=required(request.form.get("name"),"student name")
            mail=email(request.form.get("email"),"institutional email")
            alt=(request.form.get("alternate_email") or "").strip()
            if alt: alt=email(alt,"alternate email")
            system=required(request.form.get("system_no"),"system number",20)
            if not re.fullmatch(r"[A-Za-z0-9-]+",system): raise ValueError("System number may contain only letters, numbers and hyphens.")
            qid=required(request.form.get("question_id"),"question selection",30)
            q=qbank.get(qid)
            if not q or not (q.get("question") or "").strip(): raise ValueError("Select a valid question.")
            fname=f"{filename_prefix(roll)}_{system}.c"
            header=protected_header(roll,section,name,mail,alt,system,qid,q["question"].strip())
            student_key=hashlib.sha256(norm_roll(roll).encode()).hexdigest()[:16]
            record_id=hashlib.sha256(f"{student_key}|{section}|{system}|{qid}".encode()).hexdigest()[:20]
            outdir=GENERATED/student_key
            outdir.mkdir(parents=True,exist_ok=True)
            out=outdir/fname
            out.write_text(header,encoding="utf-8",newline="\\n")
            record={"record_id":record_id,"roll_no":norm_roll(roll),"filename":fname,
                    "protected_block":header,"created_at":datetime.now(timezone.utc).isoformat()}
            (outdir/f"{record_id}.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
            return send_file(out,as_attachment=True,download_name=fname)
        except ValueError as e: flash(str(e),"error")
        except Exception:
            app.logger.exception("Generation failed"); flash("Could not generate file. Contact the instructor.","error")
    return render_template("index.html",questions=qbank.values())

@app.route("/upload",methods=["GET","POST"])
def upload():
    if request.method=="POST":
        try:
            roll=required(request.form.get("roll_no"),"roll number",80)
            if norm_roll(roll) not in roster(): raise ValueError("Roll number is not present in the official student database.")
            f=request.files.get("source_file")
            if not f or not f.filename: raise ValueError("Select your completed .c file.")
            fname=secure_filename(f.filename)
            if not fname.lower().endswith(".c"): raise ValueError("Only .c files are accepted.")
            skey=hashlib.sha256(norm_roll(roll).encode()).hexdigest()[:16]
            sdir=GENERATED/skey
            if not sdir.exists(): raise ValueError("Generate your assignment file before uploading.")
            records=[]
            for mp in sdir.glob("*.json"):
                try:
                    r=json.loads(mp.read_text(encoding="utf-8"))
                    if r.get("roll_no")==norm_roll(roll): records.append(r)
                except (OSError,json.JSONDecodeError): pass
            matches=[r for r in records if r.get("filename")==fname]
            if not matches:
                expected=", ".join(sorted({r["filename"] for r in records}))
                raise ValueError(f"Filename does not match. Expected: {expected or 'generate a file first'}.")
            rec=matches[-1]
            data=f.read()
            try: content=data.decode("utf-8")
            except UnicodeDecodeError: raise ValueError("File must be UTF-8 text.")
            if not content.startswith(rec["protected_block"]):
                raise ValueError("The protected student metadata or question comments were changed or removed.")
            if not content[len(rec["protected_block"]):].strip(): raise ValueError("Program body is empty.")
            destdir=SUBMISSIONS/skey/rec["record_id"]
            destdir.mkdir(parents=True,exist_ok=True)
            version=len(list(destdir.glob("submission_v*.c")))+1
            dest=destdir/f"submission_v{version}.c"
            dest.write_bytes(data)
            receipt={"roll_no":norm_roll(roll),"filename":fname,"version":version,
                     "uploaded_at":datetime.now(timezone.utc).isoformat(),
                     "sha256":hashlib.sha256(data).hexdigest(),"validation":"passed"}
            (destdir/f"submission_v{version}.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
            flash(f"Submission accepted. Version {version} recorded.","success")
        except ValueError as e: flash(str(e),"error")
        except Exception:
            app.logger.exception("Upload failed"); flash("Could not process upload. Contact the instructor.","error")
        return redirect(url_for("upload"))
    return render_template("upload.html",max_mb=MAX_UPLOAD_MB)

@app.errorhandler(413)
def too_large(_):
    flash(f"File exceeds {MAX_UPLOAD_MB} MB.","error")
    return redirect(url_for("upload"))

if __name__=="__main__": app.run(host="0.0.0.0",port=5000,debug=False)
