const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";

// ── Palette ────────────────────────────────────────────────────────────────
const C = {
  navy:    "0D1B2A",
  teal:    "00A896",
  tealDim: "028090",
  white:   "FFFFFF",
  offWhite:"F0F4F8",
  gray:    "8899A6",
  red:     "E63946",
  gold:    "F4A261",
};

// ── Helpers ────────────────────────────────────────────────────────────────
function titleSlide(slide) {
  slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: "100%", h: "100%", fill: { color: C.navy } });
}
function contentSlide(slide) {
  slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: "100%", h: "100%", fill: { color: C.offWhite } });
}
function addTag(slide, text, x, y, color) {
  slide.addShape(pres.ShapeType.roundRect, { x, y, w: 1.8, h: 0.32, fill: { color: color || C.teal }, rectRadius: 0.05 });
  slide.addText(text, { x, y, w: 1.8, h: 0.32, fontSize: 10, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 1 — Title
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleSlide(s);

  // Accent bar left
  s.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 0.08, h: "100%", fill: { color: C.teal } });

  s.addText("GraphRAG Knowledge Platform", {
    x: 0.5, y: 1.2, w: 9, h: 0.9,
    fontSize: 40, bold: true, color: C.white, fontFace: "Calibri",
  });
  s.addText("Contradiction Detection · PageRank · Multi-Hop Reasoning", {
    x: 0.5, y: 2.2, w: 9, h: 0.5,
    fontSize: 18, color: C.teal, fontFace: "Calibri",
  });
  s.addText("Built for AdTech & MarTech at enterprise scale", {
    x: 0.5, y: 2.85, w: 9, h: 0.4,
    fontSize: 14, color: C.gray, fontFace: "Calibri",
  });

  // Bottom row tags
  addTag(s, "Neo4j + GDS", 0.5, 4.6);
  addTag(s, "Python / FastAPI", 2.45, 4.6);
  addTag(s, "380 Tests", 4.4, 4.6);
  addTag(s, "Live Demo", 6.35, 4.6);

  s.addNotes(`Welcome. I'm Sergiu.
I built an enterprise GraphRAG platform that answers the core question WPP Open faces every day:
when your AI has 30, or 300, or 3,000 documents — contracts, brand guidelines, privacy policies, campaign briefs —
how do you make sure it doesn't give an answer that contradicts a binding obligation?

Today I'll show you the architecture, a live AdTech contradiction example, and the graph algorithms underneath.`);
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 2 — The Problem
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  contentSlide(s);

  s.addText("The Problem Vector Search Can't Solve", {
    x: 0.5, y: 0.35, w: 9, h: 0.65,
    fontSize: 30, bold: true, color: C.navy, fontFace: "Calibri",
  });

  const problems = [
    ["A campaign brief approves a placement", "The SOW signed 3 months ago strictly excludes it"],
    ["A brand guideline allows adjacency expansion", "The data privacy policy prohibits the same inference on a separate legal basis"],
    ["Retrieval returns the most similar chunk", "It never checks if that chunk contradicts a higher-authority document"],
  ];

  problems.forEach(([left, right], i) => {
    const y = 1.2 + i * 1.1;
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 4.0, h: 0.75, fill: { color: C.teal }, shadow: { type: "outer", color: "888888", blur: 4, offset: 2, angle: 45 } });
    s.addText(left, { x: 0.5, y, w: 4.0, h: 0.75, fontSize: 13, color: C.white, bold: true, align: "center", valign: "middle", margin: 8 });
    s.addShape(pres.ShapeType.rect, { x: 5.5, y, w: 4.0, h: 0.75, fill: { color: C.red }, shadow: { type: "outer", color: "888888", blur: 4, offset: 2, angle: 45 } });
    s.addText(right, { x: 5.5, y, w: 4.0, h: 0.75, fontSize: 13, color: C.white, bold: true, align: "center", valign: "middle", margin: 8 });
    s.addText("vs", { x: 4.6, y: y + 0.2, w: 0.8, h: 0.35, fontSize: 16, bold: true, color: C.navy, align: "center" });
  });

  s.addText("Vector search ranks by similarity. It doesn't know who outranks whom.", {
    x: 0.5, y: 4.65, w: 9, h: 0.35,
    fontSize: 13, italic: true, color: C.gray, align: "center",
  });

  s.addNotes(`This is the problem.
In AdTech, you have documents with conflicting authority. A campaign brief approved by a regional director can violate a contract signed by the VP of Global Marketing.
Standard RAG retrieves the most similar chunk. It has no concept of which document outranks which.
Our platform solves this with a graph layer that encodes authority relationships and detects contradictions before they reach the LLM.`);
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 3 — WPP AdTech Demo: The 4-Document Chain
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  contentSlide(s);

  s.addText("Nova Beverages — 4-Document Authority Chain", {
    x: 0.5, y: 0.35, w: 9, h: 0.65,
    fontSize: 28, bold: true, color: C.navy, fontFace: "Calibri",
  });

  const docs = [
    { label: "SOW", sub: "Binding contract\nExcludes gambling/\nsports-betting", color: C.navy, auth: "Authority 1" },
    { label: "DPP", sub: "Data Privacy Policy\nProhibits gambling-\nadjacent inference", color: C.tealDim, auth: "Authority 2" },
    { label: "Brand\nGuideline", sub: "Global creative\nstandards. Defers\nto SOW.", color: "5E6D7A", auth: "Authority 3" },
    { label: "Campaign\nBrief", sub: "EU Q3 SummerRush\nApproves sports-\nbetting adjacency", color: C.red, auth: "Authority 4 ⚠" },
  ];

  docs.forEach((d, i) => {
    const x = 0.5 + i * 2.35;
    s.addShape(pres.ShapeType.rect, { x, y: 1.1, w: 2.1, h: 1.8, fill: { color: d.color }, shadow: { type: "outer", color: "999999", blur: 5, offset: 3, angle: 45 } });
    s.addText(d.label, { x, y: 1.1, w: 2.1, h: 0.55, fontSize: 15, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
    s.addText(d.sub, { x, y: 1.65, w: 2.1, h: 1.0, fontSize: 11, color: C.white, align: "center", valign: "top", margin: 6 });
    s.addShape(pres.ShapeType.rect, { x, y: 2.7, w: 2.1, h: 0.2, fill: { color: C.gold } });
    s.addText(d.auth, { x, y: 2.7, w: 2.1, h: 0.2, fontSize: 9, bold: true, color: C.navy, align: "center", valign: "middle", margin: 0 });
    if (i < 3) {
      s.addText("→", { x: x + 2.1, y: 1.85, w: 0.25, h: 0.4, fontSize: 20, bold: true, color: C.teal, align: "center" });
    }
  });

  s.addText("GOVERNS / SUPERSEDES", {
    x: 0.5, y: 3.1, w: 9.0, h: 0.3,
    fontSize: 11, italic: true, color: C.gray, align: "center",
  });

  // Contradiction callout
  s.addShape(pres.ShapeType.roundRect, { x: 0.5, y: 3.55, w: 9.0, h: 0.8, fill: { color: "FFF3CD" }, rectRadius: 0.06 });
  s.addText("⚠  Campaign Brief approves sports-betting adjacency — contradicting both SOW Section 2 (contract breach) and DPP Section 3 (data privacy violation)", {
    x: 0.65, y: 3.6, w: 8.7, h: 0.7,
    fontSize: 12, color: "856404", bold: false,
  });

  s.addNotes(`This is a real AdTech scenario I built to demonstrate the platform.
Nova Beverages has four documents. At the top: the Statement of Work, signed by the VP of Global Marketing.
It explicitly excludes gambling and sports-betting placements. No exceptions.
The Data Privacy Policy independently prohibits gambling-adjacent behavioral inference — on a separate legal basis.
The Brand Guideline allows some flexibility, but explicitly defers to the SOW.
The Campaign Brief — approved by an EU Desk Regional Director — includes sports-betting companion apps as a targeting vertical.
That's two independent violations. Neither the Brief author nor a standard RAG system would surface this.
Our graph does.`);
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 4 — Contradiction Detection
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleSlide(s);
  s.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 0.08, h: "100%", fill: { color: C.teal } });

  s.addText("Contradiction Detected", {
    x: 0.5, y: 0.4, w: 9, h: 0.65,
    fontSize: 32, bold: true, color: C.white, fontFace: "Calibri",
  });

  // C01
  s.addShape(pres.ShapeType.rect, { x: 0.5, y: 1.15, w: 4.2, h: 1.6, fill: { color: "112233" } });
  s.addText("C01 — Contract Breach", { x: 0.5, y: 1.15, w: 4.2, h: 0.38, fontSize: 13, bold: true, color: C.gold, align: "center", valign: "middle", margin: 0 });
  s.addText("SOW §2\nPROHIBITS\nsports-betting\nplacements", { x: 0.5, y: 1.55, w: 1.8, h: 1.1, fontSize: 11, color: C.white, align: "center", valign: "middle", margin: 4 });
  s.addText("✗\nvs", { x: 2.35, y: 1.65, w: 0.6, h: 0.9, fontSize: 18, bold: true, color: C.red, align: "center" });
  s.addText("Campaign Brief §2\nPERMITS\nsports-betting\nadjacency", { x: 3.0, y: 1.55, w: 1.7, h: 1.1, fontSize: 11, color: C.white, align: "center", valign: "middle", margin: 4 });

  s.addText("Winner: SOW (§4 authority clause — prevails over any Campaign Brief)", {
    x: 0.5, y: 2.78, w: 4.2, h: 0.3,
    fontSize: 9, color: C.teal, italic: true, align: "center",
  });

  // C02
  s.addShape(pres.ShapeType.rect, { x: 5.3, y: 1.15, w: 4.2, h: 1.6, fill: { color: "112233" } });
  s.addText("C02 — Data Privacy Violation", { x: 5.3, y: 1.15, w: 4.2, h: 0.38, fontSize: 13, bold: true, color: C.gold, align: "center", valign: "middle", margin: 0 });
  s.addText("DPP §3\nPROHIBITS\ngambling-adjacent\nbehavioral inference", { x: 5.3, y: 1.55, w: 1.9, h: 1.1, fontSize: 11, color: C.white, align: "center", valign: "middle", margin: 4 });
  s.addText("✗\nvs", { x: 7.25, y: 1.65, w: 0.6, h: 0.9, fontSize: 18, bold: true, color: C.red, align: "center" });
  s.addText("Campaign Brief §3\nIMPLIES same\ninference via\nbetting adjacency", { x: 7.9, y: 1.55, w: 1.6, h: 1.1, fontSize: 11, color: C.white, align: "center", valign: "middle", margin: 4 });

  s.addText("Winner: DPP (§4 legally binding — supersedes any campaign-level approval)", {
    x: 5.3, y: 2.78, w: 4.2, h: 0.3,
    fontSize: 9, color: C.teal, italic: true, align: "center",
  });

  // Graph path
  s.addText("Graph path:", { x: 0.5, y: 3.2, w: 1.2, h: 0.3, fontSize: 11, bold: true, color: C.gray });
  s.addText("SOW  ──[PROHIBITS]──▶  sports-betting-placements  ◀──[PERMITS]──  CampaignBrief", {
    x: 1.7, y: 3.2, w: 7.8, h: 0.3,
    fontSize: 11, color: C.teal, fontFace: "Courier New",
  });

  s.addText("Contradiction nodes stored as (:Conflict) with HAS_CONFLICT edges — traversable, auditable, never silent.", {
    x: 0.5, y: 3.65, w: 9.0, h: 0.35,
    fontSize: 12, italic: true, color: C.gray,
  });

  s.addNotes(`This is what the platform surfaces.
Two contradiction nodes. Two separate legal bases. The graph encodes the PROHIBITS and PERMITS edges on the same target entity.
Authority resolution is built in: the SOW's Section 4 declares it the binding authority. The DPP's Section 4 declares it legally superseding.
The Campaign Brief had no valid path to approval — neither the EU Desk director nor the Brand Guideline flexibility clause could override these.
In the graph, these are stored as Conflict nodes with HAS_CONFLICT edges. They're traversable, auditable, and surfaced in every query that touches related entities.`);
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 5 — 6-Stage Retrieval Pipeline
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  contentSlide(s);

  s.addText("6-Stage Retrieval Pipeline", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 30, bold: true, color: C.navy, fontFace: "Calibri",
  });

  const stages = [
    { n: "1", label: "Vector ANN", sub: "3072d embeddings\ncosine similarity", color: C.tealDim },
    { n: "2", label: "BM25", sub: "Keyword exact match\nregulation codes", color: C.tealDim },
    { n: "3", label: "RRF Fusion", sub: "Reciprocal Rank\nFusion merge", color: "3A7CA5" },
    { n: "4", label: "Rerank", sub: "Cross-encoder\nprecision filter", color: "3A7CA5" },
    { n: "5", label: "Graph Hop", sub: "Multi-hop traversal\nIRCoT trigger", color: C.navy },
    { n: "6", label: "GNN + PR", sub: "Query-conditioned\nPageRank scoring", color: C.navy },
  ];

  stages.forEach((st, i) => {
    const x = 0.18 + i * 1.62;
    s.addShape(pres.ShapeType.rect, { x, y: 1.05, w: 1.45, h: 1.55, fill: { color: st.color }, shadow: { type: "outer", color: "AAAAAA", blur: 4, offset: 2, angle: 45 } });
    s.addText(st.n, { x, y: 1.05, w: 1.45, h: 0.4, fontSize: 22, bold: true, color: C.gold, align: "center", valign: "middle", margin: 0 });
    s.addText(st.label, { x, y: 1.45, w: 1.45, h: 0.38, fontSize: 13, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
    s.addText(st.sub, { x, y: 1.83, w: 1.45, h: 0.7, fontSize: 10, color: "CCDDEE", align: "center", valign: "top", margin: 4 });
    if (i < 5) s.addText("→", { x: x + 1.45, y: 1.6, w: 0.17, h: 0.4, fontSize: 14, bold: true, color: C.teal, align: "center" });
  });

  s.addShape(pres.ShapeType.rect, { x: 0.18, y: 2.78, w: 9.6, h: 0.5, fill: { color: C.navy } });
  s.addText("▼  LLM Synthesis  (Groq / DeepSeek)  →  Answer + Citations + Conflict Warnings", {
    x: 0.18, y: 2.78, w: 9.6, h: 0.5,
    fontSize: 13, bold: true, color: C.white, align: "center", valign: "middle", margin: 0,
  });

  // Stats row
  const stats = [["< 5s", "End-to-end latency"], ["0.95", "RAGAS Faithfulness"], ["380", "Passing tests"], ["2", "Tenants isolated"]];
  stats.forEach(([val, lbl], i) => {
    const x = 0.5 + i * 2.4;
    s.addText(val, { x, y: 3.55, w: 2.1, h: 0.55, fontSize: 32, bold: true, color: C.teal, align: "center" });
    s.addText(lbl, { x, y: 4.1, w: 2.1, h: 0.3, fontSize: 11, color: C.gray, align: "center" });
  });

  s.addNotes(`The retrieval pipeline has six stages, each solving a problem the previous stage can't.
Vector ANN finds semantically similar chunks. BM25 catches exact terms — regulation codes, contract references — that get diluted in embedding space.
RRF merges the two ranked lists without needing score normalization.
The cross-encoder reranker applies a heavier model to the top candidates for precision.
Then multi-hop graph traversal: if the answer requires crossing documents — contract references policy, policy applies to supplier — the IRCoT trigger fires and follows the edges.
Finally, GNN and PageRank scores rank entities by both query-conditioned relevance and global importance.
Result: under 5 seconds, RAGAS faithfulness of 0.95.`);
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 6 — PageRank + Community Detection
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  contentSlide(s);

  s.addText("Graph Algorithms: PageRank + Community Detection", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 26, bold: true, color: C.navy, fontFace: "Calibri",
  });

  // PageRank box
  s.addShape(pres.ShapeType.rect, { x: 0.5, y: 1.05, w: 4.3, h: 3.25, fill: { color: C.navy } });
  s.addText("PageRank", { x: 0.5, y: 1.05, w: 4.3, h: 0.45, fontSize: 18, bold: true, color: C.teal, align: "center", valign: "middle", margin: 0 });
  s.addText("Global static importance\n(query-independent)", { x: 0.5, y: 1.5, w: 4.3, h: 0.45, fontSize: 12, color: C.gray, align: "center", valign: "middle", margin: 0 });

  const prItems = [
    "GDS gds.pageRank.stream",
    "Weighted by confidence edge property",
    "Tenant-isolated graph projection",
    "Projection dropped in finally block",
    "Persisted on Entity.pagerank",
    "POST /kg/pagerank/compute",
    "GET /kg/pagerank/top-entities",
  ];
  prItems.forEach((item, i) => {
    s.addText("• " + item, { x: 0.65, y: 2.05 + i * 0.28, w: 4.0, h: 0.26, fontSize: 11, color: C.white });
  });

  // Community Detection box
  s.addShape(pres.ShapeType.rect, { x: 5.2, y: 1.05, w: 4.3, h: 3.25, fill: { color: C.navy } });
  s.addText("Community Detection", { x: 5.2, y: 1.05, w: 4.3, h: 0.45, fontSize: 18, bold: true, color: C.teal, align: "center", valign: "middle", margin: 0 });
  s.addText("Hierarchical clustering of related entities", { x: 5.2, y: 1.5, w: 4.3, h: 0.45, fontSize: 12, color: C.gray, align: "center", valign: "middle", margin: 0 });

  const cdItems = [
    "Leiden (graspologic) — primary",
    "Multi-resolution Louvain fallback",
    "Same resolution schedule as Leiden",
    "3 levels: 1.0×, 0.5×, 0.25× resolution",
    "Connected components — last resort",
    "Community names via LLM labeling",
    "WPP demo: 4 communities = 4 docs",
  ];
  cdItems.forEach((item, i) => {
    s.addText("• " + item, { x: 5.35, y: 2.05 + i * 0.28, w: 4.0, h: 0.26, fontSize: 11, color: C.white });
  });

  s.addNotes(`Two graph algorithms, two different problems.
PageRank gives you global static importance — which entities are most referenced across the entire knowledge base. This is query-independent. The SOW, the CSR document, the top procedure — these rank high before you even ask a question.
It runs natively via GDS, tenant-isolated, weighted by the confidence property on each relationship, always dropped in a finally block to prevent projection leaks.
Community detection clusters related entities into semantic groups. Leiden is the primary algorithm — state of the art. When graspologic isn't available, we fall back to multi-resolution Louvain with the same resolution schedule, so the hierarchy looks the same regardless of which algorithm ran.
In the WPP demo, the four communities map exactly to the four documents — exactly what you'd want.`);
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 7 — Tech Stack & Architecture
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  contentSlide(s);

  s.addText("Tech Stack", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 30, bold: true, color: C.navy, fontFace: "Calibri",
  });

  const layers = [
    { label: "API Layer", items: "FastAPI · async · tenant-scoped · require_scope auth", color: C.tealDim },
    { label: "Graph Layer", items: "Neo4j 5.20 + GDS 2.6.9 + APOC · Cypher · Vector Index (3072d)", color: C.navy },
    { label: "Intelligence Layer", items: "OpenAI embeddings · Groq/DeepSeek LLM · Cross-encoder rerank · GNN scorer", color: "3A7CA5" },
    { label: "Algorithm Layer", items: "PageRank (GDS) · Leiden (graspologic) · OWL-RL reasoning · IRCoT traversal", color: "1B4F72" },
    { label: "Eval Layer", items: "RAGAS LLM-as-judge · Deterministic gates · 380 tests · 2 tenants", color: "17202A" },
  ];

  layers.forEach((layer, i) => {
    const y = 1.05 + i * 0.72;
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 2.0, h: 0.6, fill: { color: layer.color } });
    s.addText(layer.label, { x: 0.5, y, w: 2.0, h: 0.6, fontSize: 12, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
    s.addShape(pres.ShapeType.rect, { x: 2.6, y, w: 7.0, h: 0.6, fill: { color: "E8EEF4" } });
    s.addText(layer.items, { x: 2.7, y, w: 6.8, h: 0.6, fontSize: 12, color: C.navy, valign: "middle", margin: 4 });
  });

  s.addNotes(`The full stack.
API layer: FastAPI, fully async, every endpoint tenant-scoped. Authorization via require_scope — read vs write operations separated.
Graph layer: Neo4j 5.20 with GDS 2.6.9 and APOC. Native vector index at 3072 dimensions.
Intelligence layer: OpenAI text-embedding-3-large for embeddings, Groq or DeepSeek as the LLM, a cross-encoder reranker, and a GNN scorer.
Algorithm layer: GDS PageRank, Leiden community detection, OWL-RL reasoning for ontology inference, and IRCoT for multi-hop traversal.
Eval layer: RAGAS with LLM-as-judge at 20% sampling, deterministic gates for expected citations and forbidden terms, 380 passing tests across automotive and aerospace tenants.`);
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 8 — Why This Matters for WPP Open
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleSlide(s);
  s.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 0.08, h: "100%", fill: { color: C.teal } });

  s.addText("Why This Matters for WPP Open", {
    x: 0.5, y: 0.35, w: 9, h: 0.65,
    fontSize: 32, bold: true, color: C.white, fontFace: "Calibri",
  });

  const points = [
    { icon: "[1]", title: "Compliance at scale", body: "Every campaign brief checked against every binding obligation - automatically. No manual cross-referencing across 20 markets." },
    { icon: "[2]", title: "Multi-hop reasoning", body: "SOW references a policy, policy applies to a market, market has a campaign. One graph traversal, one answer." },
    { icon: "[3]", title: "Tenant isolation built in", body: "Client A's knowledge graph never touches Client B's. Authority chains, contradiction nodes, and PageRank scores are fully scoped." },
    { icon: "[4]", title: "Graph algorithms on real data", body: "PageRank surfaces the most-referenced entities before a question is asked. Community detection groups related documents for structured retrieval." },
  ];

  points.forEach((p, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = 0.5 + col * 4.8;
    const y = 1.2 + row * 1.5;
    s.addShape(pres.ShapeType.rect, { x, y, w: 4.5, h: 1.3, fill: { color: "0D2137" } });
    s.addText(p.icon + "  " + p.title, { x: x + 0.15, y: y + 0.1, w: 4.2, h: 0.38, fontSize: 14, bold: true, color: C.teal, valign: "middle", margin: 0 });
    s.addText(p.body, { x: x + 0.15, y: y + 0.48, w: 4.2, h: 0.72, fontSize: 11, color: "AABBCC", valign: "top", margin: 0 });
  });

  s.addNotes(`What does this mean for WPP Open specifically?
Compliance at scale: with 2,000 professionals across 20 markets, contracts and compliance policies don't stay in sync manually. The graph catches the conflicts.
Multi-hop reasoning: a question about a campaign budget needs to traverse the SOW, the brand guideline, and the media plan. Vector search can't do that.
Tenant isolation: built in from day one. Every Neo4j query filters by tenant. Client confidentiality is structural, not a config flag.
Graph algorithms on real data: PageRank already knows your most-referenced documents before a user asks a single question. That's a retrieval quality improvement with zero query-time cost.`);
}

// ══════════════════════════════════════════════════════════════════════════
// SLIDE 9 — Live Demo & Closing
// ══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  contentSlide(s);

  s.addText("Live Demo", {
    x: 0.5, y: 0.35, w: 9, h: 0.6,
    fontSize: 30, bold: true, color: C.navy, fontFace: "Calibri",
  });

  const steps = [
    { step: "1", label: "Start stack", cmd: "docker-compose up -d\npython -m uvicorn api.main:app --port 8000" },
    { step: "2", label: "Ingest WPP corpus", cmd: "python scripts/ingest_corpus.py\n  --tenant marketing --dir data/wpp_demo" },
    { step: "3", label: "Compute PageRank", cmd: "python scripts/pagerank_compute.py\n  --tenant marketing" },
    { step: "4", label: "Ask the question", cmd: 'POST /query\n{"question": "Can we run sports-betting\nplacements in Germany?"}' },
    { step: "5", label: "Contradiction surfaced", cmd: "Response includes:\n⚠ C01 SOW breach · C02 DPP violation" },
  ];

  steps.forEach((st, i) => {
    const x = 0.5 + (i % 3) * 3.15;
    const y = i < 3 ? 1.1 : 2.8;
    s.addShape(pres.ShapeType.rect, { x, y, w: 2.95, h: 1.45, fill: { color: C.navy }, shadow: { type: "outer", color: "BBBBBB", blur: 4, offset: 2, angle: 45 } });
    s.addShape(pres.ShapeType.rect, { x, y, w: 0.5, h: 0.42, fill: { color: C.teal } });
    s.addText(st.step, { x, y, w: 0.5, h: 0.42, fontSize: 16, bold: true, color: C.white, align: "center", valign: "middle", margin: 0 });
    s.addText(st.label, { x: x + 0.55, y: y + 0.05, w: 2.35, h: 0.35, fontSize: 12, bold: true, color: C.white, valign: "middle", margin: 0 });
    s.addText(st.cmd, { x: x + 0.1, y: y + 0.5, w: 2.75, h: 0.88, fontSize: 9, color: C.teal, fontFace: "Courier New", valign: "top", margin: 4 });
  });

  s.addShape(pres.ShapeType.rect, { x: 0.5, y: 4.5, w: 9.0, h: 0.65, fill: { color: C.teal } });
  s.addText("Public access via Cloudflare Tunnel · Automotive + Aerospace tenants already ingested · 380 tests passing", {
    x: 0.5, y: 4.5, w: 9.0, h: 0.65,
    fontSize: 12, color: C.white, align: "center", valign: "middle", bold: true, margin: 0,
  });

  s.addNotes(`The full demo is live.
Five steps. Start the stack, ingest the WPP corpus as a marketing tenant, compute PageRank, send the query — and the response surfaces both contradictions with the authority resolution.
This isn't a mockup. The automotive and aerospace tenants are already ingested. The platform has 380 passing tests.
And it's accessible publicly via Cloudflare Tunnel — so I can show you this right now, not just on slides.`);
}

// ── Write ──────────────────────────────────────────────────────────────────
pres.writeFile({ fileName: "WPP_Pitch.pptx" }).then(() => {
  console.log("Written: WPP_Pitch.pptx");
}).catch(e => {
  console.error(e);
  process.exit(1);
});
