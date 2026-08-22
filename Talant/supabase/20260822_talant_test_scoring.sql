-- Talant: teste tip "Talantul în negoț" (secțiuni I-IV, puncte variabile pe categorie).
-- Tabele și RPC-uri separate de talant_attempts/talant_scores (quiz-ul Ioan) — zero risc
-- pentru scorul și clasamentul existente. Folosește același cont (auth.users) ca restul aplicației.
-- Rulează o singură dată în SQL Editor-ul proiectului Supabase, după migrările anterioare.
begin;

create table if not exists public.talant_test_attempts (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  user_name text not null,
  client_attempt_id uuid not null unique,
  quiz_version text not null,
  total_points integer not null check (total_points >= 0),
  max_points integer not null check (max_points >= 0),
  section_scores jsonb not null check (jsonb_typeof(section_scores) = 'object'),
  attempted_at timestamptz not null default now(),
  recorded_at timestamptz not null default now()
);
create index if not exists talant_test_attempts_user_time_idx on public.talant_test_attempts (user_id, recorded_at desc);
create index if not exists talant_test_attempts_version_idx on public.talant_test_attempts (quiz_version, user_id);

create table if not exists public.talant_test_scores (
  user_id uuid not null references auth.users(id) on delete cascade,
  quiz_version text not null,
  user_name text not null,
  best_points integer not null default 0 check (best_points >= 0),
  max_points integer not null default 0 check (max_points >= 0),
  attempts integer not null default 0 check (attempts >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, quiz_version)
);

alter table public.talant_test_attempts enable row level security;
alter table public.talant_test_scores enable row level security;
revoke all on public.talant_test_attempts, public.talant_test_scores from anon, authenticated;

-- Păstrează cel mai bun scor obținut de utilizator la o versiune de test
-- (rezultatul e determinat pe server din jurnalul de încercări, nu din valorile trimise de browser).
create or replace function public.talant_test_recalculate_own_score(p_quiz_version text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
  v_best integer;
  v_max integer;
  v_attempts integer;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  v_name := initcap(coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator'));
  select max(total_points), max(max_points), count(*)
    into v_best, v_max, v_attempts
    from public.talant_test_attempts
    where user_id = v_user_id and quiz_version = p_quiz_version;
  insert into public.talant_test_scores (user_id, quiz_version, user_name, best_points, max_points, attempts, updated_at)
  values (v_user_id, p_quiz_version, v_name, coalesce(v_best, 0), coalesce(v_max, 0), coalesce(v_attempts, 0), now())
  on conflict (user_id, quiz_version) do update set
    user_name = excluded.user_name, best_points = excluded.best_points,
    max_points = excluded.max_points, attempts = excluded.attempts, updated_at = excluded.updated_at;
end;
$$;

create or replace function public.talant_record_test_attempt(
  quiz_version text,
  client_attempt_id uuid,
  total_points integer,
  max_points integer,
  section_scores jsonb,
  attempted_at timestamptz default now()
) returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  if total_points < 0 or max_points < 0 or total_points > max_points then raise exception 'Invalid attempt'; end if;
  if jsonb_typeof(section_scores) <> 'object' then raise exception 'Invalid attempt'; end if;
  v_name := initcap(coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator'));

  insert into public.talant_test_attempts
    (user_id, user_name, client_attempt_id, quiz_version, total_points, max_points, section_scores, attempted_at)
  values
    (v_user_id, v_name, client_attempt_id, left(coalesce(quiz_version, ''), 40),
     total_points, max_points, section_scores, now())
  on conflict (client_attempt_id) do nothing;

  perform public.talant_test_recalculate_own_score(left(coalesce(quiz_version, ''), 40));
end;
$$;

create or replace function public.talant_test_my_stats(p_quiz_version text)
returns table(best_points integer, max_points integer, attempts integer, updated_at timestamptz)
language sql security definer set search_path = public, pg_temp as $$
  select s.best_points, s.max_points, s.attempts, s.updated_at
  from public.talant_test_scores s
  where s.user_id = auth.uid() and s.quiz_version = p_quiz_version;
$$;

create or replace function public.talant_test_my_attempts(p_quiz_version text, p_limit integer default 20)
returns table(total_points integer, max_points integer, section_scores jsonb, attempted_at timestamptz)
language sql security definer set search_path = public, pg_temp as $$
  select a.total_points, a.max_points, a.section_scores, a.attempted_at
  from public.talant_test_attempts a
  where a.user_id = auth.uid() and a.quiz_version = p_quiz_version
  order by a.recorded_at desc limit least(greatest(p_limit, 1), 100);
$$;

create or replace function public.talant_test_leaderboard(p_quiz_version text, p_limit integer default 20)
returns table(rank bigint, user_name text, best_points integer, max_points integer)
language sql security definer set search_path = public, pg_temp as $$
  select row_number() over (order by s.best_points desc, s.updated_at asc), s.user_name, s.best_points, s.max_points
  from public.talant_test_scores s
  where s.quiz_version = p_quiz_version and s.best_points > 0
  order by s.best_points desc, s.updated_at asc limit least(greatest(p_limit, 1), 100);
$$;

revoke all on function public.talant_test_recalculate_own_score(text) from public;
revoke all on function public.talant_record_test_attempt(text, uuid, integer, integer, jsonb, timestamptz) from public;
revoke all on function public.talant_test_my_stats(text) from public;
revoke all on function public.talant_test_my_attempts(text, integer) from public;
revoke all on function public.talant_test_leaderboard(text, integer) from public;
grant execute on function public.talant_record_test_attempt(text, uuid, integer, integer, jsonb, timestamptz) to authenticated;
grant execute on function public.talant_test_my_stats(text) to authenticated;
grant execute on function public.talant_test_my_attempts(text, integer) to authenticated;
grant execute on function public.talant_test_leaderboard(text, integer) to authenticated;

commit;
