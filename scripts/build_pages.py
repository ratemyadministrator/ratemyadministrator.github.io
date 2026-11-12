"""Build static, search-engine-readable pages for Rate My Administrator.

Run by .github/workflows/pages.yml before each deploy (and on a daily schedule). Reads the
directory from data/directory.json and live listings/reviews from Supabase with the public
publishable key in config.js, then writes (not committed to git):
  colleges/index.html               every college with listings
  colleges/<slug>/index.html        one page per college: its administrators and trustees
  reviews/<slug>/index.html         one page per administrator WITH at least one visible review
  sitemap.xml
Pages link into the app with ?inst=<id> and ?admin=<id>.
"""
import datetime, html, json, os, re, sys, urllib.request

SITE = "https://ratemyadministrator.github.io"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = [("respect", "Respect"), ("communication", "Clear communication"), ("openness", "Open conversation"),
           ("asktask", "Asks rather than tasks"), ("retaliation", "Non-retaliation"), ("compensation", "Compensates extra work"),
           ("assign", "Fair assignments and notice"), ("discipline", "Fair discipline"), ("gov", "Shared governance"),
           ("contract", "Respects the contract and union")]
LEVEL = {"board": "Trustees", "exec": "President / Chancellor", "vp": "Vice presidents and provosts",
         "dean": "Deans", "director": "Directors and managers"}
LEVEL_ORDER = ["exec", "vp", "dean", "director", "board"]
esc = html.escape


def config():
    src = open(os.path.join(ROOT, "config.js")).read()
    url = re.search(r'supabaseUrl:\s*"([^"]+)"', src).group(1)
    key = re.search(r'supabasePublishableKey:\s*"([^"]+)"', src).group(1)
    return url, key


def fetch_all(url, key, path):
    rows, start = [], 0
    while True:
        req = urllib.request.Request(f"{url}/rest/v1/{path}", headers={
            "apikey": key, "Authorization": f"Bearer {key}", "Range": f"{start}-{start + 999}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            batch = json.load(r)
        rows += batch
        if len(batch) < 1000:
            return rows
        start += 1000


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower().replace("&", "and")).strip("-")
    return s[:80] or "x"


def fmt(x):
    return f"{float(x):.1f}" if x is not None else "–"


CSS = """
:root{--bg:#0b1326;--card:#171f33;--fg:#dae2fd;--muted:#b3a3a8;--faint:#9a9cb3;--line:#2a3249;--red:#dc2626;--blue:#7cb7ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--blue)}main{max-width:860px;margin:0 auto;padding:20px 16px 60px}
header{border-bottom:1px solid var(--line);padding:14px 16px}header a{color:var(--fg);text-decoration:none;font-weight:800;letter-spacing:.02em}
header b{background:var(--red);color:#fff;padding:2px 8px;border-radius:4px;margin-left:6px}
h1{font-size:1.7rem;line-height:1.2;margin:18px 0 6px}h2{font-size:1.15rem;margin:28px 0 8px;color:var(--fg)}
.sub,.crumb{color:var(--faint);font-size:.92rem}.crumb a{color:var(--faint)}
.btn{display:inline-block;background:var(--red);color:#fff;text-decoration:none;font-weight:700;padding:10px 16px;border-radius:8px;margin:12px 8px 0 0}
.btn.alt{background:transparent;border:1px solid var(--line);color:var(--fg)}
ul.list{list-style:none;padding:0;margin:0}ul.list li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}
.t{color:var(--muted);font-size:.92rem}.r{float:right;color:var(--fg);font-weight:700}
table{width:100%;border-collapse:collapse}td{padding:6px 0;border-bottom:1px solid var(--line)}td.n{text-align:right;font-weight:700}
blockquote{margin:10px 0;background:var(--card);border-left:3px solid var(--red);padding:10px 14px;border-radius:6px;white-space:pre-wrap}
footer{color:var(--faint);font-size:.85rem;margin-top:40px;border-top:1px solid var(--line);padding-top:14px}
"""


def page(title, desc, path, body, jsonld=None):
    ld = f'<script type="application/ld+json">{json.dumps(jsonld)}</script>' if jsonld else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{SITE}{path}"><meta name="theme-color" content="#0b1326">
<link rel="icon" href="/icons/favicon-32.png"><link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{SITE}{path}"><meta property="og:image" content="{SITE}/icons/icon-512.png">
<style>{CSS}</style>{ld}</head><body>
<header><a href="/">RATE MY<b>ADMINISTRATOR</b></a></header><main>{body}
<footer>Rate My Administrator is an independent project, not affiliated with any college, district or union.
Reviews are anonymous opinions of the people who posted them. <a href="/#how">How it works, terms and removal requests</a>.</footer>
</main></body></html>"""


def write(rel, text):
    p = os.path.join(ROOT, rel.strip("/"), "index.html") if not rel.endswith(".xml") else os.path.join(ROOT, rel.strip("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def main():
    url, key = config()
    d = json.load(open(os.path.join(ROOT, "data/directory.json")))
    inst = {r[0]: {"id": r[0], "name": r[1], "city": r[2], "state": r[3]} for r in d["inst"]}
    admins = fetch_all(url, key, "admins?select=id,name,title,level,inst_id,also_inst&order=name")
    summary = {s["id"]: s for s in fetch_all(url, key, "admin_summary?select=*")}
    reviewed = list(summary)
    reviews = {}
    for i in range(0, len(reviewed), 100):
        ids = ",".join(reviewed[i:i + 100])
        for r in fetch_all(url, key, f"reviews?select=admin_id,body,verdict,role,emp_status,last_year,created_at&admin_id=in.({ids})&order=created_at.desc"):
            reviews.setdefault(r["admin_id"], []).append(r)
    print(f"{len(admins)} listings, {len(summary)} reviewed")

    by_inst = {}
    for a in admins:
        for iid in [a["inst_id"]] + (a.get("also_inst") or []):
            if iid in inst:
                by_inst.setdefault(iid, []).append(a)

    slugs, used = {}, set()
    for iid in sorted(by_inst, key=lambda i: inst[i]["name"]):
        s = slugify(inst[iid]["name"])
        if s in used:
            s = slugify(f'{inst[iid]["name"]} {inst[iid]["state"]}')
        used.add(s)
        slugs[iid] = s

    def overall(s):
        vals = [float(s[f"avg_{k}"]) for k, _ in METRICS if s.get(f"avg_{k}") is not None]
        return sum(vals) / len(vals) if vals else None

    admin_slug = {}
    for aid in summary:
        a = next((x for x in admins if x["id"] == aid), None)
        if a and a["inst_id"] in inst:
            admin_slug[aid] = f'{slugify(a["name"])}-{slugify(inst[a["inst_id"]]["name"])}'[:110] + "-" + aid[:6]

    today = datetime.date.today().isoformat()
    urls = [(f"{SITE}/", today), (f"{SITE}/colleges/", today)]

    # One page per college
    for iid, lst in by_inst.items():
        i, path = inst[iid], f"/colleges/{slugs[iid]}/"
        n_rev = sum(summary[a["id"]]["n"] for a in lst if a["id"] in summary)
        body = [f'<p class="crumb"><a href="/colleges/">Colleges</a> › {esc(i["state"])}</p>',
                f'<h1>{esc(i["name"])} administrator reviews</h1>',
                f'<p class="sub">{esc(i["city"] or "")}{", " if i["city"] else ""}{esc(i["state"])} · {len(lst)} administrators and trustees listed · '
                f'{n_rev} review{"s" if n_rev != 1 else ""}</p>',
                f'<p>Anonymous ratings of the deans, vice presidents, president and trustees at {esc(i["name"])}, from the faculty, staff and students who work with them. '
                f'No account and no work email needed.</p>',
                f'<a class="btn" href="/?inst={iid}">Rate an administrator here</a>']
        for lv in LEVEL_ORDER:
            group = [a for a in lst if a["level"] == lv]
            if not group:
                continue
            body.append(f'<h2>{LEVEL[lv]}</h2><ul class="list">')
            for a in group:
                s = summary.get(a["id"])
                score = f'<span class="r">{fmt(overall(s))} / 5 · {s["n"]} review{"s" if s["n"] != 1 else ""}</span>' if s else '<span class="r t">No reviews yet</span>'
                link = f'/reviews/{admin_slug[a["id"]]}/' if a["id"] in admin_slug else f'/?admin={a["id"]}'
                body.append(f'<li>{score}<a href="{link}">{esc(a["name"])}</a><div class="t">{esc(a["title"])}</div></li>')
            body.append("</ul>")
        desc = f'Anonymous reviews of administrators and trustees at {i["name"]}: deans, vice presidents, president and board, rated by faculty, staff and students.'
        write(path, page(f'{i["name"]} Administrator Reviews | Rate My Administrator', desc, path, "\n".join(body),
                         {"@context": "https://schema.org", "@type": "CollegeOrUniversity", "name": i["name"],
                          "address": {"@type": "PostalAddress", "addressLocality": i["city"], "addressRegion": i["state"]}}))
        urls.append((SITE + path, today))

    # One page per reviewed administrator
    for aid, slug in admin_slug.items():
        a = next(x for x in admins if x["id"] == aid)
        s, i, path = summary[aid], inst[a["inst_id"]], f"/reviews/{slug}/"
        rows = "".join(f'<tr><td>{lbl}</td><td class="n">{fmt(s.get("avg_" + k))}</td></tr>' for k, lbl in METRICS if s.get("avg_" + k) is not None)
        renew = f'{round(100 * s["yes"] / s["votes"])}% would endorse renewal ({s["votes"]} votes)' if s["votes"] else ""
        quotes = "".join(
            f'<blockquote>{esc(r["body"])}<div class="t">{esc(r.get("role") or "")} · {esc(r.get("emp_status") or "")} · {r["created_at"][:10]}</div></blockquote>'
            for r in reviews.get(aid, [])[:20])
        body = (f'<p class="crumb"><a href="/colleges/">Colleges</a> › <a href="/colleges/{slugs[a["inst_id"]]}/">{esc(i["name"])}</a></p>'
                f'<h1>{esc(a["name"])}</h1><p class="sub">{esc(a["title"])} · {esc(i["name"])}</p>'
                f'<p><b>{fmt(overall(s))} / 5</b> overall from {s["n"]} anonymous review{"s" if s["n"] != 1 else ""}. {renew}</p>'
                f'<a class="btn" href="/?admin={aid}">Rate {esc(a["name"].split()[0])}</a><a class="btn alt" href="/?admin={aid}">All ratings and details</a>'
                f'<h2>Ratings</h2><table>{rows}</table><h2>What people say</h2>{quotes}')
        desc = f'{s["n"]} anonymous review{"s" if s["n"] != 1 else ""} of {a["name"]}, {a["title"]} at {i["name"]}, by faculty, staff and students.'
        write(path, page(f'{a["name"]}, {i["name"]}: Reviews | Rate My Administrator', desc, path, body))
        urls.append((SITE + path, (s.get("last_at") or today)[:10]))

    # College index
    items = "".join(f'<li><span class="r t">{len(by_inst[iid])} listed</span><a href="/colleges/{slugs[iid]}/">{esc(inst[iid]["name"])}</a>'
                    f'<div class="t">{esc(inst[iid]["city"] or "")}, {esc(inst[iid]["state"])}</div></li>'
                    for iid in sorted(by_inst, key=lambda x: inst[x]["name"]))
    write("/colleges/", page("Colleges | Rate My Administrator",
                             "Find your college and anonymously rate its deans, vice presidents, president and trustees.",
                             "/colleges/", f'<h1>Colleges</h1><p class="sub">{len(by_inst)} colleges with administrators listed. '
                                           f'More states are on the way.</p><ul class="list">{items}</ul>'))

    sm = "".join(f"<url><loc>{u}</loc><lastmod>{m}</lastmod></url>" for u, m in urls)
    write("sitemap.xml", f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
    print(f"wrote {len(by_inst)} college pages, {len(admin_slug)} review pages, {len(urls)} sitemap URLs")


if __name__ == "__main__":
    sys.exit(main())
