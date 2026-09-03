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
python generate.py --chapters "1 Samuel 1,2" --version 1
```

Pentru selecția `1 Samuel 1,2`, rezultatele sunt `output/V1/1 Samuel 1-2.pdf`, `output/V1/1 Samuel 1-2 barem.pdf` și `output/V1/test.json`.

## Interfață web locală

```bash
python -m src.web.app
```

Deschideți `http://127.0.0.1:8000`. Interfața susține selecții pe linii, categorie, ediție, etapă, dată, seed și mai multe variante.

Local, serverul ascultă numai pe `127.0.0.1`. Când platforma de găzduire setează `PORT`, ascultă pe toate interfețele; setați `HOST` pentru a forța o adresă anume. În spatele unui router de platformă setați `TRUST_PROXY=1` (așa cum face `Procfile`), altfel limita de generări per utilizator devine o limită globală, comună tuturor vizitatorilor: fără antetul `X-Forwarded-For` toate cererile par să vină de la aceeași adresă, cea a routerului.

## LLM (opțional, neimplementat intenționat în MVP)

O integrare LLM poate propune facts, dar trebuie să emită JSON strict, să primească numai versetele selectate și să treacă prin verificarea deterministă înainte de a fi salvată în corpus. Cheile nu se stochează în repository; se folosesc numai variabile de mediu. Renderingul și validarea nu depind de vreun provider.

## Teste

```bash
python -m unittest discover -s tests -v
```

Testele acoperă parserul, scope-ul, I-IV, bijecția, PDF-urile pereche și absența expresă a secțiunii V.
