# Flavored With Love

A family recipe box. Add recipes by speaking, from a website link, by photo, by uploading a file (PDF, Word, text, web page) or by typing. Spoken recipes are tidied into consistent recipe-card wording. Amounts stay exactly as you say them ("a tidbit", "enough to cover"). Change any recipe by voice. Build a shopping list from any recipe, color items by store, and reorder them. Share any recipe by text: the link opens a read-only page with just that recipe.

## How it fits together
- This folder is the app page, served by GitHub Pages at https://ratemyadministrator.github.io/recipitas/ (kept out of search engines).
- Recipes and the shopping list live in a Supabase database.
- `supabase/functions/ai/` is the Supabase function that talks to Gemini. The Gemini key is stored only as a Supabase secret.
- A **family code** unlocks everything. Without it, the page opens to a code screen and can't read or change recipes or use the Gemini key.

Nothing in this repo is secret. The Supabase URL and publishable key in `docs/index.html` are meant to be public; they only identify the project.

## Owner setup (one time)
1. **Family code:** Supabase dashboard, `recipe-box` project, **SQL Editor**, run:
   `select public.set_family_code('pick-a-code');` (at least 6 characters). Run it again any time to change the code; phones with the old code will be asked for the new one.
2. **Gemini key:** **Edge Functions → Secrets**, add `GEMINI_API_KEY` with your key.

## Turning things off
- AI only: add the Supabase secret `AI_ENABLED` = `off`.
- Everything: pause the Supabase project (Settings).
- Lock out the key: delete it in Google AI Studio and create a new one.
- Lock out a phone: change the family code.

## Files
- `index.html` — the whole app (one page)
- `share.html` — read-only page for one shared recipe (opened from a texted link)
- `supabase/functions/ai/index.ts` — Gemini function (deployed as `ai`)
- `supabase/schema.sql` — database tables and rules
