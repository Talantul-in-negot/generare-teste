-- Return every participant with points, in pages.  The client keeps fetching
-- until a short page is returned, avoiding Supabase's per-response row cap.

begin;

create or replace function public.get_public_leaderboard(
  p_limit integer default 1000,
  p_offset integer default 0
)
returns table(user_name text, points integer)
language sql
security definer
set search_path = public, pg_temp
as $function$
  select s.user_name::text, s.points::integer
  from public.scores s
  join auth.users u on u.id = s.user_id
  where auth.uid() is not null
    and s.user_name is not null
    and s.points > 0
    and lower(u.email) like '%@' || (
      case
        when lower(auth.jwt() ->> 'email') like '%@citim.app' then 'citim.app'
        else 'test.com'
      end
    )
  order by s.points desc, s.user_name asc
  limit greatest(1, least(coalesce(p_limit, 1000), 1000))
  offset greatest(0, coalesce(p_offset, 0));
$function$;

revoke all on function public.get_public_leaderboard(integer, integer) from public, anon;
grant execute on function public.get_public_leaderboard(integer, integer) to authenticated;

commit;
