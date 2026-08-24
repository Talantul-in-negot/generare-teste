-- public.scores avea încă cheia primară pe user_name, moștenită de dinainte
-- ca aplicația să suporte mai multe conturi cu același nume afișat. Cum două
-- conturi diferite (sergiu@test.com și sergiu@citim.app) se numesc amândouă
-- "Sergiu", orice INSERT nou pe al doilea cont intra în conflict cu rândul
-- deja existent al primului (23505 duplicate key pe scores_pkey), iar acel
-- eșec, ridicat din triggerul events_recalculate_scores (AFTER INSERT pe
-- events, aceeași tranzacție), făcea rollback inclusiv la evenimentele
-- proaspăt trimise — de-aia rămâneau blocate în coada locală, retrimise la
-- nesfârșit, fără să ajungă niciodată persistate.
--
-- Cheia primară trebuie să fie user_id (unic per cont), nu user_name (poate
-- fi partajat de mai multe conturi). 20260824_leaderboard_user_id.sql a
-- schimbat deja funcția publică să distingă pe user_id — migrația de față
-- aliniază și schema tabelei cu acea intenție.

begin;

alter table public.scores drop constraint if exists scores_pkey;
alter table public.scores add constraint scores_pkey primary key (user_id);

commit;
