# Talant — Cartea lui Ioan

Quiz static, cu conturi, punctaj cumulativ, istoric de încercări și clasament.

## Configurare rezultate și clasament

1. Deschide proiectul Supabase folosit de aplicație și rulează o singură dată
   [migrarea SQL](supabase/20260821_talant_scoring.sql) în SQL Editor.
2. În Supabase Auth, dezactivează confirmarea prin email dacă vrei ca un cont nou
   să poată intra imediat.
3. Rulează `npm run build` înainte de publicare prin Sites; pagina publică
   răspunde la rădăcina domeniului.

Conturile Talant folosesc intern domeniul `@talant.app`, distinct de cele din
„Citim împreună”. Utilizatorul vede și introduce numai numele ales.

## Reguli de punctaj și audit

- 10 puncte pentru fiecare întrebare rezolvată corect, o singură dată per versiune de quiz.
- Fiecare răspuns este memorat cu setul, ID-ul întrebării, opțiunile alese,
  rezultatul și momentul încercării.
- Totalul și clasamentul sunt calculate în Supabase din jurnalul de încercări;
  browserul nu trimite un total de puncte.
- Pentru integritate deplină împotriva modificării din DevTools, cheia de răspuns
  trebuie menținută exclusiv pe server. În această aplicație statică, răspunsurile
  există și în `questions.js`, deci mecanismul este auditabil, nu anti-trișare.

## Verificare

```powershell
npm test
```
