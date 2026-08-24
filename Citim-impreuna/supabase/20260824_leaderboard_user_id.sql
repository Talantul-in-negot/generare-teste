-- get_public_leaderboard returned only (user_name, points), so the client
-- could not distinguish two different accounts sharing the same display
-- name (e.g. sergiu@citim.app vs sergiu@test.com, both named "Sergiu") and
-- silently merged them client-side, taking whichever had more points.
-- Adding user_id lets the client key/highlight rows by actual identity.
--
-- Changing a TABLE function's return columns requires DROP + CREATE (not
-- CREATE OR REPLACE), which also drops existing grants — re-granted below
-- to both anon and authenticated so public leaderboard access is preserved.

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
  where s.user_name is not null
  order by s.points desc, s.user_name asc
  limit greatest(1, least(coalesce(p_limit, 5), 1000));
$function$;

grant execute on function public.get_public_leaderboard(integer) to anon, authenticated;

commit;
