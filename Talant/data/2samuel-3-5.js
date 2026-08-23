// ════════════════════════════════════════════════════════════
// TALANTUL ÎN NEGOȚ — Ediția 2027 — 2 Samuel 3-5
// Extras din data/2 Samuel 3-5.pdf, cheie din data/2 Samuel 3-5 barem.pdf
// ════════════════════════════════════════════════════════════

const TEST_2SAMUEL_3_5 = {
  title: "TALANTUL ÎN NEGOȚ — Ediția 2027",
  subtitle: "2 Samuel 3-5",
  sections: [
    {
      id: "I",
      type: "af",
      instructions: "Marcați răspunsul corect pe foaia cu răspunsuri, A (adevărat) sau F (fals):",
      points: 2,
      items: [
        { text: "Saul a făcut cum îi poruncise Domnul și i-a bătut pe filisteni de la Gheba până la Ghezer.", ref: "2 Samuel 5:25", correct: "F" },
        { text: "Împăratul a mers cu oamenii lui asupra Ierusalimului, împotriva iebusiților, locuitorii țării.", ref: "2 Samuel 5:6", correct: "A" },
        { text: "David ajungea tot mai mare, și Domnul, Dumnezeul Oștirilor, era cu el.", ref: "2 Samuel 5:10", correct: "A" },
        { text: "Saul era în vârstă de treizeci de ani când s-a făcut împărat și a domnit patruzeci de ani.", ref: "2 Samuel 5:4", correct: "F" },
        { text: "Israel au aflat că David fusese uns împărat peste Israel și s-au suit toți să-l caute.", ref: "2 Samuel 5:17", correct: "F" },
        { text: "Hiram, împăratul Tirului, i-a trimis soli lui David și lemn de cedru și tâmplari și cioplitori de piatră, care i-au zidit o casă lui Saul.", ref: "2 Samuel 5:11", correct: "F" },
        { text: "Aceștia i s-au născut lui David la Hebron.", ref: "2 Samuel 3:5", correct: "A" },
        { text: "Și Domnul a zis: „Să nu te sui, ci ia-i pe la spate și mergi asupra lor în dreptul duzilor!", ref: "2 Samuel 5:23", correct: "A" },
        { text: "Dar David a pus mâna pe cetățuia Sionului: aceasta este cetatea lui David.", ref: "2 Samuel 5:7", correct: "A" },
        { text: "Împăratul a făcut următoarea cântare de jale pentru Abner și a zis: „Să moară Saul cum moare un mișel?", ref: "2 Samuel 3:33", correct: "F" }
      ]
    },
    {
      id: "II",
      type: "single",
      instructions: "Marcați litera corespunzătoare răspunsului corect pe foaia cu răspunsuri (doar un răspuns corect):",
      points: 4,
      items: [
        { text: "Între casa cui și casa lui David a ținut mult războiul?", ref: "2 Samuel 3:1", options: ["Saul", "Elcana", "Ana"], correct: "A" },
        { text: "Cine și oamenii lui i-au luat?", ref: "2 Samuel 5:21", options: ["David", "Ana", "Elcana"], correct: "A" },
        { text: "Cine le-a poruncit oamenilor lui să-i omoare, să le taie mâinile și picioarele și să-i spânzure la marginea iazului din Hebron?", ref: "2 Samuel 4:12", options: ["Ana", "Elcana", "David"], correct: "C" },
        { text: "Cine, după ce a plecat de la David, a trimis pe urmele lui Abner niște soli, care l-au adus înapoi de la fântâna fără apă Sira, fără ca David să știe ceva?", ref: "2 Samuel 3:26", options: ["Elcana", "Ana", "Ioab"], correct: "C" },
        { text: "Cine le-a răspuns lui Recab și lui Baana?", ref: "2 Samuel 4:9", options: ["David", "Ana", "Elcana"], correct: "A" },
        { text: "Al cui fiu avea doi capi peste cetele de război, unul numit Baana și altul Recab, fiii lui Rimon din Beerot, dintre fiii lui Beniamin?", ref: "2 Samuel 4:2", options: ["Ana", "Saul", "Elcana"], correct: "B" },
        { text: "Unde i s-au născut lui David cei numiți Șamua, Șobab, Natan și Solomon?", ref: "2 Samuel 5:14", options: ["Efraim", "Ierusalim", "Silo"], correct: "B" },
        { text: "Și l-au uns pe David împărat peste:", ref: "2 Samuel 5:3", options: ["Silo", "Efraim", "Israel"], correct: "C" },
        { text: "Cine s-au suit din nou și s-au răspândit în valea Refaim?", ref: "2 Samuel 5:22", options: ["Filistenii", "Silo", "Efraim"], correct: "A" },
        { text: "Cine era fiul lui Ner despre care tot poporul și tot Israelul au înțeles, în ziua aceea, că nu fusese ucis din porunca împăratului?", ref: "2 Samuel 3:37", options: ["Elcana", "Abner", "Ana"], correct: "B" }
      ]
    },
    {
      id: "III",
      type: "match",
      instructions: "Faceți asocierea și marcați litera corespunzătoare pe foaia cu răspunsuri:",
      points: 2,
      left: [
        { label: "David", ref: "2 Samuel 3:14", correct: "B" },
        { label: "Saul", ref: "2 Samuel 3:7", correct: "D" },
        { label: "Filistenii", ref: "2 Samuel 5:18", correct: "A" },
        { label: "Ioab", ref: "2 Samuel 3:30", correct: "C" },
        { label: "Domnul", ref: "2 Samuel 5:24", correct: "E" }
      ],
      right: [
        { letter: "A", text: "„au venit și s-au răspândit în valea Refaim”" },
        { letter: "B", text: "„i-a trimis soli lui Iș-Boșet”" },
        { letter: "C", text: "„și fratele său”" },
        { letter: "D", text: "„avusese o țiitoare”" },
        { letter: "E", text: "„merge înaintea ta”" }
      ]
    },
    {
      id: "IV",
      type: "multi",
      instructions: "Marcați litera corespunzătoare răspunsului corect pe foaia cu răspunsuri (poate fi unul, două, trei sau niciun răspuns corect):",
      points: 5,
      items: [
        { text: "Abner nu mai era la David, în Hebron, căci David:", ref: "2 Samuel 3:22", options: ["îi dăduse drumul", "casa lui Saul", "plecase în pace"], correct: ["A", "C"] },
        { text: "Toate semințiile lui Israel au venit la David, în Hebron, și au zis: „Iată că noi suntem:", ref: "2 Samuel 5:1", options: ["carne din carnea ta", "os din oasele tale", "tot poporul a plâns"], correct: ["A", "B"] },
        { text: "Tot poporul s-a apropiat de David ca să-l facă să mănânce ceva cât era încă ziuă, dar David a jurat zicând: „Să mă pedepsească Dumnezeu cu toată:", ref: "2 Samuel 3:35", options: ["altceva înainte de apusul soarelui", "os din oasele tale", "asprimea dacă voi gusta pâine"], correct: ["A", "C"] }
      ]
    }
  ]
};

if (typeof module !== "undefined") module.exports = TEST_2SAMUEL_3_5;
