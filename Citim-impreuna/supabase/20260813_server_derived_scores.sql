-- Score integrity migration.
-- Apply once in Supabase SQL Editor before deploying the matching client build.

begin;

alter table public.events
  add column if not exists page_index integer,
  add column if not exists page_size smallint,
  add column if not exists recorded_at timestamptz not null default now();

alter table public.events
  drop constraint if exists events_page_index_check,
  drop constraint if exists events_page_size_check;

alter table public.events
  add constraint events_page_index_check check (page_index is null or page_index >= 0),
  add constraint events_page_size_check check (page_size is null or page_size between 1 and 5);

-- Preserve every participant's already-calculated result. New events are added
-- server-side after this timestamp, so queued offline events are not lost.
alter table public.scores
  add column if not exists baseline_points integer not null default 0,
  add column if not exists baseline_at timestamptz;

update public.scores
set baseline_points = greatest(coalesce(points, 0), 0),
    baseline_at = coalesce(baseline_at, now());

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
  v_baseline_points integer := 0;
  v_baseline_at timestamptz := 'epoch'::timestamptz;
  v_new_points integer := 0;
begin
  if v_user_id is null then
    raise exception 'Authentication is required';
  end if;

  -- p_user_name and p_points remain optional only so older clients keep working.
  -- Neither value is trusted or used to calculate the score.
  select s.baseline_points, s.baseline_at
    into v_baseline_points, v_baseline_at
  from public.scores s
  where s.user_id = v_user_id
  order by s.updated_at desc nulls last
  limit 1;

  v_baseline_points := coalesce(v_baseline_points, 0);
  v_baseline_at := coalesce(v_baseline_at, 'epoch'::timestamptz);

  with first_correct as (
    select
      coalesce(e.cycle, 0) as cycle_number,
      e.page_index,
      e.verse_ref,
      min(e.created_at) as first_correct_at
    from public.events e
    where e.user_id = v_user_id
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
    where e.user_id = v_user_id
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
          where wrong.user_id = v_user_id
            and wrong.recorded_at > v_baseline_at
            and coalesce(wrong.cycle, 0) = p.cycle_number
            and wrong.page_index = p.page_index
            and wrong.correct is false
            and wrong.created_at < p.completed_at
        )
    ), 0)
  into v_new_points
  from first_correct;

  update public.scores
  set user_name = v_name,
      points = v_baseline_points + v_new_points,
      updated_at = now()
  where user_id = v_user_id;

  if not found then
    insert into public.scores (
      user_id, user_name, points, baseline_points, baseline_at, updated_at
    ) values (
      v_user_id, v_name, v_new_points, 0, now(), now()
    );
  end if;
end;
$$;

revoke all on function public.upsert_own_score(text, integer) from public;
grant execute on function public.upsert_own_score(text, integer) to authenticated;

commit;
