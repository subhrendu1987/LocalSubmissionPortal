# Student C Assignment Portal

## Workflow
- Students manually enter roll number, section, name, institutional email, optional alternate email, system number and assigned question.
- Only the roll number is checked against `data/students.csv`; the other details are accepted as manually entered.
- Server generates a personalized C file with protected metadata and question comments.
- Students edit code only after `// Student program starts here`.
- On upload, the server checks the roll number, exact generated filename, and exact original comment/header prefix.
- Accepted submissions are stored in versioned files.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
python app.py
```
Open http://127.0.0.1:5000. For LAN use, students visit http://SERVER-IP:5000.

## Student database
Edit `data/students.csv`:
```csv
roll_no
SAU/CS/BTECH/CS/2024/11
SAU/FET/BTECHCSE/2025/049
```

## Question bank
Edit `data/questions.csv`:
```csv
question_id,title,question
C001,Prime test,"Write a C program to determine whether a number is prime."
C002,Array processing,"Write a C program to find the largest element in an array."
```
Use proper CSV quoting for commas or multiline question text.

## Filename rule
Filename = normalized roll-number tokens joined with underscores + `_SYSTEMNUMBER.c`.
A configurable replacement maps `CS` to `CSE` to reproduce the example:
`SAU/CS/BTECH/CS/2024/11`, system `19` -> `SAU_CS_BTECH_CSE_2024_11_19.c`.
Edit `ROLL_FILENAME_REPLACEMENTS` in `app.py` if this mapping is not correct.

## Storage
- `generated/<roll-hash>/`: downloaded templates and server-side protected reference records.
- `submissions/<roll-hash>/<assignment-id>/`: accepted versioned `.c` files and JSON receipts.

## Important
This prototype checks roll-number membership, not the identity of the person using it. A student could enter another valid roll number. Add institute SSO/OTP or unique issued tokens for identity assurance. The name, section, email and system number are not cross-checked against the roster. Use HTTPS and a production WSGI server for deployment outside a trusted lab network.
