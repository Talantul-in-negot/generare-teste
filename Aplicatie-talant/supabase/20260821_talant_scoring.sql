-- Talant: conturi, tentativi imuabile și clasament derivat pe server.
-- Rulează acest fișier o singură dată în SQL Editor-ul proiectului Supabase.
begin;

create table if not exists public.talant_attempts (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  user_name text not null,
  client_attempt_id uuid not null unique,
  quiz_version text not null default 'ioan-v1',
  round_key text not null,
  question_id integer not null check (question_id >= 0),
  selected_indices jsonb not null check (jsonb_typeof(selected_indices) = 'array'),
  correct boolean not null,
  attempted_at timestamptz not null default now(),
  recorded_at timestamptz not null default now()
);
create index if not exists talant_attempts_user_time_idx on public.talant_attempts (user_id, recorded_at desc);
create index if not exists talant_attempts_answer_idx on public.talant_attempts (user_id, quiz_version, question_id) where correct;

create table if not exists public.talant_scores (
  user_id uuid primary key references auth.users(id) on delete cascade,
  user_name text not null,
  points integer not null default 0 check (points >= 0),
  correct_answers integer not null default 0 check (correct_answers >= 0),
  attempts integer not null default 0 check (attempts >= 0),
  accuracy integer not null default 0 check (accuracy between 0 and 100),
  updated_at timestamptz not null default now()
);

alter table public.talant_attempts enable row level security;
alter table public.talant_scores enable row level security;
revoke all on public.talant_attempts, public.talant_scores from anon, authenticated;

create or replace function public.talant_recalculate_own_score()
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
  v_correct integer;
  v_attempts integer;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  v_name := coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator');
  select count(distinct (quiz_version, question_id)), count(*)
    into v_correct, v_attempts
    from public.talant_attempts where user_id = v_user_id and correct;
  select count(*) into v_attempts from public.talant_attempts where user_id = v_user_id;
  insert into public.talant_scores (user_id, user_name, points, correct_answers, attempts, accuracy, updated_at)
  values (v_user_id, v_name, v_correct * 10, v_correct, v_attempts,
          case when v_attempts = 0 then 0 else round(100.0 * v_correct / v_attempts)::integer end, now())
  on conflict (user_id) do update set user_name = excluded.user_name, points = excluded.points,
    correct_answers = excluded.correct_answers, attempts = excluded.attempts,
    accuracy = excluded.accuracy, updated_at = excluded.updated_at;
end;
$$;

create or replace function public.talant_record_attempt(
  question_id integer, round_key text, quiz_version text, selected_indices jsonb, correct boolean, client_attempt_id uuid, attempted_at timestamptz default now()
) returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v_user_id uuid := auth.uid(); v_name text;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  if question_id < 0 or jsonb_typeof(selected_indices) <> 'array' then raise exception 'Invalid attempt'; end if;
  v_name := coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator');
  insert into public.talant_attempts (user_id, user_name, client_attempt_id, question_id, round_key, quiz_version, selected_indices, correct, attempted_at)
  -- Momentul auditabil este stabilit pe server; valoarea clientului este ignorată.
  values (v_user_id, v_name, client_attempt_id, question_id, left(coalesce(round_key, ''), 80), left(coalesce(quiz_version, 'ioan-v1'), 40), selected_indices, correct, now())
  on conflict (client_attempt_id) do nothing;
  perform public.talant_recalculate_own_score();
end;
$$;

create or replace function public.talant_my_stats()
returns table(points integer, correct_answers integer, attempts integer, accuracy integer, updated_at timestamptz)
language sql security definer set search_path = public, pg_temp as $$
  select s.points, s.correct_answers, s.attempts, s.accuracy, s.updated_at from public.talant_scores s where s.user_id = auth.uid();
$$;

create or replace function public.talant_my_attempts(p_limit integer default 20)
returns table(question_id integer, round_key text, selected_indices jsonb, correct boolean, attempted_at timestamptz)
language sql security definer set search_path = public, pg_temp as $$
  select a.question_id, a.round_key, a.selected_indices, a.correct, a.attempted_at
  from public.talant_attempts a where a.user_id = auth.uid() order by a.recorded_at desc limit least(greatest(p_limit, 1), 100);
$$;

create or replace function public.talant_leaderboard(p_limit integer default 20)
returns table(rank bigint, user_name text, points integer, correct_answers integer, accuracy integer)
language sql security definer set search_path = public, pg_temp as $$
  select row_number() over (order by s.points desc, s.accuracy desc, s.updated_at asc), s.user_name, s.points, s.correct_answers, s.accuracy
  from public.talant_scores s where s.points > 0 order by s.points desc, s.accuracy desc, s.updated_at asc limit least(greatest(p_limit, 1), 100);
$$;

revoke all on function public.talant_recalculate_own_score() from public;
revoke all on function public.talant_record_attempt(integer, text, text, jsonb, boolean, uuid, timestamptz) from public;
revoke all on function public.talant_my_stats() from public;
revoke all on function public.talant_my_attempts(integer) from public;
revoke all on function public.talant_leaderboard(integer) from public;
grant execute on function public.talant_record_attempt(integer, text, text, jsonb, boolean, uuid, timestamptz) to authenticated;
grant execute on function public.talant_my_stats() to authenticated;
grant execute on function public.talant_my_attempts(integer) to authenticated;
grant execute on function public.talant_leaderboard(integer) to authenticated;
commit;
