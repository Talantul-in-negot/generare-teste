begin;

-- Legacy browser-claimed scores remain in the database but are deliberately
-- excluded from all v2 stats/leaderboards after this migration.
create table if not exists public.talant_quiz_answer_keys (
  quiz_version text not null,
  question_id integer not null check (question_id >= 0),
  correct_indices jsonb not null check (jsonb_typeof(correct_indices) = 'array'),
  primary key (quiz_version, question_id)
);
create table if not exists public.talant_test_answer_keys (
  quiz_version text not null,
  section_id text not null,
  item_index integer not null check (item_index >= 0),
  correct_answer jsonb not null,
  points integer not null check (points > 0),
  primary key (quiz_version, section_id, item_index)
);
alter table public.talant_quiz_answer_keys enable row level security;
alter table public.talant_test_answer_keys enable row level security;
revoke all on public.talant_quiz_answer_keys, public.talant_test_answer_keys from anon, authenticated;

alter table public.talant_scores add column if not exists score_version text not null default 'legacy';
create unique index if not exists talant_attempts_v2_user_question_idx
  on public.talant_attempts (user_id, quiz_version, question_id) where quiz_version = 'ioan-v2';

drop function if exists public.talant_record_attempt(integer, text, text, jsonb, boolean, uuid, timestamptz);
create or replace function public.talant_recalculate_own_score()
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
  v_correct integer;
  v_attempts integer;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  v_name := initcap(split_part(coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator'), '@', 1));
  select count(*) filter (where correct), count(*) into v_correct, v_attempts
    from public.talant_attempts
    where user_id = v_user_id and quiz_version = 'ioan-v2';
  insert into public.talant_scores (user_id, user_name, points, correct_answers, attempts, accuracy, score_version, updated_at)
  values (v_user_id, v_name, v_correct, v_correct, v_attempts,
          case when v_attempts = 0 then 0 else round(100.0 * v_correct / v_attempts)::integer end, 'ioan-v2', now())
  on conflict (user_id) do update set user_name = excluded.user_name, points = excluded.points,
    correct_answers = excluded.correct_answers, attempts = excluded.attempts, accuracy = excluded.accuracy,
    score_version = excluded.score_version, updated_at = excluded.updated_at;
end;
$$;

create or replace function public.talant_record_attempt(
  p_quiz_version text,
  p_question_id integer,
  p_selected_indices jsonb,
  p_client_attempt_id uuid
) returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
  v_correct boolean;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  if jsonb_typeof(p_selected_indices) <> 'array' then raise exception 'Invalid answer'; end if;
  select k.correct_indices = p_selected_indices into v_correct
    from public.talant_quiz_answer_keys k
    where k.quiz_version = p_quiz_version and k.question_id = p_question_id;
  if v_correct is null then raise exception 'Unknown quiz question'; end if;
  v_name := initcap(split_part(coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator'), '@', 1));
  insert into public.talant_attempts
    (user_id, user_name, client_attempt_id, question_id, round_key, quiz_version, selected_indices, correct, attempted_at)
  values (v_user_id, v_name, p_client_attempt_id, p_question_id, '', p_quiz_version, p_selected_indices, v_correct, now())
  on conflict (user_id, quiz_version, question_id) where quiz_version = 'ioan-v2' do update set
    client_attempt_id = excluded.client_attempt_id, user_name = excluded.user_name,
    selected_indices = case when excluded.correct then excluded.selected_indices else public.talant_attempts.selected_indices end,
    correct = public.talant_attempts.correct or excluded.correct, attempted_at = excluded.attempted_at,
    recorded_at = now();
  perform public.talant_recalculate_own_score();
end;
$$;

create or replace function public.talant_my_stats()
returns table(points integer, correct_answers integer, attempts integer, accuracy integer, updated_at timestamptz)
language sql security definer set search_path = public, pg_temp as $$
  select s.points, s.correct_answers, s.attempts, s.accuracy, s.updated_at from public.talant_scores s
  where s.user_id = auth.uid() and s.score_version = 'ioan-v2';
$$;
create or replace function public.talant_my_attempts(p_limit integer default 20)
returns table(question_id integer, round_key text, selected_indices jsonb, correct boolean, attempted_at timestamptz)
language sql security definer set search_path = public, pg_temp as $$
  select a.question_id, a.round_key, a.selected_indices, a.correct, a.attempted_at from public.talant_attempts a
  where a.user_id = auth.uid() and a.quiz_version = 'ioan-v2'
  order by a.recorded_at desc limit least(greatest(p_limit, 1), 100);
$$;
-- The v2 leaderboard adds accuracy to the returned row type; PostgreSQL
-- requires the old function to be dropped rather than replaced in place.
drop function if exists public.talant_leaderboard(integer);
create or replace function public.talant_leaderboard(p_limit integer default 20)
returns table(rank bigint, user_name text, points integer, accuracy integer)
language sql security definer set search_path = public, pg_temp as $$
  select row_number() over (order by s.points desc, s.accuracy desc, s.updated_at asc), s.user_name, s.points, s.accuracy
  from public.talant_scores s where s.points > 0 and s.score_version = 'ioan-v2'
  order by s.points desc, s.accuracy desc, s.updated_at asc limit least(greatest(p_limit, 1), 100);
$$;

drop function if exists public.talant_record_test_attempt(text, uuid, integer, integer, jsonb, timestamptz);
create or replace function public.talant_record_test_attempt(
  p_quiz_version text,
  p_client_attempt_id uuid,
  p_answers jsonb
) returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
  v_total integer;
  v_max integer;
  v_sections jsonb;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  if jsonb_typeof(p_answers) <> 'object' then raise exception 'Invalid answers'; end if;
  if not exists (select 1 from public.talant_test_answer_keys where quiz_version = p_quiz_version) then
    raise exception 'Unknown quiz version';
  end if;
  select coalesce(sum(case when coalesce(p_answers #> array[k.section_id, k.item_index::text], 'null'::jsonb) = k.correct_answer then k.points else 0 end), 0),
         coalesce(sum(k.points), 0)
    into v_total, v_max from public.talant_test_answer_keys k where k.quiz_version = p_quiz_version;
  select coalesce(jsonb_object_agg(section_id, jsonb_build_object('earned', earned, 'max', max_points)), '{}'::jsonb)
    into v_sections from (
      select k.section_id,
        sum(case when coalesce(p_answers #> array[k.section_id, k.item_index::text], 'null'::jsonb) = k.correct_answer then k.points else 0 end)::integer as earned,
        sum(k.points)::integer as max_points
      from public.talant_test_answer_keys k where k.quiz_version = p_quiz_version group by k.section_id
    ) section_totals;
  v_name := initcap(split_part(coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator'), '@', 1));
  insert into public.talant_test_attempts
    (user_id, user_name, client_attempt_id, quiz_version, total_points, max_points, section_scores, attempted_at)
  values (v_user_id, v_name, p_client_attempt_id, p_quiz_version, v_total, v_max, v_sections, now())
  on conflict (client_attempt_id) do nothing;
  perform public.talant_test_recalculate_own_score(p_quiz_version);
end;
$$;

create or replace function public.talant_test_recalculate_own_score(p_quiz_version text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
  v_group text;
  v_best integer;
  v_max integer;
  v_attempts integer;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  v_name := initcap(split_part(coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator'), '@', 1));
  v_group := public.talant_user_group();
  select max(total_points), max(max_points), count(*) into v_best, v_max, v_attempts
    from public.talant_test_attempts where user_id = v_user_id and quiz_version = p_quiz_version;
  insert into public.talant_test_scores (user_id, quiz_version, user_name, group_name, best_points, max_points, attempts, updated_at)
  values (v_user_id, p_quiz_version, v_name, v_group, coalesce(v_best, 0), coalesce(v_max, 0), coalesce(v_attempts, 0), now())
  on conflict (user_id, quiz_version) do update set user_name = excluded.user_name, group_name = excluded.group_name,
    best_points = excluded.best_points, max_points = excluded.max_points, attempts = excluded.attempts, updated_at = excluded.updated_at;
end;
$$;

revoke all on function public.talant_recalculate_own_score() from public;
revoke all on function public.talant_record_attempt(text, integer, jsonb, uuid) from public;
revoke all on function public.talant_my_stats() from public;
revoke all on function public.talant_my_attempts(integer) from public;
revoke all on function public.talant_leaderboard(integer) from public;
revoke all on function public.talant_record_test_attempt(text, uuid, jsonb) from public;
revoke all on function public.talant_test_recalculate_own_score(text) from public;
grant execute on function public.talant_record_attempt(text, integer, jsonb, uuid) to authenticated;
grant execute on function public.talant_my_stats() to authenticated;
grant execute on function public.talant_my_attempts(integer) to authenticated;
grant execute on function public.talant_leaderboard(integer) to authenticated;
grant execute on function public.talant_record_test_attempt(text, uuid, jsonb) to authenticated;
