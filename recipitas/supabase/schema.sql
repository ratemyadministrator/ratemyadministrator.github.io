-- Flavored With Love database (Supabase / Postgres). This is the current setup, for reference and rebuilding.
-- Nothing here is secret. The family code itself is never stored, only a bcrypt hash of it.

-- Recipes: free-text amounts and units, kept exactly as written
create table public.recipes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade,
  name text not null check (length(btrim(name)) > 0),
  description text not null default '',
  prep_time text not null default '',
  cook_time text not null default '',
  total_time text not null default '',
  servings text not null default '',
  category text not null default '',
  notes text not null default '',
  source text not null default '',
  ingredients jsonb not null default '[]'::jsonb check (jsonb_typeof(ingredients) = 'array'),
  steps jsonb not null default '[]'::jsonb check (jsonb_typeof(steps) = 'array'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Shopping list (no prices). color = store color the cook taps to set
create table public.shopping_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade,
  recipe_id uuid references public.recipes(id) on delete set null,
  recipe_name text not null default '',
  quantity text not null default '',
  unit text not null default '',
  name text not null check (length(btrim(name)) > 0),
  note text not null default '',
  checked boolean not null default false,
  position bigint not null default (extract(epoch from clock_timestamp()) * 1000)::bigint,
  color text not null default '' check (color in ('', 'rose', 'amber', 'green', 'blue', 'purple')),
  created_at timestamptz not null default now()
);

create or replace function public.touch_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin new.updated_at = now(); return new; end; $$;
create trigger recipes_touch before update on public.recipes
  for each row execute function public.touch_updated_at();

-- Family code: one shared code unlocks recipes, the list and the Gemini function
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
create table private.family_code (
  id int primary key default 1 check (id = 1),
  code_hash text not null,
  set_at timestamptz not null default now()
);

-- Owner sets or changes the code in the Supabase SQL editor:
--   select public.set_family_code('your code here');
create or replace function public.set_family_code(new_code text)
returns text language plpgsql security definer set search_path = '' as $$
begin
  if length(coalesce(btrim(new_code), '')) < 6 then raise exception 'Use at least 6 characters'; end if;
  insert into private.family_code (id, code_hash) values (1, extensions.crypt(btrim(new_code), extensions.gen_salt('bf', 8)))
  on conflict (id) do update set code_hash = excluded.code_hash, set_at = now();
  return 'Family code saved';
end; $$;
revoke all on function public.set_family_code(text) from public, anon, authenticated;

-- True only when the request's x-family-code header matches
create or replace function public.family_ok()
returns boolean language plpgsql stable security definer set search_path = '' as $$
declare given text; h text;
begin
  given := btrim(coalesce(current_setting('request.headers', true)::json ->> 'x-family-code', ''));
  if given = '' then return false; end if;
  select code_hash into h from private.family_code where id = 1;
  if h is null then return false; end if;
  return extensions.crypt(given, h) = h;
end; $$;
revoke all on function public.family_ok() from public;
grant execute on function public.family_ok() to anon, authenticated;

alter table public.recipes enable row level security;
alter table public.shopping_items enable row level security;

create policy "family reads recipes" on public.recipes for select to anon, authenticated using ((select public.family_ok()));
create policy "family adds recipes" on public.recipes for insert to anon, authenticated with check ((select public.family_ok()));
create policy "family changes recipes" on public.recipes for update to anon, authenticated using ((select public.family_ok())) with check ((select public.family_ok()));
create policy "family deletes recipes" on public.recipes for delete to anon, authenticated using ((select public.family_ok()));
create policy "family reads list" on public.shopping_items for select to anon, authenticated using ((select public.family_ok()));
create policy "family adds to list" on public.shopping_items for insert to anon, authenticated with check ((select public.family_ok()));
create policy "family changes list" on public.shopping_items for update to anon, authenticated using ((select public.family_ok())) with check ((select public.family_ok()));
create policy "family deletes from list" on public.shopping_items for delete to anon, authenticated using ((select public.family_ok()));

grant select, insert, update, delete on public.recipes, public.shopping_items to anon, authenticated;

-- Share links: one unguessable link per recipe; the link shows only that recipe, read-only.
create table private.recipe_shares (
  token uuid primary key default gen_random_uuid(),
  recipe_id uuid not null unique references public.recipes(id) on delete cascade,
  created_at timestamptz not null default now()
);
create or replace function public.share_recipe(rid uuid)
returns uuid language plpgsql volatile security definer set search_path = '' as $$
declare t uuid;
begin
  if not public.family_ok() then raise exception 'Wrong or missing family code' using errcode = '42501'; end if;
  if not exists (select 1 from public.recipes where id = rid) then raise exception 'Recipe not found'; end if;
  insert into private.recipe_shares (recipe_id) values (rid) on conflict (recipe_id) do nothing;
  select token into t from private.recipe_shares where recipe_id = rid;
  return t;
end; $$;
revoke all on function public.share_recipe(uuid) from public;
grant execute on function public.share_recipe(uuid) to anon, authenticated;
create or replace function public.shared_recipe(t uuid)
returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'name', r.name, 'description', r.description, 'prep_time', r.prep_time, 'cook_time', r.cook_time,
    'total_time', r.total_time, 'servings', r.servings, 'category', r.category, 'notes', r.notes,
    'source', r.source, 'ingredients', r.ingredients, 'steps', r.steps)
  from private.recipe_shares s join public.recipes r on r.id = s.recipe_id
  where s.token = t;
$$;
revoke all on function public.shared_recipe(uuid) from public;
grant execute on function public.shared_recipe(uuid) to anon, authenticated;
