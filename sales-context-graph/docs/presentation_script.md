# Sales Context Graph — Script de prezentare
### Scene + Voiceover (video demo / pitch, ~4 min)

---

## SCENA 1 — Deschidere (0:00–0:20)

**VIZUAL:** Ecran negru. Apare, literă cu literă, o propoziție tastată live:
*"Given an opportunity, identify the objection raised by a stakeholder in the
latest relevant call and recommend an appropriate content asset the buyer
hasn't already viewed — with exact evidence."*

**VOICEOVER:**
> "Orice AI de vânzări poate rezuma un apel. Foarte puține pot răspunde la
> întrebarea care contează cu adevărat: *de ce* nu s-a închis deal-ul — și ce
> să faci, chiar acum, ca să-l deblochezi. Ăsta e Sales Context Graph."

---

## SCENA 2 — Problema reală (0:20–0:50)

**VIZUAL:** Split screen — stânga: un CRM cu un card de opportunity static și
gol; dreapta: o transcriere de apel derulând rapid, plină de zgomot.

**VOICEOVER:**
> "Datele de vânzări trăiesc rupte în trei silozuri: CRM-ul spune *ce stadiu*
> are deal-ul. Apelurile spun *de ce* — obiecții, semnale de cumpărare,
> blocaje — dar îngropate în text nestructurat. Iar platforma de content
> habar n-are dacă materialul trimis a fost măcar deschis. Un asistent AI
> conectat direct la un LLM peste transcript ghicește. Noi construim un graf."

---

## SCENA 3 — Reveal arhitectural (0:50–1:30)

**VIZUAL:** Cele trei surse (Salesforce, Gong, Showpad) converg vizual către
un nod central care se transformă într-un graf Neo4j animat, cu noduri
`Account`, `Opportunity`, `Claim`, `ContentAsset` aprinzându-se pe rând.

**VOICEOVER:**
> "CRM-ul dă identitatea comercială canonică. Transcrierile devin *Claims* —
> afirmații cu dovadă, nu fapte de graf sacre: fiecare obiecție, fiecare
> semnal de cumpărare are un span exact de text, un vorbitor, o polaritate —
> afirmat, negat, ipotetic — și un scor de încredere. Nimic nu e adevăr
> absolut până nu e coroborat. Iar identitatea entităților nu e niciodată
> lăsată la voia similarității textuale — e rezolvată determinist unde se
> poate, probabilistic cu prag de margine unde nu se poate, și trimisă la
> revizuire umană când e ambiguă. 'Volks Wagen' scris greșit într-un apel nu
> se leagă niciodată orbește de 'Volkswagen Financial Services' — semnale
> relaționale independente decid, nu un singur scor de similaritate."

---

## SCENA 4 — Demo live: recomandarea (1:30–2:20)

**VIZUAL:** Ecran de terminal / UI `/viz` — se apasă un buton "Ask" cu
întrebarea: *"what's blocking the Volkswagen deal?"*. Răspunsul apare
progresiv: obiecția identificată, apelul sursă, span-ul exact de text
subliniat, apoi asset-ul de content recomandat cu motivul explicit de
excludere ("already viewed" pentru un candidat, exclus).

**VOICEOVER:**
> "Sistemul găsește cel mai recent apel relevant, identifică o obiecție
> afirmată de un stakeholder cumpărător — nu ipotetică, nu negată — caută
> content care adresează exact acea obiecție printr-o mapare curatoriată, nu
> inventată pe loc, exclude tot ce cumpărătorul a văzut deja, și clasează ce
> rămâne. Fiecare cuvânt din răspuns citează un Claim real, servit efectiv în
> context. Dacă o citare nu poate fi verificată mecanic împotriva textului
> sursă, rezumatul întreg e respins — nu trimis parțial halucinat."

---

## SCENA 5 — Ce face acest sistem *seller-ready* (2:20–3:10)

**VIZUAL:** Montaj rapid de patru ecrane: dashboard de conflicte
("2 Claims contradictorii coexistă"), digest proactiv Slack ("deal blocat de
14 zile, fără follow-up"), harta buying committee ("single-threaded — un
singur contact pe acest deal"), și un grafic cross-deal ("top 3 obiecții în
pipeline-ul tău").

**VOICEOVER:**
> "Nu e doar un motor de întrebare-răspuns. Detectează conflicte între
> afirmații care coexistă fără să fie tăcut ignorate. Trimite semnale
> proactive — deal-uri single-threaded, content trimis dar nedeschis,
> conflicte nerezolvate — direct în Slack, fără să aștepți să întrebi. Și
> agregă peste tot pipeline-ul unui vânzător: care sunt obiecțiile care chiar
> blochează trimestrul, nu doar un deal izolat."

---

## SCENA 6 — Rigoare sub capotă (3:10–3:40)

**VIZUAL:** Cod care scrolează scurt — `tenant_query()`, un test cu două
workspace-uri cu nume identice, apoi verde: "PASS — cross-tenant isolation".

**VOICEOVER:**
> "Fiecare interogare e izolată pe tenant la nivel structural — nu prin
> convenție, ci printr-un wrapper care respinge orice Cypher ce nu
> scopează explicit fiecare nod potrivit. Ingestia e idempotentă. Sursele
> corectate sau șterse se reconciliază explicit, nu se acumulează ca gunoi.
> Și niciun LLM nu scrie direct în graf, nu rezolvă identități, nu scorează
> — extrage doar date tipizate, sub validare strictă."

---

## SCENA 7 — Închidere (3:40–4:00)

**VIZUAL:** Revenire la graful animat, care se micșorează încet într-un
singur nod luminos central, apoi text final pe fundal negru:
**"Sales Context Graph — evidence, not guesswork."**

**VOICEOVER:**
> "Nu construim încă un asistent general de vânzări. Construim, mai întâi,
> fundația de care orice asistent are nevoie ca să fie de încredere:
> identitate curată, proveniență completă, izolare de tenant reală, și
> context care nu depășește niciodată ce poate demonstra. Restul se
> construiește deasupra — sigur."

**[FADE OUT — logo / link demo]**

---

## Note de producție

- **Ton vocal:** calm, încrezător, fără hype artificial — pauzele contează
  la fel de mult ca replicile.
- **Muzică:** ambient minimalist, fără crescendo dramatic — lasă graful
  animat să facă treaba vizuală.
- **Durată totală țintă:** 3:40–4:10.
- **Sursă de adevăr pentru afirmații:** [`docs/architecture.md`](architecture.md),
  [`docs/entity-resolution.md`](entity-resolution.md), [`README.md`](../README.md).
  Orice cifră adăugată ulterior (teste, latențe) trebuie verificată live în
  `docs/evaluation.md` înainte de a intra în script — nu inventată pentru efect.
