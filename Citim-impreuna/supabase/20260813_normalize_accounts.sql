-- Normalize legacy test accounts and the names shown in the leaderboard.
-- Passwords are intentionally not changed.

begin;

update auth.users
set email = lower(email),
    raw_user_meta_data = coalesce(raw_user_meta_data, '{}'::jsonb)
      || jsonb_build_object(
        'username', initcap(replace(split_part(lower(email), '@', 1), '_', ' '))
      )
where email is not null
  and lower(email) like '%@test.com';

update public.scores s
set user_name = coalesce(nullif(trim(u.raw_user_meta_data ->> 'username'), ''), s.user_name)
from auth.users u
where s.user_id = u.id;

commit;
