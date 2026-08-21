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

## Instalare

```bash
python -m pip install -r requirements.txt
copy config.example.yaml config.yaml
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

Deschideți `http://127.0.0.1:8000`. Interfața susține selecții pe linii, categorie, ediție, etapă, dată, dificultate, seed și mai multe variante.

## LLM (opțional, neimplementat intenționat în MVP)

O integrare LLM poate propune facts, dar trebuie să emită JSON strict, să primească numai versetele selectate și să treacă prin verificarea deterministă înainte de a fi salvată în corpus. Cheile nu se stochează în repository; se folosesc numai variabile de mediu. Renderingul și validarea nu depind de vreun provider.

## Teste

```bash
python -m unittest discover -s tests -v
```

Testele acoperă parserul, scope-ul, I-IV, bijecția, PDF-urile pereche și absența expresă a secțiunii V.
