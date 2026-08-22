// ════════════════════════════════════════════════════════════
// TALANTUL ÎN NEGOȚ — Ediția 2027 — 1 Samuel 1-2
// Extras din data/1 Samuel 1-2.pdf, cheie din data/1 Samuel 1-2 barem.pdf
// ════════════════════════════════════════════════════════════

const TEST_1SAMUEL = {
  title: "TALANTUL ÎN NEGOȚ — Ediția 2027",
  subtitle: "1 Samuel 1-2",
  sections: [
    {
      id: "I",
      type: "af", // Adevărat / Fals
      instructions: "Marcați răspunsul corect pe foaia cu răspunsuri, A (adevărat) sau F (fals):",
      points: 2,
      items: [
        { text: "Ana a răspuns: „Nu, domnul meu, eu sunt o femeie care suferă în inima ei și n-am băut nici vin, nici băutură amețitoare, ci îmi vărsam sufletul înaintea Domnului.", ref: "1 Samuel 1:15", correct: "A" },
        { text: "Vrăjmașii Domnul vor tremura, din înălțimea cerului El Își va arunca tunetul asupra lor; Domnul va judeca marginile pământului.", ref: "1 Samuel 2:10", correct: "F" },
        { text: "Tinerii aceștia se făceau vinovați înaintea Domnului de un foarte mare păcat, pentru că nesocoteau darurile Domnului.", ref: "1 Samuel 2:17", correct: "A" },
        { text: "Ori de câte ori se suia Elcana la Casa Domnului, Penina o înțepa la fel.", ref: "1 Samuel 1:7", correct: "F" },
        { text: "Ana s-a sculat după ce au mâncat și au băut ei la Silo.", ref: "1 Samuel 1:9", correct: "A" },
        { text: "Ana era foarte bătrână și a aflat cum se purtau fiii lui cu tot Israelul; a aflat și că se culcau cu femeile care slujeau afară, la ușa Cortului Întâlnirii.", ref: "1 Samuel 2:22", correct: "F" },
        { text: "Penina avea copii, dar Ana n-avea.", ref: "1 Samuel 1:2", correct: "A" },
        { text: "Ana făcea slujba înaintea Domnului; și copilul acesta era îmbrăcat cu un efod de in.", ref: "1 Samuel 2:18", correct: "F" },
        { text: "Bărbatul său, Elcana, s-a suit apoi cu toată casa lui să-I aducă Domnului jertfa de peste an și să-și împlinească juruința.", ref: "1 Samuel 1:21", correct: "A" },
        { text: "Ana, bărbatul ei, i-a zis: „Fă ce vei crede; așteaptă până-l vei înțărca!", ref: "1 Samuel 1:23", correct: "F" }
      ]
    },
    {
      id: "II",
      type: "single", // o singură literă corectă din a/b/c
      instructions: "Marcați litera corespunzătoare răspunsului corect pe foaia cu răspunsuri (doar un răspuns corect):",
      points: 4,
      items: [
        { text: "Era un om din Ramataim-Țofim, din muntele lui:", ref: "1 Samuel 1:1", options: ["Israel", "Silo", "Efraim"], correct: "C" },
        { text: "Vei vedea un potrivnic al tău în Locașul Meu, în timp ce:", ref: "1 Samuel 2:32", options: ["Israel", "Efraim", "Silo"], correct: "A" },
        { text: "Fiindcă ea stătea multă vreme în rugăciune înaintea:", ref: "1 Samuel 1:12", options: ["Domnului", "Efraim", "Dumnezeul"], correct: "A" },
        { text: "De aceea vreau să I-l dau Domnului: toată viața lui să-I fie dat Domnului.\" Și s-au închinat acolo înaintea:", ref: "1 Samuel 1:28", options: ["Efraim", "Domnului", "Dumnezeul"], correct: "B" },
        { text: "Potrivnica ei o înțepa deseori ca s-o facă să se mânie, pentru că:", ref: "1 Samuel 1:6", options: ["Domnul", "Efraim", "Dumnezeul"], correct: "A" },
        { text: "Și cum se face că tu îi cinstești pe fiii tăi mai mult decât pe Mine, ca să vă îngrășați din cele dintâi roade luate din toate darurile poporului Meu:", ref: "1 Samuel 2:29", options: ["Efraim", "Israel", "Silo"], correct: "B" },
        { text: "Dar Anei îi dădea o parte îndoită, căci o iubea pe:", ref: "1 Samuel 1:5", options: ["Elcana", "Ana", "Eli"], correct: "B" },
        { text: "Dacă un om păcătuiește împotriva altui om, îl va judeca:", ref: "1 Samuel 2:25", options: ["Ana", "Efraim", "Dumnezeu"], correct: "C" },
        { text: "Omul acesta se suia în fiecare an din cetatea sa la:", ref: "1 Samuel 1:3", options: ["Efraim", "Israel", "Silo"], correct: "C" },
        { text: "Ea a făcut o juruință și a zis: „Doamne:", ref: "1 Samuel 1:11", options: ["Dumnezeul", "Domnul", "Efraim"], correct: "A" }
      ]
    },
    {
      id: "III",
      type: "match", // asociere: fiecare din stânga primește o literă din dreapta
      instructions: "Faceți asocierea și marcați litera corespunzătoare pe foaia cu răspunsuri:",
      points: 2,
      left: [
        { label: "Ana", ref: "1 Samuel 1:27", correct: "E" },
        { label: "Samuel", ref: "1 Samuel 2:14", correct: "D" },
        { label: "Eli", ref: "1 Samuel 2:12", correct: "A" },
        { label: "Israel", ref: "1 Samuel 2:26", correct: "B" },
        { label: "Domnul", ref: "1 Samuel 1:10", correct: "C" }
      ],
      right: [
        { letter: "A", text: "„erau niște oameni răi”" },
        { letter: "B", text: "„care veneau la Silo”" },
        { letter: "C", text: "„a ascultat rugăciunea pe care I-o făceam”" },
        { letter: "D", text: "„creștea mereu și era plăcut Domnului și oamenilor”" },
        { letter: "E", text: "„se ruga Domnului cu sufletul amărât și plângea”" }
      ]
    },
    {
      id: "IV",
      type: "multi", // poate fi unul, două, trei sau niciun răspuns corect
      instructions: "Marcați litera corespunzătoare răspunsului corect pe foaia cu răspunsuri (poate fi unul, două, trei sau niciun răspuns corect):",
      points: 5,
      items: [
        { text: "Când l-a înțărcat, l-a suit cu ea și:", ref: "1 Samuel 1:24", options: ["o efă de făină", "a luat trei tauri", "un burduf cu vin"], correct: ["B"] },
        { text: "Domnul sărăcește și El îmbogățește:", ref: "1 Samuel 2:7", options: ["El smerește", "El înalță", "El scoate de acolo"], correct: ["A", "B"] },
        { text: "Nu mai vorbiți cu atâta îngâmfare, să nu vă mai iasă din gură cuvinte de mândrie, căci Domnul:", ref: "1 Samuel 2:3", options: ["a luat trei tauri", "s-a culcat cu nevasta sa Ana", "este un Dumnezeu care știe totul"], correct: ["C"] }
      ]
    }
  ]
};

if (typeof module !== "undefined") module.exports = TEST_1SAMUEL;
