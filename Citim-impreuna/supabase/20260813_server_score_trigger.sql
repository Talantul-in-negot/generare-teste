-- Keep scores server-derived even when the browser cannot call the score RPC.
-- The backfill preserves each existing baseline and adds only later events.

begin;

create or replace function public.recalculate_score_for_user(
  p_user_id uuid,
  p_user_name text default null
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_baseline_points integer := 0;
  v_baseline_at timestamptz := 'epoch'::timestamptz;
  v_existing_name text;
  v_name text;
  v_new_points integer := 0;
  v_streak_bonus integer := 0;
begin
  if p_user_id is null then
    return;
  end if;

  select s.baseline_points, s.baseline_at, s.user_name
    into v_baseline_points, v_baseline_at, v_existing_name
  from public.scores s
  where s.user_id = p_user_id
  order by s.updated_at desc nulls last
  limit 1;

  v_baseline_points := coalesce(v_baseline_points, 0);
  v_baseline_at := coalesce(v_baseline_at, 'epoch'::timestamptz);
  v_name := nullif(trim(p_user_name), '');

  if v_name is null then
    select nullif(trim(u.raw_user_meta_data ->> 'username'), '')
      into v_name
    from auth.users u
    where u.id = p_user_id;
  end if;

  v_name := coalesce(v_name, v_existing_name, 'Utilizator');

  with first_correct as (
    select
      coalesce(e.cycle, 0) as cycle_number,
      e.page_index,
      e.verse_ref,
      min(e.created_at) as first_correct_at
    from public.events e
    where e.user_id = p_user_id
      and e.correct is true
      and e.recorded_at > v_baseline_at
    group by coalesce(e.cycle, 0), e.page_index, e.verse_ref
  ),
  page_requirements as (
    select
      coalesce(e.cycle, 0) as cycle_number,
      e.page_index,
      max(e.page_size) as expected_answers
    from public.events e
    where e.user_id = p_user_id
      and e.recorded_at > v_baseline_at
      and e.page_index is not null
      and e.page_size is not null
    group by coalesce(e.cycle, 0), e.page_index
  ),
  completed_pages as (
    select
      r.cycle_number,
      r.page_index,
      r.expected_answers,
      count(f.verse_ref) as correct_answers,
      max(f.first_correct_at) as completed_at
    from page_requirements r
    left join first_correct f
      on f.cycle_number = r.cycle_number
      and f.page_index = r.page_index
    group by r.cycle_number, r.page_index, r.expected_answers
  )
  select
    count(*) * 10 + coalesce((
      select count(*) * 20
      from completed_pages p
      where p.correct_answers >= p.expected_answers
        and not exists (
          select 1
          from public.events wrong
          where wrong.user_id = p_user_id
            and wrong.recorded_at > v_baseline_at
            and coalesce(wrong.cycle, 0) = p.cycle_number
            and wrong.page_index = p.page_index
            and wrong.correct is false
            and wrong.created_at < p.completed_at
        )
    ), 0)
  into v_new_points
  from first_correct;

  with daily_chapters as (
    select
      (e.recorded_at at time zone 'Europe/Bucharest')::date as reading_date,
      e.chapter_ref,
      max(e.chapter_size) as expected_verses,
      count(distinct e.verse_ref) filter (where e.correct is true) as correct_verses
    from public.events e
    where e.user_id = p_user_id
      and e.recorded_at > v_baseline_at
      and e.chapter_ref is not null
      and e.chapter_size is not null
    group by (e.recorded_at at time zone 'Europe/Bucharest')::date, e.chapter_ref
  ),
  qualifying_days as (
    select distinct reading_date
    from daily_chapters
    where correct_verses >= expected_verses
  ),
  sequenced_days as (
    select
      reading_date,
      reading_date - row_number() over (order by reading_date)::integer as streak_group
    from qualifying_days
  ),
  streaks as (
    select count(*)::integer as day_count
    from sequenced_days
    group by streak_group
  )
  select coalesce(sum((day_count / 7) * 2000), 0)
  into v_streak_bonus
  from streaks;

  update public.scores
  set user_name = v_name,
      points = v_baseline_points + v_new_points + v_streak_bonus,
      updated_at = now()
  where user_id = p_user_id;

  if not found then
    insert into public.scores (
      user_id, user_name, points, baseline_points, baseline_at, updated_at
    ) values (
      p_user_id, v_name, v_new_points + v_streak_bonus, 0, 'epoch'::timestamptz, now()
    );
  end if;
end;
$$;

create or replace function public.upsert_own_score(
  p_user_name text default null,
  p_points integer default null
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user_id uuid := auth.uid();
  v_name text := coalesce(
    nullif(trim(auth.jwt() -> 'user_metadata' ->> 'username'), ''),
    'Utilizator'
  );
begin
  if v_user_id is null then
    raise exception 'Authentication is required';
  end if;

  perform public.recalculate_score_for_user(v_user_id, v_name);
end;
$$;

create or replace function public.recalculate_scores_after_event_insert()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user_id uuid;
begin
  for v_user_id in
    select distinct user_id
    from new_events
    where user_id is not null
  loop
    perform public.recalculate_score_for_user(v_user_id);
  end loop;
  return null;
end;
$$;

drop trigger if exists events_recalculate_scores on public.events;
create trigger events_recalculate_scores
after insert on public.events
referencing new table as new_events
for each statement
execute function public.recalculate_scores_after_event_insert();

do $$
declare
  v_user_id uuid;
begin
  for v_user_id in
    select distinct user_id
    from public.events
    where user_id is not null
  loop
    perform public.recalculate_score_for_user(v_user_id);
  end loop;
end;
$$;

revoke all on function public.recalculate_score_for_user(uuid, text) from public;
revoke all on function public.recalculate_scores_after_event_insert() from public;
revoke all on function public.upsert_own_score(text, integer) from public;
grant execute on function public.upsert_own_score(text, integer) to authenticated;

commit;
