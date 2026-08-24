-- Splits the leaderboard by account domain: someone logged in with a
-- @citim.app account only sees other @citim.app accounts, and someone
-- logged in with a legacy @test.com account only sees other @test.com
-- accounts. Mirrors Talant's talant_user_group() domain-grouping pattern
-- (see Talant/supabase/20260823_talant_test_church.sql), adapted to Citim's
-- two known account domains instead of a lookup table.
--
-- auth.jwt() ->> 'email' resolves to the CALLING user's own email (from
-- their bearer token), so this only works for authenticated calls — fine,
-- since the app already requires login before opening the stats screen.
-- An anonymous call (no email claim) falls through to the @test.com branch.

begin;

drop function if exists public.get_public_leaderboard(integer);

create function public.get_public_leaderboard(p_limit integer default 5)
returns table(user_id uuid, user_name text, points integer)
language sql
security definer
set search_path = 'public', 'pg_temp'
as $function$
  select s.user_id, s.user_name::text, s.points::integer
  from public.scores s
  join auth.users u on u.id = s.user_id
  where s.user_name is not null
    and lower(u.email) like '%@' || (
      case
        when lower(auth.jwt() ->> 'email') like '%@citim.app' then 'citim.app'
        else 'test.com'
      end
    )
  order by s.points desc, s.user_name asc
  limit greatest(1, least(coalesce(p_limit, 5), 1000));
$function$;

grant execute on function public.get_public_leaderboard(integer) to anon, authenticated;

commit;
