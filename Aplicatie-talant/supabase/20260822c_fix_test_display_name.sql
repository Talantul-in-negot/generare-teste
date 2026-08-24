-- Repară numele afișat în clasamentul testelor: talant_test_recalculate_own_score
-- lua numele exclusiv din user_metadata.username, dar conturile create direct din
-- Supabase Dashboard (fără acel câmp) cădeau pe fallback-ul generic 'Utilizator',
-- spre deosebire de client (auth.js), care derivă numele din email dacă lipsește.
-- Rulează după 20260822_talant_test_scoring.sql / 20260822b_fix_test_attempt_id.sql.
begin;

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
  v_name := coalesce(
    nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''),
    nullif(initcap(replace(split_part(coalesce(auth.jwt() ->> 'email', ''), '@', 1), '_', ' ')), ''),
    'Utilizator'
  );
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

-- Corectează o singură dată rândurile deja salvate cu numele generic.
update public.talant_test_scores s
set user_name = initcap(replace(split_part(u.email, '@', 1), '_', ' ')), updated_at = now()
from auth.users u
where s.user_id = u.id and s.user_name = 'Utilizator';

update public.talant_test_attempts a
set user_name = initcap(replace(split_part(u.email, '@', 1), '_', ' '))
from auth.users u
where a.user_id = u.id and a.user_name = 'Utilizator';

commit;
