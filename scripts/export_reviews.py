"""Export every review and administrator to CSV files.

Runs nightly from .github/workflows/nightly-export.yml, or by hand:
    SUPABASE_URL=https://xxxx.supabase.co SUPABASE_SECRET_KEY=sb_secret_... python scripts/export_reviews.py

Uses only the Python standard library. Writes export/reviews.csv and export/admins.csv.
Hidden (reported) reviews are included with hidden=true so nothing is lost.
"""
import csv, json, os, sys, urllib.request
from pathlib import Path

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
if not URL or not KEY:
    sys.exit("Set SUPABASE_URL and SUPABASE_SECRET_KEY.")

METRICS = ["respect", "communication", "openness", "asktask", "retaliation",
           "compensation", "assign", "discipline", "gov", "contract"]
EXP = ["raised", "adverse", "notice", "disciplined", "process", "unpaid", "payanswer", "union"]

def fetch_all(table, select, order):
    rows, start, page = [], 0, 1000
    while True:
        req = urllib.request.Request(
            f"{URL}/rest/v1/{table}?select={select}&order={order}",
            headers={"apikey": KEY, "Range-Unit": "items", "Range": f"{start}-{start + page - 1}",
                     "User-Agent": "rma-export/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            batch = json.load(r)
        rows += batch
        if len(batch) < page:
            return rows
        start += page

out = Path("export"); out.mkdir(exist_ok=True)

admins = fetch_all("admins", "id,name,title,level,inst_id,inst_name,status,source_url,checked_on,created_at", "created_at.asc")
with open(out / "admins.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["admin_id", "name", "title", "level", "inst_id", "institution", "status", "source_url", "checked_on", "created_at"])
    for a in admins:
        w.writerow([a["id"], a["name"], a["title"], a["level"], a["inst_id"], a["inst_name"], a["status"],
                    a.get("source_url"), a.get("checked_on"), a["created_at"]])

names = {a["id"]: a for a in admins}
reviews = fetch_all("reviews", "id,admin_id,inst_id,scores,exp,verdict,tags,body,role,years,emp_status,last_year,report_count,hidden,created_at,updated_at", "created_at.asc")
with open(out / "reviews.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["review_id", "created_at", "updated_at", "admin_id", "admin_name", "admin_title", "inst_id", "institution"]
               + [f"score_{m}" for m in METRICS] + [f"exp_{e}" for e in EXP]
               + ["verdict", "tags", "status", "last_year", "role", "years", "review_text", "report_count", "hidden"])
    for r in reviews:
        a = names.get(r["admin_id"], {})
        w.writerow([r["id"], r["created_at"], r.get("updated_at"), r["admin_id"], a.get("name"), a.get("title"), r["inst_id"], a.get("inst_name")]
                   + [r["scores"].get(m) for m in METRICS] + [(r.get("exp") or {}).get(e, "") for e in EXP]
                   + [r["verdict"], "; ".join(r.get("tags") or []), r.get("emp_status"), r.get("last_year"), r.get("role"), r.get("years"),
                      r["body"], r["report_count"], r["hidden"]])

print(f"Exported {len(admins)} administrators and {len(reviews)} reviews to {out}/")
