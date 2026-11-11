-- Rate My Administrator: database setup
-- Paste this whole file into Supabase > SQL Editor > New query, then click Run.
-- It is safe to run on a brand-new project. Running it twice will report "already exists" errors but change nothing.

create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------
-- Administrators (deans, VPs, presidents, chancellors)
-- ---------------------------------------------------------------
create table public.admins (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (char_length(name) between 5 and 80),
  title       text not null check (char_length(title) between 3 and 160),
  level       text not null check (level in ('board','exec','vp','dean','director')),
  inst_id     text not null check (char_length(inst_id) between 3 and 80),
  inst_name   text,
  source_url  text,                       -- public page the listing came from (seeded entries)
  checked_on  date,                       -- when that page was checked
  status      text not null default 'active' check (status in ('active','hidden')),
  created_at  timestamptz not null default now()
);
-- One listing per person per institution (case-insensitive name match)
create unique index admins_inst_name_uniq on public.admins (inst_id, lower(name));
create index admins_inst_idx on public.admins (inst_id);

-- ---------------------------------------------------------------
-- Reviews
-- ---------------------------------------------------------------
create or replace function public.valid_scores(s jsonb) returns boolean
language sql immutable set search_path = '' as $$
  -- each rating is 1-5; areas marked N/A are simply left out; at least 3 must be rated
  select jsonb_typeof(s) = 'object'
     and not exists (
       select 1 from jsonb_each_text(s) e
       where e.key <> all (array['respect','communication','openness','asktask','retaliation',
                                 'compensation','assign','discipline','gov','contract'])
          or e.value !~ '^[1-5]$')
     and (select count(*) from jsonb_object_keys(s)) >= 3
$$;

create table public.reviews (
  id             uuid primary key default gen_random_uuid(),
  admin_id       uuid not null references public.admins(id) on delete cascade,
  inst_id        text not null,
  scores         jsonb not null check (public.valid_scores(scores)),
  exp            jsonb not null default '{}'::jsonb check (jsonb_typeof(exp) = 'object'),
  verdict        text not null check (verdict in ('yes','no','abstain')),
  tags           text[] not null default '{}' check (cardinality(tags) <= 4),
  body           text not null check (char_length(body) between 40 and 2000),
  role           text check (char_length(role) <= 60),
  years          text check (char_length(years) <= 40),
  emp_status     text check (char_length(emp_status) <= 40),
  last_year      text check (char_length(last_year) <= 12),
  edit_key_hash  text,                    -- sha256 of a random key kept in the reviewer's browser
  report_count   int not null default 0,
  hidden         boolean not null default false,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz
);
create index reviews_admin_idx on public.reviews (admin_id) where not hidden;
create index reviews_created_idx on public.reviews (created_at desc);

create table public.reports (
  id         uuid primary key default gen_random_uuid(),
  review_id  uuid not null references public.reviews(id) on delete cascade,
  reason     text,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- Flood guard: no IP addresses are stored, so limit bursts instead
-- ---------------------------------------------------------------
create or replace function public.flood_guard() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from public.reviews where admin_id = new.admin_id and created_at > now() - interval '1 hour') >= 15 then
    raise exception 'Too many reviews of this administrator in the last hour. Try again later.';
  end if;
  if (select count(*) from public.reviews where created_at > now() - interval '1 minute') >= 60 then
    raise exception 'The site is receiving too many reviews right now. Try again in a minute.';
  end if;
  return new;
end $$;
create trigger reviews_flood_guard before insert on public.reviews
  for each row execute function public.flood_guard();

-- ---------------------------------------------------------------
-- Leaderboard summary (one row per reviewed administrator)
-- ---------------------------------------------------------------
create or replace view public.admin_summary with (security_invoker = on) as
select a.id, a.name, a.title, a.level, a.inst_id,
       count(r.id)::int as n,
       avg((r.scores->>'respect')::numeric)       as avg_respect,
       avg((r.scores->>'communication')::numeric) as avg_communication,
       avg((r.scores->>'openness')::numeric)      as avg_openness,
       avg((r.scores->>'asktask')::numeric)       as avg_asktask,
       avg((r.scores->>'retaliation')::numeric)   as avg_retaliation,
       avg((r.scores->>'compensation')::numeric)  as avg_compensation,
       avg((r.scores->>'assign')::numeric)        as avg_assign,
       avg((r.scores->>'discipline')::numeric)    as avg_discipline,
       avg((r.scores->>'gov')::numeric)           as avg_gov,
       avg((r.scores->>'contract')::numeric)      as avg_contract,
       count(*) filter (where r.verdict = 'yes')::int            as yes,
       count(*) filter (where r.verdict in ('yes','no'))::int    as votes,
       max(r.created_at) as last_at
from public.admins a
join public.reviews r on r.admin_id = a.id and not r.hidden
where a.status = 'active'
group by a.id;

-- ---------------------------------------------------------------
-- Functions the public page may call
-- ---------------------------------------------------------------
-- Edit your own review (the browser that posted it holds the key)
create or replace function public.update_review(p_id uuid, p_key text, p_review jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  update public.reviews set
    scores     = p_review->'scores',
    exp        = coalesce(p_review->'exp', '{}'::jsonb),
    verdict    = p_review->>'verdict',
    tags       = coalesce(array(select jsonb_array_elements_text(p_review->'tags')), '{}'),
    body       = p_review->>'body',
    role       = p_review->>'role',
    years      = p_review->>'years',
    emp_status = p_review->>'emp_status',
    last_year  = p_review->>'last_year',
    updated_at = now()
  where id = p_id
    and edit_key_hash = encode(extensions.digest(p_key, 'sha256'), 'hex');
  if not found then raise exception 'not_allowed'; end if;
end $$;

-- Report a review; three reports hide it until the owner reviews it
create or replace function public.report_review(p_id uuid, p_reason text) returns void
language plpgsql security definer set search_path = public as $$
begin
  insert into public.reports (review_id, reason) values (p_id, left(coalesce(p_reason, ''), 500));
  update public.reviews
     set report_count = report_count + 1,
         hidden = hidden or (report_count + 1) >= 3
   where id = p_id;
end $$;

-- ---------------------------------------------------------------
-- Access rules. The publishable (anon) key can only do what is granted here.
-- ---------------------------------------------------------------
alter table public.admins  enable row level security;
alter table public.reviews enable row level security;
alter table public.reports enable row level security;

revoke all on public.admins, public.reviews, public.reports from anon, authenticated;

-- Read: active admins, visible reviews (never the edit key hash or report counts)
grant select (id, name, title, level, inst_id, inst_name, status, created_at) on public.admins to anon, authenticated;
grant select (id, admin_id, inst_id, scores, exp, verdict, tags, body, role, years, emp_status, last_year, created_at, updated_at, hidden)
  on public.reviews to anon, authenticated;
grant select on public.admin_summary to anon, authenticated;

create policy "read active admins"   on public.admins  for select to anon, authenticated using (status = 'active');
create policy "read visible reviews" on public.reviews for select to anon, authenticated using (not hidden);

-- Write: add administrators and reviews only (no edits or deletes except through the functions above)
grant insert (name, title, level, inst_id, inst_name) on public.admins to anon, authenticated;
grant insert (admin_id, inst_id, scores, exp, verdict, tags, body, role, years, emp_status, last_year, edit_key_hash)
  on public.reviews to anon, authenticated;

create policy "add admins"  on public.admins  for insert to anon, authenticated with check (status = 'active');
create policy "add reviews" on public.reviews for insert to anon, authenticated with check (not hidden and report_count = 0);

revoke execute on function public.update_review(uuid, text, jsonb), public.report_review(uuid, text) from public;
grant execute on function public.update_review(uuid, text, jsonb), public.report_review(uuid, text) to anon, authenticated;
