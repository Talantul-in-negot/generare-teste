-- Talant: un punct pentru fiecare răspuns corect și nume afișat cu inițială mare.
-- Rulează o singură dată în SQL Editor, după 20260821_talant_scoring.sql.
begin;

create or replace function public.talant_recalculate_own_score()
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
  v_correct integer;
  v_attempts integer;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  v_name := initcap(coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator'));
  select count(distinct (quiz_version, question_id))
    into v_correct
    from public.talant_attempts where user_id = v_user_id and correct;
  select count(*) into v_attempts from public.talant_attempts where user_id = v_user_id;
  insert into public.talant_scores (user_id, user_name, points, correct_answers, attempts, accuracy, updated_at)
  values (v_user_id, v_name, v_correct, v_correct, v_attempts,
          case when v_attempts = 0 then 0 else round(100.0 * v_correct / v_attempts)::integer end, now())
  on conflict (user_id) do update set user_name = excluded.user_name, points = excluded.points,
    correct_answers = excluded.correct_answers, attempts = excluded.attempts,
    accuracy = excluded.accuracy, updated_at = excluded.updated_at;
end;
$$;

-- Aduce totalurile existente la noua regulă și capitalizează clasamentul curent.
update public.talant_scores
set points = correct_answers, user_name = initcap(user_name), updated_at = now();

commit;
