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
import datetime, html, json, os, re, sys, urllib.request, urllib.error

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



VIDEO_LD = {"@context": "https://schema.org", "@type": "VideoObject",
            "name": "How to post an anonymous review of a college administrator safely",
            "description": "A two-minute walkthrough of Rate My Administrator: find your college, pick the administrator from its list, rate ten areas, write a review, request a college that isn't filled in yet, and six ways to protect your anonymity. No sound; all instructions are on screen.",
            "thumbnailUrl": ["https://ratemyadministrator.github.io/media/how-to-post-safely.jpg"],
            "uploadDate": "2026-11-22T15:30:00-08:00", "duration": "PT2M3S",
            "contentUrl": "https://ratemyadministrator.github.io/media/how-to-post-safely.mp4",
            "embedUrl": "https://ratemyadministrator.github.io/guides/how-to-post-safely/"}

FAQ_LD = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": "Is Rate My Administrator anonymous?", "acceptedAnswer": {"@type": "Answer", "text": "Yes. There are no accounts and no names or emails, and the site does not store IP addresses with reviews. The database host keeps short-lived request logs that can include IP addresses, so a VPN adds protection. Details about you are optional and start at \"Prefer not to say.\""}}, {"@type": "Question", "name": "Can my college find out I wrote a review?", "acceptedAnswer": {"@type": "Answer", "text": "The site has nothing that identifies you to hand over. What can identify you is the content: a specific meeting date, a policy number cited against you, or a program only you teach. Describe patterns instead of single incidents, post from your own device on home internet or cellular data, and wait a while after a specific event."}}, {"@type": "Question", "name": "Who can write a review?", "acceptedAnswer": {"@type": "Answer", "text": "Anyone affected by how a college is run: current and former faculty and staff, retirees, students and community members. There is no employment check, on purpose, because checking a work email would expose reviewers."}}, {"@type": "Question", "name": "Who can be reviewed?", "acceptedAnswer": {"@type": "Answer", "text": "Administrators and trustees in their public roles: presidents, chancellors, provosts, vice presidents, deans, directors with academic or HR authority, and board members. Reviews of people who don't hold a leadership role are removed."}}, {"@type": "Question", "name": "What if my administrator isn't listed?", "acceptedAnswer": {"@type": "Answer", "text": "Search for your college, then add the person's name and title as the college lists them. Listings come from official college pages, and new colleges are added every day."}}, {"@type": "Question", "name": "How do you stop fake or abusive reviews?", "acceptedAnswer": {"@type": "Answer", "text": "Each review must rate specific conduct and include a written account. Readers can report a review, and three reports hide it until it is checked. Threats, harassment, slurs, private or health details, student names and contact information are removed. The site never writes reviews itself."}}, {"@type": "Question", "name": "Can I edit or remove my review?", "acceptedAnswer": {"@type": "Answer", "text": "Yes, from the same browser you posted from: reviewing the same person again replaces your earlier review. From another device, it posts a new one."}}, {"@type": "Question", "name": "How do I request a correction or removal?", "acceptedAnswer": {"@type": "Answer", "text": "Email ratemyadministrators@gmail.com with the person's name, the college and what's wrong. Requests are answered within 7 days. Reviews that break the rules are hidden; opinions and first-hand experience are what the site is for."}}, {"@type": "Question", "name": "Who runs the site?", "acceptedAnswer": {"@type": "Answer", "text": "An independent project, not affiliated with any college, district or union. It uses an AI assistant for operations and outreach; moderation rules are published on the site."}}, {"@type": "Question", "name": "Does it cost anything?", "acceptedAnswer": {"@type": "Answer", "text": "No. It's free to read and post, with no account and no ads."}}]}

# Evergreen guide pages (plain, sourced). Each: (slug, title, description, body_html[, jsonld])
GUIDES = [
    ("faq",
     "Rate My Administrator FAQ: Is It Anonymous? Who Can Review? | Rate My Administrator",
     "Answers about Rate My Administrator: how anonymous reviews of college deans, presidents and trustees work, who can post, and how moderation and removals work.",
     """<h1>Questions and answers</h1>
<details class="qa"><summary>Is Rate My Administrator anonymous?</summary><p>Yes. There are no accounts and no names or emails, and the site does not store IP addresses with reviews. The database host keeps short-lived request logs that can include IP addresses, so a VPN adds protection. Details about you are optional and start at &quot;Prefer not to say.&quot;</p></details>
<details class="qa"><summary>Can my college find out I wrote a review?</summary><p>The site has nothing that identifies you to hand over. What can identify you is the content: a specific meeting date, a policy number cited against you, or a program only you teach. Describe patterns instead of single incidents, post from your own device on home internet or cellular data, and wait a while after a specific event.</p></details>
<details class="qa"><summary>Who can write a review?</summary><p>Anyone affected by how a college is run: current and former faculty and staff, retirees, students and community members. There is no employment check, on purpose, because checking a work email would expose reviewers.</p></details>
<details class="qa"><summary>Who can be reviewed?</summary><p>Administrators and trustees in their public roles: presidents, chancellors, provosts, vice presidents, deans, directors with academic or HR authority, and board members. Reviews of people who don&#x27;t hold a leadership role are removed.</p></details>
<details class="qa"><summary>What if my administrator isn&#x27;t listed?</summary><p>Search for your college, then add the person&#x27;s name and title as the college lists them. Listings come from official college pages, and new colleges are added every day.</p></details>
<details class="qa"><summary>How do you stop fake or abusive reviews?</summary><p>Each review must rate specific conduct and include a written account. Readers can report a review, and three reports hide it until it is checked. Threats, harassment, slurs, private or health details, student names and contact information are removed. The site never writes reviews itself.</p></details>
<details class="qa"><summary>Can I edit or remove my review?</summary><p>Yes, from the same browser you posted from: reviewing the same person again replaces your earlier review. From another device, it posts a new one.</p></details>
<details class="qa"><summary>How do I request a correction or removal?</summary><p>Email ratemyadministrators@gmail.com with the person&#x27;s name, the college and what&#x27;s wrong. Requests are answered within 7 days. Reviews that break the rules are hidden; opinions and first-hand experience are what the site is for.</p></details>
<details class="qa"><summary>Who runs the site?</summary><p>An independent project, not affiliated with any college, district or union. It uses an AI assistant for operations and outreach; moderation rules are published on the site.</p></details>
<details class="qa"><summary>Does it cost anything?</summary><p>No. It&#x27;s free to read and post, with no account and no ads.</p></details>
<a class="btn" href="/">Go to Rate My Administrator</a>""", FAQ_LD),
    ("how-to-post-safely",
     "How to Post an Anonymous Review of a College Administrator Safely (Video) | Rate My Administrator",
     "Two-minute video: how to rate a dean, vice president, president or trustee anonymously on Rate My Administrator, plus six ways to keep small details from identifying you.",
     """<h1>How to post a review safely</h1>
<video controls playsinline preload="metadata" poster="/media/how-to-post-safely.jpg?v=2" width="1920" height="1080" style="width:100%;height:auto;border-radius:10px;background:#060e20;margin-top:8px" aria-describedby="vid-text">
<source src="/media/how-to-post-safely.mp4?v=2" type="video/mp4">Your browser can't play this video. Open "Read the steps instead" below.</video>
<a class="btn" href="/">Go to Rate My Administrator</a>
<details id="vid-text" style="margin-top:14px"><summary style="cursor:pointer;color:var(--blue)">Read the steps instead</summary>
<h2>Posting a review, step by step</h2>
<ol>
<li><b>Open the site</b> and choose <a href="/#submit">Rate an administrator</a>.</li>
<li><b>Find your college.</b> Type part of its name and pick it from the list.</li>
<li><b>Pick the administrator from the list.</b> Most colleges already list their president, vice presidents, deans, directors, managers and trustees. If someone is missing, tap <i>Add someone not listed</i> and use their name and title as the college lists them.</li>
<li><b>Rate ten areas from 1 to 5 stars,</b> such as respect, clear communication, non-retaliation, fair assignments, shared governance and following the contract. Mark any that don't apply as N/A.</li>
<li><b>Answer the optional yes/no questions</b> about your experience. They appear only as group totals once at least 3 people answer, never as one person's answers.</li>
<li><b>Say whether you'd recommend renewal,</b> then pick up to four tags.</li>
<li><b>Write your review.</b> Describe patterns, not one-time incidents, and read the reminder that small details can identify you.</li>
<li><b>Post it.</b> No name, no email, no account. Your review appears right away.</li>
</ol>
<h2>College not filled in yet?</h2>
<p>Find it under <a href="/#institutions">Institutions</a> and tap <b>Request this college</b>. Its president, vice presidents, deans, trustees and managers are added from official college pages, usually within 24 hours. Only the college is saved with a request, nothing about you.</p>
<h2>Six ways to protect yourself</h2>
<ol>
<li><b>Describe patterns, not incidents.</b> "Budget cuts are announced without asking the departments affected" could come from many people. "At the Oct. 4 budget meeting he told me my lab would close" points to a few.</li>
<li><b>Leave out details only you would know:</b> dates of specific meetings or emails, a policy number cited against you, the one program or class only you teach, your exact department, title or years.</li>
<li><b>Post from your own phone or computer,</b> on cellular data or home internet, not a work computer or campus Wi-Fi. A VPN adds another layer.</li>
<li><b>Timing can point to you.</b> Wait a while after a specific event, especially a small meeting. You can update your review later from the same device.</li>
<li><b>Know what the site keeps:</b> your ratings, answers and written review, plus any details you choose to share. Never stored with a review: your name, email, account or IP address.</li>
<li><b>Keep it about conduct in the role.</b> No student names, no health, family or personal-life details, nothing you can't support. Reviews that read as personal attacks are removed.</li>
</ol>
</details>
""", VIDEO_LD, False),
    ("what-is-shared-governance",
     "What Shared Governance Means for College Faculty and Staff | Rate My Administrator",
     "A plain guide to shared governance: what it is, what California law requires of community college boards, and how to tell real consultation from a rubber stamp.",
     """<p class="crumb"><a href="/">Home</a> › Guides</p>
<h1>What shared governance means</h1>
<p class="sub">A plain-language guide for faculty, staff and students</p>
<p>Shared governance is the idea that a college is run jointly: the board and administrators handle overall direction and budgets, and faculty have the main say on academic matters such as curriculum, degree requirements and standards. Staff and students also have a defined voice. It is not a courtesy. In many places it is written into law and policy.</p>
<h2>Where the idea comes from</h2>
<p>The classic statement is the 1966 <a href="https://www.aaup.org/report/statement-government-colleges-and-universities">Statement on Government of Colleges and Universities</a>, written jointly by the AAUP, the American Council on Education and the Association of Governing Boards. It says faculty have primary responsibility for curriculum, methods of instruction, research, faculty status and the parts of student life that relate to education.</p>
<h2>What California law requires at community colleges</h2>
<ul>
<li>The Education Code requires each district board to ensure faculty, staff and students can <a href="https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=EDC&amp;sectionNum=70902">participate effectively in district and college governance</a> (Section 70902(b)(7)).</li>
<li>Title 5 regulations list the "academic and professional matters" on which boards must consult the academic senate, often called the "10+1": curriculum, degree and certificate requirements, grading policies, educational program development, student preparation and success standards, governance structures as they relate to faculty roles, faculty roles in accreditation, faculty professional development, program review, institutional planning and budget processes, and other matters the board and senate agree on (<a href="https://www.law.cornell.edu/regulations/california/5-CCR-53200">5 CCR § 53200</a>).</li>
<li>On those matters the board must "consult collegially": either rely primarily on the senate's advice or reach mutual agreement with it, and it can depart from that advice only in exceptional circumstances and, if asked, must explain why in writing (<a href="https://www.law.cornell.edu/regulations/california/5-CCR-53203">5 CCR § 53203</a>).</li>
<li>Classified staff and students have their own participation rights under Title 5 (<a href="https://www.law.cornell.edu/regulations/california/5-CCR-51023.5">§ 51023.5</a> and <a href="https://www.law.cornell.edu/regulations/california/5-CCR-51023.7">§ 51023.7</a>).</li>
</ul>
<p>Working conditions such as pay, workload and discipline are a separate track: they belong to collective bargaining with the union, not to the senate.</p>
<h2>Real consultation vs. a rubber stamp</h2>
<ul>
<li><b>Real:</b> faculty are asked before a decision is made, have time and information to respond, and can see how their advice changed the outcome.</li>
<li><b>Rubber stamp:</b> the senate is "informed" after the fact, feedback is gathered once the plan is final, or committees meet but never see the numbers.</li>
<li><b>Warning signs:</b> decisions announced over the summer, task forces that replace standing committees, and "we heard you" messages with no change.</li>
</ul>
<h2>What you can do</h2>
<ul>
<li>Read your district's board policy on participation in governance (often numbered BP 2510) and your senate's bylaws.</li>
<li>Ask your senate or union what was consulted on and when.</li>
<li>Rate how your administrators handle shared governance. "Shared governance" is one of the ten things every review on <a href="/">Rate My Administrator</a> scores, anonymously.</li>
</ul>
<p class="t">This guide is general information, not legal advice. Rules differ at the CSU, the UC and in other states.</p>
<a class="btn" href="/">Rate an administrator</a>"""),
]

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


# Reviewer details: broad groups only, shown only once an administrator has MIN_DETAIL_N reviews; month, never day.
MIN_DETAIL_N = 3
ROLE_GROUP = {"Full-time faculty": "Faculty", "Part-time / adjunct faculty": "Faculty", "Department chair": "Faculty",
              "Counselor / librarian": "Faculty", "Classified staff": "Staff"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def reviewer_line(r, n):
    d = (r.get("posted_month") or r.get("created_at") or "")[:7]
    when = f"{MONTHS[int(d[5:7]) - 1]} {d[:4]}" if len(d) == 7 else ""
    if n < MIN_DETAIL_N:
        return " · ".join(x for x in ["Anonymous reviewer", when] if x)
    role = r.get("role_group") or ROLE_GROUP.get(r.get("role") or "", r.get("role") or "")
    parts = [r.get("emp_status"), role, r.get("years"), when]
    return " · ".join(x for x in parts if x and x != "Prefer not to say")


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
.intro{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:14px 0 4px;font-size:.95rem}
.intro .btn{margin:10px 8px 0 0;padding:8px 14px}
details.qa{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}details.qa summary{cursor:pointer;font-weight:600}details.qa p{margin:8px 0 0;color:var(--muted)}
footer{color:var(--faint);font-size:.85rem;margin-top:40px;border-top:1px solid var(--line);padding-top:14px}
"""


INTRO = """<div class="intro">Anonymously rate college deans, vice presidents, presidents and trustees. Faculty, staff, students and community members can post; no account, no email.<br>
<a class="btn" href="/#submit">Rate an administrator</a><a class="btn alt" href="/">Go to the main site</a></div>"""


def page(title, desc, path, body, jsonld=None, intro=True):
    ld = f'<script type="application/ld+json">{json.dumps(jsonld)}</script>' if jsonld else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{SITE}{path}"><meta name="theme-color" content="#0b1326">
<link rel="icon" href="/icons/favicon-32.png"><link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{SITE}{path}"><meta property="og:image" content="{SITE}/icons/icon-512.png">
<style>{CSS}</style>{ld}</head><body>
<header><a href="/">RATE MY<b>ADMINISTRATOR</b></a></header><main>
{INTRO if intro else ""}{body}
<footer>Rate My Administrator is an independent project, not affiliated with any college, district or union.
Reviews are anonymous opinions of the people who posted them. <a href="/#how">How it works, terms and removal requests</a> · <a href="/guides/faq/">FAQ</a> · <a href="/guides/how-to-post-safely/">How to post safely (video)</a> · <a href="https://bsky.app/profile/ratemyadmin.bsky.social" rel="noopener noreferrer">Follow on Bluesky</a> · <a href="/guides/what-is-shared-governance/">What shared governance means</a>.</footer>
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
        try:  # coarse public columns (role_group, posted_month); older database falls back
            rows = fetch_all(url, key, f"reviews?select=admin_id,body,verdict,role_group,emp_status,years,posted_month&admin_id=in.({ids})&order=posted_month.desc")
        except urllib.error.HTTPError:
            rows = fetch_all(url, key, f"reviews?select=admin_id,body,verdict,role,emp_status,years,created_at&admin_id=in.({ids})&order=created_at.desc")
        for r in rows:
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
                f'<p>Anonymous ratings of the deans, vice presidents, president and trustees at {esc(i["name"])}, from faculty, staff, students and community members, current and retired. '
                f'No account and no work email needed.</p>',
                f'<a class="btn" href="/?inst={iid}">Rate an administrator here</a>']
        if n_rev == 0:
            body.insert(4, f'<p style="background:var(--card);border:1px solid var(--line);border-left:3px solid var(--red);border-radius:8px;padding:12px 14px">'
                           f'<b>No one has rated {esc(i["name"])} leadership yet.</b> Be the first. It takes about two minutes, '
                           f'and nothing about you is collected: no account, no email, no IP address.</p>')
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
        desc = f'Anonymous reviews of administrators and trustees at {i["name"]}: deans, vice presidents, president and board, rated by faculty, staff, students and community members.'
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
        revs = reviews.get(aid, [])
        quotes = "".join(f'<blockquote>{esc(r["body"])}<div class="t">{esc(reviewer_line(r, len(revs)))}</div></blockquote>' for r in revs[:20])
        body = (f'<p class="crumb"><a href="/colleges/">Colleges</a> › <a href="/colleges/{slugs[a["inst_id"]]}/">{esc(i["name"])}</a></p>'
                f'<h1>{esc(a["name"])}</h1><p class="sub">{esc(a["title"])} · {esc(i["name"])}</p>'
                f'<p><b>{fmt(overall(s))} / 5</b> overall from {s["n"]} anonymous review{"s" if s["n"] != 1 else ""}. {renew}</p>'
                f'<a class="btn" href="/?admin={aid}">Rate {esc(a["name"].split()[0])}</a><a class="btn alt" href="/?admin={aid}">All ratings and details</a>'
                f'<h2>Ratings</h2><table>{rows}</table><h2>What people say</h2>{quotes}')
        desc = f'{s["n"]} anonymous review{"s" if s["n"] != 1 else ""} of {a["name"]}, {a["title"]} at {i["name"]}, by faculty, staff, students and community members.'
        write(path, page(f'{a["name"]}, {i["name"]}: Reviews | Rate My Administrator', desc, path, body))
        urls.append((SITE + path, (s.get("last_at") or today)[:10]))

    # College index
    items = "".join(f'<li><span class="r t">{len(by_inst[iid])} listed</span><a href="/colleges/{slugs[iid]}/">{esc(inst[iid]["name"])}</a>'
                    f'<div class="t">{esc(inst[iid]["city"] or "")}, {esc(inst[iid]["state"])}</div></li>'
                    for iid in sorted(by_inst, key=lambda x: inst[x]["name"]))
    write("/colleges/", page("All colleges and administrators | Rate My Administrator",
                             "Find your college and anonymously rate its deans, vice presidents, president and trustees.",
                             "/colleges/", f'<h1>All colleges</h1><p class="sub">{len(by_inst)} colleges with administrators listed, A to Z. Tap your college to see '
                                           f'its administrators and their ratings. Not listed? <a href="/#institutions">Search all 3,239 colleges</a> '
                                           f'and add an administrator yourself.</p><ul class="list">{items}</ul>'))

    for slug, title, desc, body, *extra in GUIDES:
        path = f"/guides/{slug}/"
        write(path, page(title, desc, path, body, extra[0] if extra else None, extra[1] if len(extra) > 1 else True))
        urls.append((SITE + path, today))

    sm = "".join(f"<url><loc>{u}</loc><lastmod>{m}</lastmod></url>" for u, m in urls)
    write("sitemap.xml", f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
    print(f"wrote {len(by_inst)} college pages, {len(admin_slug)} review pages, {len(urls)} sitemap URLs")


if __name__ == "__main__":
    sys.exit(main())
