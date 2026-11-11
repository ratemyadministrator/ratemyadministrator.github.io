# Rate My ~~Professor~~ Administrator

Anonymous reviews of the deans, vice presidents, presidents, and chancellors who run US colleges. Faculty and staff rate them on ten areas, answer a few yes/no questions about their experience, and write a review. No account, no email, no name.

- **Directory:** 3,239 institutions (985 community colleges, 596 public universities, 1,658 private nonprofit colleges), built from federal IPEDS and College Scorecard data and updated for closures and mergers through October 2026.
- **Administrators:** seeded from official college leadership pages (see `seed/`), and anyone can add a missing one.
- **Stack:** one HTML page on GitHub Pages + a free Supabase database. No server to run.

## How it fits together

| Piece | Where it lives |
|---|---|
| The site (`index.html`, `config.js`) | GitHub Pages |
| College directory (`data/directory.json`) | This repository |
| Administrators and reviews | Your Supabase database |
| Nightly CSV backup | GitHub Actions (private to repo admins) |

---

## Setup (about 20 minutes)

### 1. Create the database (Supabase)

1. Go to [supabase.com](https://supabase.com) and sign up (free plan is fine to start).
2. Click **New project**. Name it `rate-my-administrator`, set a strong database password (save it somewhere), and pick the region closest to most users (for California: **West US**). Click **Create new project** and wait a minute or two.
3. In the left sidebar open **SQL Editor**, click **New query**, paste the entire contents of [`supabase/schema.sql`](supabase/schema.sql), and click **Run**. You should see "Success. No rows returned."
4. Still in SQL Editor, open a new query, paste the contents of the seed file in [`seed/`](seed/) (California administrators), and click **Run**.
5. Check it worked: open **Table Editor** in the sidebar. You should see `admins` (with rows), `reviews` (empty), and `reports` (empty).

### 2. Get your two connection values

1. Open **Project Settings > API Keys**. (The **Connect** button at the top of the dashboard shows the same values.)
2. Copy the **Project URL** (looks like `https://abcdxyz.supabase.co`).
3. Copy the **publishable key** (starts with `sb_publishable_`).
4. Open `config.js` in this repository and paste both values in place of the placeholders.

> The publishable key is meant to be public; the database rules only let it read visible reviews and add new ones. **Never** put the secret key (`sb_secret_...`) in `config.js` or anywhere in the site.

### 3. Publish the site (GitHub Pages)

1. Sign in to [github.com](https://github.com) and click **New repository**. Name it `rate-my-administrator`, make it **Public**, and create it.
2. On the new repository page click **uploading an existing file**, drag in everything from this folder (including the `.github`, `data`, `scripts`, `seed`, and `supabase` folders), and click **Commit changes**.
   - If drag-and-drop skips the `.github` folder, create the file `.github/workflows/nightly-export.yml` by hand with **Add file > Create new file** and paste its contents.
3. Go to **Settings > Pages**. Under **Build and deployment**, set **Source** to **Deploy from a branch**, branch **main**, folder **/ (root)**, and click **Save**.
4. After a minute the site is live at `https://YOUR-USERNAME.github.io/rate-my-administrator/`.

### 4. Turn on the nightly backup (optional, recommended)

1. In Supabase, **Project Settings > API Keys**, create or copy a **secret key** (`sb_secret_...`).
2. In GitHub, open your repository's **Settings > Secrets and variables > Actions** and add two **repository secrets**:
   - `SUPABASE_URL` = your Project URL
   - `SUPABASE_SECRET_KEY` = the secret key
3. Open the **Actions** tab, choose **Nightly export**, and click **Run workflow** to test it. When it finishes, the run page has a download with `reviews.csv` and `admins.csv`, one row per review with every score and answer.

Secrets are encrypted and never visible in the repository. Exports are attached to workflow runs (kept 90 days), not committed, so a removed review doesn't stay in public git history.

---

## Running it day to day

**Moderation.** Readers can report a review. Three reports hide it automatically. To review hidden items: Supabase **Table Editor > reviews**, filter `hidden = true`, read them, and either set `hidden` back to `false` or delete the row. To remove an administrator listing, set its `status` to `hidden`.

**Adding administrators in bulk.** Put researched lists in `data/admins/*.json` (fields: `instId`, `name`, `title`, `level`, `sourceUrl`, `checked`) and run:

```
python scripts/build_seed.py data/admins/*.json > seed/admins.sql
```

Paste the output into the SQL Editor. Existing names at the same institution are skipped, so re-running is safe.

**Giving an AI agent ownership.** An agent needs: write access to this repository (to update the directory and seed files), the Supabase secret key (to moderate and export), and a schedule. A sensible standing assignment:
1. Daily: read newly reported reviews, hide clear personal attacks or identifying details, and summarize the rest for the human owner.
2. Monthly: re-check college leadership pages for administrator changes and closures, and add new administrators through `build_seed.py`.
3. Nightly: confirm the export ran.

Keep a human able to override the agent, and keep the secret key in the agent's secret store, never in the site.

---

## Privacy and limits

- **No accounts and no verification.** Anyone can post. Read scores together with the number of reviews and the written detail.
- **What is stored:** only what the reviewer types and selects. No names, emails, or device info are saved by the site.
- **IP addresses:** like any web service, Supabase keeps short-lived request logs that can include IP addresses. The site never stores them. For stronger protection, put a proxy such as a Cloudflare Worker in front of the database that drops the visitor's IP before forwarding.
- **Floods:** the database rejects more than 15 reviews of one administrator per hour and more than 60 reviews site-wide per minute. Add Cloudflare Turnstile if spam becomes a problem.
- **Editing:** the browser that posted a review holds a private key that lets it update that review. Another device posts a new review.
- **Experience answers** appear only as percentages once at least 3 people have answered a question.
- **Legal:** in the US, Section 230 generally protects sites from liability for what users post. The How it works tab carries the terms and the removal-request contact (ratemyadministrators@gmail.com); answer requests within 7 days.

## Files

```
index.html                 the whole site
config.js                  your Supabase URL and publishable key
data/directory.json        3,239 institutions
supabase/schema.sql        tables, access rules, flood guard, edit and report functions
seed/                      administrator lists ready to paste into Supabase
scripts/build_seed.py      researched JSON -> SQL seed file
scripts/export_reviews.py  database -> CSV
.github/workflows/nightly-export.yml   runs the export every night
```

## License

MIT. Fork it, run it for your state, and send improvements back.
