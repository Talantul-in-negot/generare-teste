-- Repară conflictul dintre parametrul RPC și coloana client_attempt_id
-- în talant_record_test_attempt (aceeași problemă reparată în 2026-08-21
-- pentru talant_record_attempt). Rulează după 20260822_talant_test_scoring.sql.
begin;

create or replace function public.talant_record_test_attempt(
  quiz_version text,
  p_client_attempt_id uuid,
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
    (v_user_id, v_name, p_client_attempt_id, left(coalesce(quiz_version, ''), 40),
     total_points, max_points, section_scores, now())
  on conflict (client_attempt_id) do nothing;

  perform public.talant_test_recalculate_own_score(left(coalesce(quiz_version, ''), 40));
end;
$$;

commit;
