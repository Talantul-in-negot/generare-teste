# Generator de teste biblice - Talantul în Negoț

Generator local, determinist și audibil pentru două documente sincronizate: testul concurenților și baremul corectorilor. Nu există secțiunea V.

## Ce a fost preluat din PDF-urile de referință

- A4 portret, margini de aproximativ 10 mm și font sans-serif de aproximativ 10-11 pt;
- header în trei zone: titlu/ediție, categorie-etapă-dată, versiune;
- titluri de secțiune aldine, separatoare orizontale și item-uri păstrate împreună;
- referințe aliniate la dreapta, roșii și italice;
- căsuțe pentru A/F și asociere; în barem, literele și variantele corecte sunt roșii italice;
- documentele de referință au inclus V, dar generatorul livrează strict I-IV.

## Arhitectură

`BibleRepository` citește exclusiv corpusul local. Selecția este analizată de `selection.py`, iar un index de facts verificat livrează dovezile către generatorul deterministic. `validation.py` verifică structură, bijecții, distribuția II, duplicate și scope; `rendering.py` produce ambele PDF-uri din exact aceeași `TestDefinition`. `output/Vn/test.json` este sursa de adevăr auditabilă.

Atenție la ce garantează fiecare verificare. `validate_evidence` compară dovada fiecărei întrebări cu `get_verse`, dar ambele provin din același parser — deci confirmă că generatorul a citat corpusul fidel, nu că acel corpus spune ce spune textul sursă. Singura verificare cu adevăr din afara parserului este `data/verse-counts.json`: un tabel scris de mână cu numărul de versete din fiecare capitol, față de care `BibleRepository` își compară parsarea la încărcare și refuză un corpus din care lipsesc sau în care s-au contopit versete. Actualizați-l numai după un text tipărit/autoritativ.

## Instalare

```bash
python -m pip install -r requirements.txt
copy config.example.yaml config.yaml
```

Pentru rularea testelor este nevoie și de dependențele de dezvoltare:

```bash
python -m pip install -r requirements-dev.txt
```

## Configurarea corpusului

Puneți traducerea licențiată/verificabilă în `data/bible/bible.json`. Nu este inclusă sau descărcată automat nicio traducere. Consultați [data/bible/README.md](data/bible/README.md) pentru formatul exact și licența pe care trebuie să o notați.

Pentru corectitudinea baremului, fiecare `fact` conține textul exact al dovezii, referința calculată structural și relația de generare. Fără cel puțin 28 de facts distincte în selecția cerută, generatorul refuză să publice un test incomplet sau cu întrebări duplicate.

## Rulare CLI

```bash
python generate.py --chapters "1 Samuel 1,2,3" --version 1
```

Pentru selecția `1 Samuel 1,2,3`, rezultatele sunt `output/V1/1 Samuel 1-3 V1.pdf`, `output/V1/1 Samuel 1-3 V1 barem.pdf` și `output/V1/test.json`. Numărul variantei apare atât în numele fișierului, cât și în colțul din dreapta al paginii; baremul se anunță acolo ca atare.

Selecția trebuie să acopere cel puțin 3 capitole — același prag pe care îl aplică și interfața web, din același motiv (vezi mai jos).

Mai multe variante se cer într-o singură rulare, cu `--versions`:

```bash
python generate.py --chapters "1 Samuel 1,2,3" --versions 2
```

Astfel fiecare variantă află ce au consumat surorile ei și le ocolește pe cât poate: pe `1 Samuel 1,2,3`, versetele comune între V1 și V2 scad de la 13 din 28 la 7. Nu la zero, și anume intenționat — `build_test` amână faptele deja folosite (le trimite la coada bazinului), nu le exclude, ca o selecție abia suficientă pentru un test să poată produce totuși al doilea în loc să eșueze.

Cerute în rulări separate, variantele nu beneficiază deloc: generatorul nu ține minte nimic între procese. `--version` rămâne numărul primei variante.

### Curățarea directorului de ieșire

Interfața web își șterge singură sesiunile mai vechi de 24 de ore, dar numai când primește o cerere. Pe o mașină locală directorul se adună, așa că CLI-ul poate face curat după o rulare reușită:

```bash
python generate.py --chapters "1 Samuel 1,2,3" --versions 2 --keep-recent 5
```

Păstrează cele mai recente 5 intrări generate și le șterge pe celelalte, listând ce a șters. Trei garanții, fiindcă opțiunea chiar șterge:

- variantele rulării curente sunt protejate întotdeauna, indiferent de vechimea aparentă a directorului — nici `--keep-recent 0` nu poate șterge testul tocmai generat;
- se șterg numai directoarele care chiar conțin teste generate (`test.json`, direct sau la un nivel sub — ambele forme, a CLI-ului și a interfeței web). Un fișier lăsat acolo, un dosar propriu sau un director rămas dintr-o rulare întreruptă nu sunt atinse;
- curățarea rulează abia după ce toate variantele au fost scrise și validate: o rulare eșuată nu ia cu ea și rezultatele celei anterioare.

Fără opțiune nu se șterge nimic.

## Interfață web locală

```bash
python -m src.web.app
```

Deschideți `http://127.0.0.1:8000`. Interfața susține selecții pe linii, categorie, ediție, etapă, dată, seed și mai multe variante. Selecția trebuie să acopere cel puțin 3 capitole (`MIN_SELECTION_CHAPTERS`, definit lângă parser în `selection.py` pentru ca CLI-ul și formularul să nu poată ajunge la praguri diferite): sub acest prag testul complet (28 de fapte distincte, pe patru secțiuni care concurează pentru același bazin) eșuează la generare în aproximativ un sfert din cazuri, față de niciodată de la 3 capitole în sus.

Local, serverul ascultă numai pe `127.0.0.1`. Când platforma de găzduire setează `PORT`, ascultă pe toate interfețele; setați `HOST` pentru a forța o adresă anume. În spatele unui router de platformă setați `TRUST_PROXY=1` (așa cum face `Procfile`), altfel limita de generări per utilizator devine o limită globală, comună tuturor vizitatorilor: fără antetul `X-Forwarded-For` toate cererile par să vină de la aceeași adresă, cea a routerului.

### Jurnalul de utilizare

Fiecare generare reușită (nu și cele eșuate) e înregistrată într-un fișier text, o linie: data/ora UTC, adresa reală a apelantului (aceeași verificată de `TRUST_PROXY`, nu adresa routerului), selecția generată, numărul de variante. Implicit `data/usage.log`, mutabil cu variabila de mediu `USAGE_LOG_PATH`. Aceeași linie apare și pe stdout, deci și în log-ul platformei de găzduire.

Fișierul e local procesului care rulează — pe o platformă cu disc efemer (planul gratuit Render, printre altele) nu supraviețuiește unui redeploy sau unei reporniri după inactivitate, ci acoperă doar intervalul de la ultima pornire.

Pentru păstrare completă între redeployuri, aceeași înregistrare se trimite opțional și către Supabase (Postgres găzduit), dacă sunt setate `SUPABASE_URL` și `SUPABASE_SERVICE_KEY` ca variabile de mediu — nicio cheie nu se stochează în repository, la fel ca la integrarea LLM de mai jos. `SUPABASE_SERVICE_KEY` trebuie să fie cheia *service role* (nu cea publică/anon): apelul se face exclusiv din acest server, niciodată dintr-un browser, iar cheia service ocolește Row Level Security fără să fie nevoie de nicio politică suplimentară. Fără cele două variabile, comportamentul rămâne identic cu cel de mai sus — fișier local + stdout.

Tabelul se creează o singură dată, în editorul SQL al proiectului Supabase ales:

```sql
create table if not exists usage_log (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  client_ip text not null,
  selection text not null,
  versions int not null
);

alter table usage_log enable row level security;
```

Pentru a păstra și descărcările PDF, se creează suplimentar:

```sql
create table if not exists download_log (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  client_ip text not null,
  session_id text not null,
  version int not null,
  document_type text not null check (document_type in ('contest', 'answer_key')),
  filename text not null
);

alter table download_log enable row level security;
```

(RLS activat din prudență, deși nu e nevoie de nicio politică — cheia service role o ocolește oricum.)

## LLM (opțional, neimplementat intenționat în MVP)

O integrare LLM poate propune facts, dar trebuie să emită JSON strict, să primească numai versetele selectate și să treacă prin verificarea deterministă înainte de a fi salvată în corpus. Cheile nu se stochează în repository; se folosesc numai variabile de mediu. Renderingul și validarea nu depind de vreun provider.

## Teste

```bash
python -m unittest discover -s tests -v
```

Testele acoperă parserul, scope-ul, I-IV, bijecția, PDF-urile pereche și absența expresă a secțiunii V.
