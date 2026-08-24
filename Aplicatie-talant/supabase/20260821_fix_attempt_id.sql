-- Repară conflictul dintre parametrul RPC și coloana client_attempt_id.
begin;

create or replace function public.talant_record_attempt(
  question_id integer,
  round_key text,
  quiz_version text,
  selected_indices jsonb,
  correct boolean,
  p_client_attempt_id uuid,
  attempted_at timestamptz default now()
) returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user_id uuid := auth.uid();
  v_name text;
begin
  if v_user_id is null then raise exception 'Authentication is required'; end if;
  if question_id < 0 or jsonb_typeof(selected_indices) <> 'array' then raise exception 'Invalid attempt'; end if;
  v_name := initcap(coalesce(nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''), 'Utilizator'));

  insert into public.talant_attempts
    (user_id, user_name, client_attempt_id, question_id, round_key, quiz_version, selected_indices, correct, attempted_at)
  values
    (v_user_id, v_name, p_client_attempt_id, question_id,
     left(coalesce(round_key, ''), 80), left(coalesce(quiz_version, 'ioan-v1'), 40),
     selected_indices, correct, now())
  on conflict (client_attempt_id) do nothing;

  perform public.talant_recalculate_own_score();
end;
$$;

commit;
