# Corpus biblic local

Aplicația nu descarcă și nu include implicit nicio traducere. Puneți un fișier licențiat și verificabil la `data/bible/bible.json`.

Formatul de bază este:

```json
{
  "translation": "Numele traducerii și licența",
  "books": {"1 Samuel": {"1": {"1": "textul exact al versetului"}}},
  "facts": [{
    "id": "1sam-1-1-subiect",
    "statement": "Enunț susținut literal de text.",
    "subject": "subiect",
    "predicate": "a făcut",
    "object": "obiectul sau rezultatul",
    "evidence": {"book": "1 Samuel", "chapter": 1, "verse_start": 1, "verse_end": 1, "text": "textul exact al versetului"}
  }]
}
```

`facts` este indexul verificat din care MVP-ul compune întrebările. Fiecare fapt include textul exact al dovezii; generatorul respinge lipsa de referințe și orice referință din afara selecției. Pentru un test complet sunt necesare cel puțin 28 de facts distincte, distribuite între capitolele selectate.
