# Talant — Cartea lui Ioan

Quiz static, cu conturi, punctaj cumulativ, istoric de încercări și clasament.

## Configurare rezultate și clasament

1. Rulează migrările existente de scor în ordine, apoi
   [migrarea pentru grupe](supabase/20260823_talant_test_church.sql) dacă folosești
   clasamentele pe biserici.
2. Rulează apoi [migrarea de scor securizat](supabase/20260824_secure_scoring.sql)
   în SQL Editor. Aceasta conține cele 558 de chei pentru quiz și 504 chei pentru
   testele Samuel, calculate din fișierele de întrebări din acest repository.
3. În Supabase Auth, confirmarea emailului poate rămâne dezactivată pentru
   conturile locale `@talant.app`. Auto-înregistrarea nu mai acceptă emailuri
   externe; conturile de grupă se creează numai de administrator.
4. Rulează `npm run build` înainte de publicare prin Sites; pagina publică
   răspunde la rădăcina domeniului.

Conturile Talant folosesc intern domeniul `@talant.app`, distinct de cele din
„Citim împreună”. Utilizatorul vede și introduce numai numele ales.

## Reguli de punctaj și audit

- Un punct pentru fiecare întrebare rezolvată corect, o singură dată per versiune de quiz.
- Fiecare răspuns este memorat cu setul, ID-ul întrebării, opțiunile alese,
  rezultatul și momentul încercării.
- Totalul, corectitudinea și clasamentul sunt calculate în Supabase din cheile
  de răspuns păstrate în tabele neaccesibile browserului; clientul trimite numai
  opțiunile selectate.
- Rezultatele v1 vechi nu sunt șterse, dar sunt izolate ca „legacy” și nu mai
  intră în statistici sau clasamentele v2. Testele Samuel folosesc separat
  versiunile v2.

## Verificare

```powershell
npm test
npm run build
```

Dacă modifici întrebările sau baremele, actualizează versiunile quiz-urilor,
rulează `node scripts/generate-secure-scoring-migration.js`, apoi aplică noua
migrare generată înainte de publicare.
