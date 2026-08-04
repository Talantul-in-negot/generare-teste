"""Minimal browser visualization for the P4.5 Context Graph.

Not part of docs/plan.md's required API surface (§11) — a debugging/demo aid
layered on top of the existing POST /api/v1/context/build and
GET /api/v1/claims/{id}/evidence endpoints. Renders Claims as a
subject --predicate--> object node-link graph directly in the browser via a
small hand-rolled force layout (no CDN dependency, consistent with this
repo's offline-reproducibility stance elsewhere).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["viz"])


@router.get("/viz", response_class=HTMLResponse)
async def context_graph_viz() -> str:
    return _PAGE


_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Context Graph Viz</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; display: flex; height: 100vh; }
  #panel { width: 320px; padding: 16px; box-sizing: border-box; border-right: 1px solid #ddd; overflow-y: auto; }
  #panel label { display: block; margin-top: 10px; font-size: 12px; color: #555; }
  #panel input { width: 100%; padding: 6px; box-sizing: border-box; margin-top: 2px; }
  #panel button { margin-top: 14px; width: 100%; padding: 8px; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer; }
  #panel button:hover { background: #1d4ed8; }
  #status { margin-top: 10px; font-size: 12px; color: #b91c1c; white-space: pre-wrap; }
  #meta { margin-top: 14px; font-size: 12px; color: #444; }
  #detail { margin-top: 14px; padding-top: 10px; border-top: 1px solid #ddd; font-size: 12px; }
  #detail h4 { margin: 0 0 6px 0; }
  #graph { flex: 1; position: relative; }
  svg { width: 100%; height: 100%; }
  .node circle { stroke: #fff; stroke-width: 1.5px; cursor: pointer; }
  .node text { font-size: 10px; pointer-events: none; }
  .edge-label { font-size: 9px; fill: #555; pointer-events: none; }
  .legend { position: absolute; bottom: 10px; left: 10px; font-size: 11px; background: rgba(255,255,255,0.9); padding: 8px; border-radius: 4px; }
  .legend div { display: flex; align-items: center; margin-bottom: 3px; }
  .legend span.swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
</style>
</head>
<body>
<div id="panel">
  <h3>Context Graph</h3>
  <label>Workspace ID
    <input id="workspaceId" value="ws-demo">
  </label>
  <label>API Key
    <input id="apiKey" type="password" placeholder="X-Api-Key">
  </label>
  <label>Subject ID (contact/account/etc.)
    <input id="subjectId" placeholder="optional">
  </label>
  <label>Conversation ID
    <input id="conversationId" placeholder="optional">
  </label>
  <label>Max nodes
    <input id="maxNodes" placeholder="default">
  </label>
  <button id="buildBtn">Build</button>
  <div id="status"></div>
  <div id="meta"></div>
  <div id="detail"></div>
</div>
<div id="graph">
  <svg id="svg"></svg>
  <div class="legend">
    <div><span class="swatch" style="background:#16a34a"></span>AFFIRMED</div>
    <div><span class="swatch" style="background:#dc2626"></span>NEGATED</div>
    <div><span class="swatch" style="background:#ca8a04"></span>HYPOTHETICAL</div>
    <div><span class="swatch" style="background:#6b7280"></span>entity node</div>
  </div>
</div>

<script>
const polarityColor = { AFFIRMED: "#16a34a", NEGATED: "#dc2626", HYPOTHETICAL: "#ca8a04" };
const entityColor = "#2563eb";
const literalColor = "#6b7280";

let nodes = [];
let edges = [];
let svgEl = document.getElementById("svg");

document.getElementById("buildBtn").addEventListener("click", build);

async function build() {
  const statusEl = document.getElementById("status");
  const metaEl = document.getElementById("meta");
  const detailEl = document.getElementById("detail");
  statusEl.textContent = "";
  metaEl.textContent = "";
  detailEl.innerHTML = "";

  const workspaceId = document.getElementById("workspaceId").value.trim();
  const apiKey = document.getElementById("apiKey").value.trim();
  const subjectId = document.getElementById("subjectId").value.trim();
  const conversationId = document.getElementById("conversationId").value.trim();
  const maxNodesRaw = document.getElementById("maxNodes").value.trim();

  if (!workspaceId) { statusEl.textContent = "Workspace ID is required."; return; }
  if (!apiKey) { statusEl.textContent = "API Key is required."; return; }

  const body = {};
  if (subjectId) body.subject_id = subjectId;
  if (conversationId) body.conversation_id = conversationId;
  if (maxNodesRaw) body.max_nodes = parseInt(maxNodesRaw, 10);

  let resp;
  try {
    resp = await fetch("/api/v1/context/build", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Workspace-Id": workspaceId, "X-Api-Key": apiKey },
      body: JSON.stringify(body),
    });
  } catch (e) {
    statusEl.textContent = "Request failed: " + e;
    return;
  }
  if (!resp.ok) {
    statusEl.textContent = "HTTP " + resp.status + ": " + (await resp.text());
    return;
  }
  const result = await resp.json();

  metaEl.innerHTML =
    "nodes_used: " + result.nodes_used + " / " + result.budget_max_nodes + "<br>" +
    "tokens_used: " + result.tokens_used + " / " + result.budget_max_tokens + "<br>" +
    "truncated: " + result.truncated + "<br>" +
    "claims: " + result.claims.length + "<br>" +
    "unresolved_mentions: " + result.unresolved_mention_ids.length + "<br>" +
    "conflicts: " + result.conflicts.length;

  buildGraph(result, workspaceId, apiKey);
}

function buildGraph(result, workspaceId, apiKey) {
  const nodeById = new Map();
  edges = [];

  function ensureNode(id, label, kind) {
    if (!nodeById.has(id)) {
      nodeById.set(id, { id, label, kind, x: Math.random() * 600 + 50, y: Math.random() * 400 + 50, vx: 0, vy: 0 });
    }
    return nodeById.get(id);
  }

  for (const claim of result.claims) {
    const subj = ensureNode(claim.subject_id, shorten(claim.subject_id), "entity");
    const objId = claim.object_id || ("lit:" + claim.claim_id);
    const objLabel = claim.object_value || shorten(claim.object_id);
    const obj = ensureNode(objId, objLabel, claim.object_id ? "entity" : "literal");
    edges.push({
      source: subj.id, target: obj.id,
      predicate: claim.predicate, polarity: claim.polarity,
      claimId: claim.claim_id, workspaceId, apiKey,
    });
  }

  nodes = Array.from(nodeById.values());
  runLayout();
  render(workspaceId);
}

function shorten(s) {
  if (!s) return "?";
  return s.length > 14 ? s.slice(0, 6) + "…" + s.slice(-4) : s;
}

function runLayout() {
  const W = svgEl.clientWidth || 800, H = svgEl.clientHeight || 600;
  const cx = W / 2, cy = H / 2;
  for (let iter = 0; iter < 300; iter++) {
    for (const a of nodes) {
      let fx = (cx - a.x) * 0.002, fy = (cy - a.y) * 0.002;
      for (const b of nodes) {
        if (a === b) continue;
        const dx = a.x - b.x, dy = a.y - b.y;
        const distSq = Math.max(dx * dx + dy * dy, 1);
        const force = 2500 / distSq;
        fx += (dx / Math.sqrt(distSq)) * force;
        fy += (dy / Math.sqrt(distSq)) * force;
      }
      a.vx = (a.vx + fx) * 0.8;
      a.vy = (a.vy + fy) * 0.8;
    }
    for (const e of edges) {
      const a = nodes.find(n => n.id === e.source), b = nodes.find(n => n.id === e.target);
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const diff = (dist - 140) * 0.02;
      const ux = dx / dist, uy = dy / dist;
      a.vx += ux * diff; a.vy += uy * diff;
      b.vx -= ux * diff; b.vy -= uy * diff;
    }
    for (const n of nodes) {
      n.x += n.vx; n.y += n.vy;
      n.x = Math.max(30, Math.min(W - 30, n.x));
      n.y = Math.max(30, Math.min(H - 30, n.y));
    }
  }
}

function render(workspaceId) {
  svgEl.innerHTML = "";
  const ns = "http://www.w3.org/2000/svg";

  for (const e of edges) {
    const a = nodes.find(n => n.id === e.source), b = nodes.find(n => n.id === e.target);
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
    line.setAttribute("stroke", polarityColor[e.polarity] || "#999");
    line.setAttribute("stroke-width", "1.5");
    line.style.cursor = "pointer";
    line.addEventListener("click", () => showEvidence(e));
    svgEl.appendChild(line);

    const label = document.createElementNS(ns, "text");
    label.setAttribute("x", (a.x + b.x) / 2);
    label.setAttribute("y", (a.y + b.y) / 2);
    label.setAttribute("class", "edge-label");
    label.textContent = e.predicate;
    svgEl.appendChild(label);
  }

  for (const n of nodes) {
    const g = document.createElementNS(ns, "g");
    g.setAttribute("class", "node");
    g.setAttribute("transform", "translate(" + n.x + "," + n.y + ")");

    const circle = document.createElementNS(ns, "circle");
    circle.setAttribute("r", n.kind === "entity" ? 8 : 5);
    circle.setAttribute("fill", n.kind === "entity" ? entityColor : literalColor);
    g.appendChild(circle);

    const text = document.createElementNS(ns, "text");
    text.setAttribute("x", 10);
    text.setAttribute("y", 4);
    text.textContent = n.label;
    g.appendChild(text);

    g.addEventListener("click", () => showNode(n));
    svgEl.appendChild(g);
  }
}

async function showEvidence(edge) {
  const detailEl = document.getElementById("detail");
  detailEl.innerHTML = "<h4>Loading evidence…</h4>";
  try {
    const resp = await fetch("/api/v1/claims/" + encodeURIComponent(edge.claimId) + "/evidence", {
      headers: { "X-Workspace-Id": edge.workspaceId, "X-Api-Key": edge.apiKey },
    });
    const data = await resp.json();
    detailEl.innerHTML =
      "<h4>Claim: " + data.claim_id + "</h4>" +
      "<b>" + data.predicate + "</b> → " + (data.object_value || data.object_id) + "<br>" +
      "polarity: " + data.polarity + "<br>" +
      "speaker_role: " + data.speaker_role + "<br>" +
      "confidence: " + data.confidence + "<br>" +
      "adjudication: " + data.adjudication_status + "<br>" +
      "<blockquote style='margin:6px 0;padding:6px;background:#f3f4f6;'>" +
      (data.excerpt || "(no excerpt)") + "</blockquote>";
  } catch (e) {
    detailEl.textContent = "Failed to load evidence: " + e;
  }
}

function showNode(node) {
  const detailEl = document.getElementById("detail");
  detailEl.innerHTML = "<h4>Node</h4>id: " + node.id + "<br>kind: " + node.kind;
}
</script>
</body>
</html>
"""
