"""Minimal browser visualization for the P4.5 Context Graph plus the
product-completeness pass's Q&A/insights/ask/digest endpoints.

Not part of docs/plan.md's required API surface (§11) — a debugging/demo aid
layered on top of existing GET/POST endpoints. Four tabs on `/viz`:

- "Context Graph": renders Claims as a subject --predicate--> object
  node-link graph via a small hand-rolled force layout (no CDN dependency).
- "Browse Intents": a generic runner over every Q&A intent (api/routes/qa.py)
  and insights endpoint (api/routes/insights.py). Increment 20 replaced the
  previously hardcoded per-endpoint JS array with a fetch of GET
  /api/v1/qa/intents (src/nlq/catalog.py) — the same catalog the natural-
  language layer and its own structural tests already treat as the single
  source of truth, so this list can no longer drift from the real API surface.
- "Ask": free-text question -> POST /api/v1/ask, showing the resolved intent,
  confidence, any ambiguities the system refused to guess through, the
  grounded narrative (if requested), and the underlying structured result.
- "Alerts": GET /api/v1/digest, the proactive signals from Increment 17.

Separately, `GET /viz/panel` (Increment 20) is a compact, single-opportunity,
iframe-embeddable view (alerts + open objections + buying committee) meant for
embedding in Salesforce/Showpad — an embeddable panel, not a packaged
Salesforce/Showpad app (no OAuth, no AppExchange packaging; see README.md).
"""

from __future__ import annotations

import json
import html

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api.dependencies import verify_api_key, verify_panel_token
from src.core.config import get_settings
from src.viz.panel_tokens import PanelTokenClaims, PanelTokenError, mint_panel_token

router = APIRouter(tags=["viz"])


class PanelTokenRequest(BaseModel):
    opportunity_id: str


@router.post("/viz/panel-token")
async def create_panel_token(body: PanelTokenRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    """Mints the token GET /viz/panel now requires, replacing the raw
    X-Api-Key that used to sit directly in the panel's URL (docs/
    evaluation.md's Showpad-compatibility analysis, item 3). Requires the
    real API key -- whoever configures the Salesforce/Showpad embed calls
    this once, out of band, and only the returned token goes into the
    iframe src; the real key never reaches the browser. See
    src/viz/panel_tokens.py for the token's shape, expiry, and revocation.
    """
    try:
        token = await mint_panel_token(workspace_id, body.opportunity_id)
    except PanelTokenError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"token": token, "panel_url": f"/viz/panel?token={token}"}


@router.get("/viz", response_class=HTMLResponse)
async def context_graph_viz() -> str:
    settings = get_settings()
    if not (settings.demo_public_access_enabled and settings.demo_public_api_key):
        return _PAGE
    # The key is exposed only in the deliberately opt-in demo mode. It is
    # scoped to the synthetic workspace and accepted only on read-only paths.
    key = html.escape(settings.demo_public_api_key, quote=True)
    workspace = html.escape(settings.demo_public_workspace_id, quote=True)
    page = _PAGE
    for element_id in ("apiKey", "qaApiKey", "askApiKey", "alertsApiKey"):
        page = page.replace(f'id="{element_id}" type="password" placeholder="X-Api-Key"',
                            f'id="{element_id}" type="password" value="{key}" placeholder="X-Api-Key"')
    for element_id in ("workspaceId", "qaWorkspaceId", "askWorkspaceId", "alertsWorkspaceId"):
        page = page.replace(f'id="{element_id}" value="ws-demo"',
                            f'id="{element_id}" value="{workspace}"')
    return page


@router.get("/viz/panel", response_class=HTMLResponse)
async def opportunity_panel(response: Response, token: str, claims: PanelTokenClaims = Depends(verify_panel_token)) -> str:
    settings = get_settings()
    allowed = settings.embed_allowed_origins.strip()
    # No CORSMiddleware is registered for this single-purpose header (see
    # docs/operations.md's "no new infrastructure until measured need" stance
    # applied here too) — frame-ancestors is set directly on this one route's
    # response. Empty setting means "no origin may embed this," not "any can."
    response.headers["Content-Security-Policy"] = (
        f"frame-ancestors {allowed}" if allowed else "frame-ancestors 'none'"
    )
    # workspace_id/opportunity_id come from the *validated token*, not
    # re-parsed from client-supplied query params -- a caller can no longer
    # claim a different opportunity_id than the one their token was minted
    # for. `token` itself is re-embedded verbatim so the page's own JS can
    # present it as X-Panel-Token on its 3 downstream fetches below.
    page = _PANEL_PAGE
    page = page.replace("__PANEL_TOKEN_JSON__", json.dumps(token))
    page = page.replace("__WORKSPACE_ID_JSON__", json.dumps(claims.workspace_id))
    page = page.replace("__OPPORTUNITY_ID_JSON__", json.dumps(claims.opportunity_id))
    return page


# ── Brand tokens (Phase 9, docs/evaluation.md's "Brand and visual layer"
# finding) ────────────────────────────────────────────────────────────────
# Before this, the same semantic color existed as up to 4 independent hex
# literals across this file: CSS strings (_SHARED_STYLES), inline style=
# attributes, string-concatenated innerHTML, and JS constants
# (polarityColor/entityColor/literalColor) driving the SVG graph -- no
# shared token, so the legend and the JS could (and did) drift apart. This
# one flat dict is now the *only* place a color is spelled out as a
# literal: every CSS rule below reads var(--color-*), the legend swatches
# read the same var(), and _js_color_constants() generates the JS object
# from these exact same values -- not hand-typed a second time. Palette
# values are Showpad's public brand tokens (brandcenter.showpad.com):
# primary Navy with 3 secondary tones, Brick/Plum accents, warm Sand/Cream/
# White neutrals.
BRAND_PALETTE = {
    # Raw Showpad brand tokens, verbatim.
    "navy": "#0d5189",
    "navy-dark": "#15254e",
    "navy-mid": "#0b4472",
    "navy-light": "#539dc4",
    "brick": "#dd7159",
    "plum": "#8c3fcc",
    "sand": "#e8ded4",
    "cream": "#f0ece8",
    "white": "#eeeeee",
    # Context Graph tab semantics (edge polarity + node kind) -- the exact
    # values the legend swatches and the JS polarityColor/entityColor/
    # literalColor constants below are generated from.
    "entity": "#0d5189",        # was Tailwind blue #2563eb -> Showpad Navy
    "literal": "#6b6258",       # was cool grey #6b7280 -> warmed neutral
    "affirmed": "#1f7a4d",      # was Tailwind green #16a34a -> warmed green
    "negated": "#dd7159",       # was Tailwind red #dc2626 -> Showpad Brick
    "hypothetical": "#c98a2c",  # was Tailwind amber #ca8a04 -> warmed amber
    # Interactive controls (buttons, active tab)
    "accent": "#8c3fcc",        # Plum
    "accent-hover": "#7530ad",  # darkened Plum for :hover
    "on-accent": "#ffffff",     # text/icon color drawn on an accent background
    # Layout / base text
    "border": "#ddd0c4",        # warmed border grey, was #ddd
    "surface": "#f0ece8",       # Cream, was #f3f4f6
    "surface-alt": "#e8ded4",   # Sand, was #e5e7eb
    "text": "#15254e",          # navy-dark, was implicit black/#555/#374151
    "text-muted": "#6b6258",
    # Status badges / ambiguity callouts
    "success-bg": "#dbeee1",
    "danger-bg": "#f7e3dd",
    "danger-text": "#b3492f",   # was #b91c1c / #991b1b (two slightly
                                 # different reds for the same "error" role)
    "warning-bg": "#f5e7c6",
    "warning-border": "#c98a2c",
    "legend-bg": "rgba(240,236,232,0.92)",  # Cream, translucent
}

# Showpad's type system (docs/evaluation.md): Nib Pro SemiBold for
# headlines (fallback Lora), Söhne for body (fallback Mona Sans), Söhne
# Mono for tabular/technical text (fallback Noto Sans Mono). No font files
# are bundled (no StaticFiles mount in this repo, docs/evaluation.md notes
# branding here is text-only) -- these resolve to the fallback unless the
# brand fonts happen to be installed locally, same caveat
# docs/architecture.html already carries for its own font stack.
TYPOGRAPHY = {
    "headline": "'Nib Pro SemiBold', Lora, serif",
    "body": "Söhne, 'Mona Sans', system-ui, sans-serif",
    "mono": "'Söhne Mono', 'Noto Sans Mono', monospace",
}


def _root_css_vars() -> str:
    """The single generation point for every --color-*/--font-* custom
    property this file's CSS reads. See BRAND_PALETTE's module comment."""
    color_lines = "\n".join(f"  --color-{key}: {value};" for key, value in BRAND_PALETTE.items())
    font_lines = "\n".join(f"  --font-{key}: {value};" for key, value in TYPOGRAPHY.items())
    return ":root {\n" + color_lines + "\n" + font_lines + "\n}"


def _js_color_constants() -> str:
    """Generates polarityColor/entityColor/literalColor straight from
    BRAND_PALETTE -- the JS the SVG graph renderer reads can no longer
    drift from the CSS custom properties or the legend swatches below; all
    three now trace back to the same dict keys instead of 3 independent
    hand-typed copies."""
    polarity_entries = ", ".join(
        f'{polarity}: "{BRAND_PALETTE[role]}"'
        for polarity, role in (("AFFIRMED", "affirmed"), ("NEGATED", "negated"), ("HYPOTHETICAL", "hypothetical"))
    )
    return (
        f"const polarityColor = {{ {polarity_entries} }};\n"
        f'const entityColor = "{BRAND_PALETTE["entity"]}";\n'
        f'const literalColor = "{BRAND_PALETTE["literal"]}";'
    )


def _legend_swatches_html() -> str:
    """Same source as _js_color_constants() above -- each swatch reads the
    CSS custom property (itself generated from BRAND_PALETTE), so a legend
    swatch and its corresponding JS graph color can never show a different
    hex for the same semantic role."""
    rows = (
        ("affirmed", "AFFIRMED"),
        ("negated", "NEGATED"),
        ("hypothetical", "HYPOTHETICAL"),
        ("entity", "entity node"),
        ("literal", "literal value node"),
    )
    return "\n".join(
        f'        <div><span class="swatch" style="background:var(--color-{key})"></span>{label}</div>'
        for key, label in rows
    )


# Shared between /viz and /viz/panel so both pages render JSON identically
# without duplicating the renderer.
_RENDER_JSON_JS = """
// Generic, recursive JSON -> readable-HTML renderer: scalars print inline,
// arrays of objects become tables (one row per item, columns from the union
// of keys), arrays of scalars become a comma list, objects become a
// definition-list-like block of "key: value" rows with nested rendering.
function renderJson(value) {
  const wrap = document.createElement("div");
  if (value === null || value === undefined) {
    wrap.className = "result-scalar"; wrap.textContent = "(none)"; return wrap;
  }
  if (typeof value !== "object") {
    wrap.className = "result-scalar";
    if (typeof value === "boolean") {
      const badge = document.createElement("span");
      badge.className = "badge " + value;
      badge.textContent = String(value);
      wrap.appendChild(badge);
    } else {
      wrap.textContent = String(value);
    }
    return wrap;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) { wrap.className = "result-scalar"; wrap.textContent = "(empty)"; return wrap; }
    const allScalar = value.every(v => v === null || typeof v !== "object");
    if (allScalar) {
      wrap.className = "result-scalar";
      wrap.textContent = value.join(", ");
      return wrap;
    }
    const cols = Array.from(value.reduce((set, row) => {
      if (row && typeof row === "object" && !Array.isArray(row)) Object.keys(row).forEach(k => set.add(k));
      return set;
    }, new Set()));
    const table = document.createElement("table");
    table.className = "result";
    const thead = document.createElement("tr");
    for (const c of cols) { const th = document.createElement("th"); th.textContent = c; thead.appendChild(th); }
    table.appendChild(thead);
    for (const row of value) {
      const tr = document.createElement("tr");
      if (row && typeof row === "object" && !Array.isArray(row)) {
        for (const c of cols) { const td = document.createElement("td"); td.appendChild(renderJson(row[c])); tr.appendChild(td); }
      } else {
        const td = document.createElement("td"); td.colSpan = cols.length || 1; td.appendChild(renderJson(row)); tr.appendChild(td);
      }
      table.appendChild(tr);
    }
    wrap.appendChild(table);
    return wrap;
  }
  for (const [k, v] of Object.entries(value)) {
    const keyEl = document.createElement("div");
    keyEl.className = "result-key";
    keyEl.textContent = k;
    wrap.appendChild(keyEl);
    wrap.appendChild(renderJson(v));
  }
  return wrap;
}
"""

_SHARED_STYLES = """
""" + _root_css_vars() + """
  body { font-family: var(--font-body); color: var(--color-text); }
  h3, h4 { font-family: var(--font-headline); }
  table.result { border-collapse: collapse; margin: 6px 0 14px 0; font-size: 12px; font-family: var(--font-mono); }
  table.result td, table.result th { border: 1px solid var(--color-border); padding: 4px 8px; text-align: left; vertical-align: top; }
  table.result th { background: var(--color-surface); }
  .result-key { font-weight: 600; color: var(--color-text); margin-top: 10px; font-family: var(--font-body); }
  .result-scalar { font-size: 13px; margin: 4px 0; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; background: var(--color-surface-alt); }
  .badge.true { background: var(--color-success-bg); color: var(--color-affirmed); }
  .badge.false { background: var(--color-danger-bg); color: var(--color-danger-text); }
"""

_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Sales Context Graph Viz</title>
<style>
""" + _SHARED_STYLES + """
  body { margin: 0; display: flex; height: 100vh; }
  #panel { width: 380px; padding: 16px; box-sizing: border-box; border-right: 1px solid var(--color-border); overflow-y: auto; }
  #panel label { display: block; margin-top: 10px; font-size: 12px; color: var(--color-text-muted); }
  #panel input, #panel select, #panel textarea { width: 100%; padding: 6px; box-sizing: border-box; margin-top: 2px; font-family: inherit; }
  #panel button { margin-top: 14px; width: 100%; padding: 8px; background: var(--color-accent); color: var(--color-on-accent); border: none; border-radius: 4px; cursor: pointer; font-family: var(--font-body); }
  #panel button:hover { background: var(--color-accent-hover); }
  .quick-questions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 8px 0 12px; }
  .quick-question { width: auto !important; min-height: 30px; margin: 0 !important; padding: 5px 9px !important; border-radius: 5px !important; text-align: left; }
  #panel input[type=checkbox] { width: auto; }
  #status, #qaStatus, #askStatus, #alertsStatus { margin-top: 10px; font-size: 12px; color: var(--color-danger-text); white-space: pre-wrap; }
  #meta { margin-top: 14px; font-size: 12px; color: var(--color-text-muted); }
  #detail { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--color-border); font-size: 12px; }
  #detail h4 { margin: 0 0 6px 0; }
  #main { flex: 1; position: relative; overflow: auto; }
  #graph { position: absolute; inset: 0; }
  svg { width: 100%; height: 100%; user-select: none; }
  .node circle { stroke: var(--color-on-accent); stroke-width: 1.5px; cursor: pointer; }
  .node text { font-size: 10px; pointer-events: none; font-family: var(--font-mono); }
  .edge-label { font-size: 9px; fill: var(--color-text-muted); pointer-events: none; font-family: var(--font-mono); }
  .legend { position: absolute; bottom: 10px; left: 10px; font-size: 11px; background: var(--color-legend-bg); padding: 8px; border-radius: 4px; }
  .legend div { display: flex; align-items: center; margin-bottom: 3px; }
  .legend span.swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .tabs { display: flex; border-bottom: 1px solid var(--color-border); flex-wrap: wrap; }
  .tab { flex: 1; padding: 10px 6px; text-align: center; cursor: pointer; font-size: 12px; color: var(--color-text-muted); border-bottom: 2px solid transparent; }
  .tab.active { color: var(--color-accent); border-bottom-color: var(--color-accent); font-weight: 600; }
  .tabpage { display: none; padding: 20px; }
  .tabpage.active { display: block; }
  .citation { font-size: 11px; color: var(--color-text-muted); margin: 2px 0; }
  .uncited { font-size: 11px; color: var(--color-danger-text); margin: 2px 0; }
  .ambiguity { background: var(--color-warning-bg); border: 1px solid var(--color-warning-border); padding: 8px; border-radius: 4px; margin: 6px 0; font-size: 12px; }
</style>
</head>
<body>
<div id="panel">
  <div class="tabs">
    <div class="tab active" data-tab="graph">Context Graph</div>
    <div class="tab" data-tab="qa">Browse Intents</div>
    <div class="tab" data-tab="ask">Ask</div>
    <div class="tab" data-tab="alerts">Alerts</div>
  </div>

  <div id="graph-controls">
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

  <div id="qa-controls" style="display:none">
    <h3>Browse Intents</h3>
    <label>Workspace ID
      <input id="qaWorkspaceId" value="ws-demo">
    </label>
    <label>API Key
      <input id="qaApiKey" type="password" placeholder="X-Api-Key">
    </label>
    <label>Question
      <select id="qaSelect"></select>
    </label>
    <div id="qaFields"></div>
    <button id="qaRunBtn">Run</button>
    <div id="qaStatus"></div>
  </div>

  <div id="ask-controls" style="display:none">
    <h3>Ask</h3>
    <p style="font-size:12px;color:var(--color-text-muted)">Free-form question -&gt; natural-language intent layer (Increment 15). Requires LLM_PROVIDER configured server-side.</p>
    <label>Workspace ID
      <input id="askWorkspaceId" value="ws-demo">
    </label>
    <label>API Key
      <input id="askApiKey" type="password" placeholder="X-Api-Key">
    </label>
    <label>Question
      <textarea id="askQuestion" rows="3" placeholder="e.g. what objections has Volkswagen raised?"></textarea>
    </label>
    <div class="quick-questions" aria-label="Quick questions">
      <span style="font-size:12px;color:var(--color-text-muted)">Quick:</span>
      <button type="button" class="quick-question" data-question="What objections are currently open?"><b>1</b> What objections are currently open?</button>
      <button type="button" class="quick-question" data-question="Who have we not engaged in this opportunity?"><b>2</b> Who have we not engaged?</button>
      <button type="button" class="quick-question" data-question="What content should I send next?"><b>3</b> What content should I send?</button>
      <button type="button" class="quick-question" data-question="What changed since the last call?"><b>4</b> What changed since last call?</button>
    </div>
    <label><input id="askNarrative" type="checkbox"> Include narrative summary</label>
    <label><input id="askVoice" type="checkbox"> Read answer aloud (optional TTS)</label>
    <details style="margin-top:10px">
      <summary style="font-size:12px;cursor:pointer">Optional context (ids the UI would already know)</summary>
      <label>Opportunity ID <input id="askOpportunityId"></label>
      <label>Seller ID <input id="askSellerId"></label>
      <label>Conversation ID <input id="askConversationId"></label>
      <label>Subject ID <input id="askSubjectId"></label>
      <label>Buyer Contact ID <input id="askBuyerContactId"></label>
    </details>
    <button id="askRunBtn">Ask</button>
    <div id="askStatus"></div>
  </div>

  <div id="alerts-controls" style="display:none">
    <h3>Alerts</h3>
    <p style="font-size:12px;color:var(--color-text-muted)">Proactive signals (Increment 17): single-threaded deals, unanswered objections, unopened content, unresolved conflicts, stalled deals.</p>
    <label>Workspace ID
      <input id="alertsWorkspaceId" value="ws-demo">
    </label>
    <label>API Key
      <input id="alertsApiKey" type="password" placeholder="X-Api-Key">
    </label>
    <label>Seller ID (optional, narrows to one rep's pipeline)
      <input id="alertsSellerId" placeholder="optional">
    </label>
    <button id="alertsRunBtn">Get digest</button>
    <div id="alertsStatus"></div>
  </div>
</div>

<div id="main">
  <div id="graph-page" class="tabpage active" style="position:absolute;inset:0;padding:0">
    <div id="graph">
      <svg id="svg"></svg>
      <div class="legend">
""" + _legend_swatches_html() + """
      </div>
    </div>
  </div>
  <div id="qa-page" class="tabpage">
    <div id="qaResult"></div>
  </div>
  <div id="ask-page" class="tabpage">
    <div id="askResult"></div>
  </div>
  <div id="alerts-page" class="tabpage">
    <div id="alertsResult"></div>
  </div>
</div>

<script>
/* ── Tabs ─────────────────────────────────────────────────────────────── */
const TAB_NAMES = ["graph", "qa", "ask", "alerts"];
for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    const target = tab.dataset.tab;
    for (const name of TAB_NAMES) {
      document.getElementById(name + "-controls").style.display = name === target ? "" : "none";
      document.getElementById(name + "-page").classList.toggle("active", name === target);
    }
    if (target === "qa" && qaSelect.options.length === 0) loadIntents();
  });
}

/* ── Context Graph (unchanged from the original single-tab page) ────────── */
""" + _js_color_constants() + """

let nodes = [];
let edges = [];
let svgEl = document.getElementById("svg");

// Drag-to-reposition: pointer capture lives on svgEl itself (not the
// per-node <g>), because render() below does svgEl.innerHTML = "" on every
// frame — a capture held by a <g> would be silently dropped the instant a
// drag causes a re-render, killing the drag mid-motion.
let draggingNode = null;
let dragMoved = false;
let currentWorkspaceId = null;

svgEl.addEventListener("pointermove", (ev) => {
  if (!draggingNode) return;
  dragMoved = true;
  const rect = svgEl.getBoundingClientRect();
  const W = svgEl.clientWidth || 800, H = svgEl.clientHeight || 600;
  draggingNode.x = Math.max(30, Math.min(W - 30, ev.clientX - rect.left));
  draggingNode.y = Math.max(30, Math.min(H - 30, ev.clientY - rect.top));
  draggingNode.vx = 0;
  draggingNode.vy = 0;
  render(currentWorkspaceId);
});
svgEl.addEventListener("pointerup", () => { draggingNode = null; });
svgEl.addEventListener("pointercancel", () => { draggingNode = null; });

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
  currentWorkspaceId = workspaceId;
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
    line.setAttribute("stroke", polarityColor[e.polarity] || literalColor);
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

    g.style.cursor = "grab";
    g.addEventListener("pointerdown", (ev) => {
      draggingNode = n;
      dragMoved = false;
      svgEl.setPointerCapture(ev.pointerId);
      ev.stopPropagation();
    });
    // click still fires after a drag's pointerup — dragMoved suppresses the
    // detail panel from popping open on every drag release.
    g.addEventListener("click", () => { if (!dragMoved) showNode(n); });
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
      "<blockquote style='margin:6px 0;padding:6px;background:var(--color-surface);'>" +
      (data.excerpt || "(no excerpt)") + "</blockquote>";
  } catch (e) {
    detailEl.textContent = "Failed to load evidence: " + e;
  }
}

function showNode(node) {
  const detailEl = document.getElementById("detail");
  detailEl.innerHTML = "<h4>Node</h4>id: " + node.id + "<br>kind: " + node.kind;
}

/* ── Browse Intents (Q&A / insights runner) ──────────────────────────────
   Increment 20: ENDPOINTS is no longer a hardcoded array — it's populated
   from GET /api/v1/qa/intents (src/nlq/catalog.py), the same catalog the
   natural-language layer and its structural tests already treat as the
   single source of truth. */
let ENDPOINTS = [];

async function loadIntents() {
  const statusEl = document.getElementById("qaStatus");
  const workspaceId = document.getElementById("qaWorkspaceId").value.trim();
  const apiKey = document.getElementById("qaApiKey").value.trim();
  if (!workspaceId || !apiKey) {
    statusEl.textContent = "Enter Workspace ID and API Key, then reopen this tab to load the intent list.";
    return;
  }
  statusEl.textContent = "Loading intents…";
  try {
    const resp = await fetch("/api/v1/qa/intents", {
      headers: { "X-Workspace-Id": workspaceId, "X-Api-Key": apiKey },
    });
    if (!resp.ok) { statusEl.textContent = "HTTP " + resp.status + ": " + (await resp.text()); return; }
    const data = await resp.json();
    ENDPOINTS = data.intents.map(i => ({
      id: i.intent_id, label: i.question, method: i.method, path: i.path,
      fields: i.params.map(p => ({
        name: p.name, required: p.required,
        label: p.name.replace(/_/g, " ") + (p.kind === "datetime" ? " (ISO datetime, e.g. 2026-06-01T00:00:00Z)" : ""),
      })),
    }));
    qaSelect.innerHTML = "";
    for (const ep of ENDPOINTS) {
      const opt = document.createElement("option");
      opt.value = ep.id;
      opt.textContent = ep.label;
      qaSelect.appendChild(opt);
    }
    statusEl.textContent = "";
    renderQaFields();
  } catch (e) {
    statusEl.textContent = "Failed to load intents: " + e;
  }
}

const qaSelect = document.getElementById("qaSelect");
qaSelect.addEventListener("change", renderQaFields);

function renderQaFields() {
  const ep = ENDPOINTS.find(e => e.id === qaSelect.value);
  const container = document.getElementById("qaFields");
  container.innerHTML = "";
  if (!ep) return;
  for (const f of ep.fields) {
    const label = document.createElement("label");
    label.textContent = f.label + (f.required ? " *" : "");
    const input = document.createElement("input");
    input.id = "qaField_" + f.name;
    label.appendChild(input);
    container.appendChild(label);
  }
}

document.getElementById("qaRunBtn").addEventListener("click", runQa);

async function runQa() {
  const statusEl = document.getElementById("qaStatus");
  const resultEl = document.getElementById("qaResult");
  statusEl.textContent = "";
  const ep = ENDPOINTS.find(e => e.id === qaSelect.value);
  if (!ep) { statusEl.textContent = "Load the intent list first (re-open this tab with credentials filled in)."; return; }

  const workspaceId = document.getElementById("qaWorkspaceId").value.trim();
  const apiKey = document.getElementById("qaApiKey").value.trim();
  if (!workspaceId) { statusEl.textContent = "Workspace ID is required."; return; }
  if (!apiKey) { statusEl.textContent = "API Key is required."; return; }

  const values = {};
  for (const f of ep.fields) {
    const raw = document.getElementById("qaField_" + f.name).value.trim();
    if (f.required && !raw) { statusEl.textContent = f.label + " is required."; return; }
    if (raw) values[f.name] = raw;
  }

  let path = ep.path;
  const bodyFields = {};
  for (const [key, val] of Object.entries(values)) {
    if (path.includes("{" + key + "}")) {
      path = path.replace("{" + key + "}", encodeURIComponent(val));
    } else {
      bodyFields[key] = val;
    }
  }

  const options = {
    method: ep.method,
    headers: { "X-Workspace-Id": workspaceId, "X-Api-Key": apiKey },
  };
  if (ep.method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(bodyFields);
  }

  resultEl.innerHTML = "<p>Loading…</p>";
  let resp;
  try {
    resp = await fetch(path, options);
  } catch (e) {
    statusEl.textContent = "Request failed: " + e;
    resultEl.innerHTML = "";
    return;
  }
  const text = await resp.text();
  if (!resp.ok) {
    statusEl.textContent = "HTTP " + resp.status + ": " + text;
    resultEl.innerHTML = "";
    return;
  }
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  resultEl.innerHTML = "<h3>" + ep.label + "</h3>";
  resultEl.appendChild(renderJson(data));
}

/* ── Ask (natural language, Increment 15/16) ─────────────────────────────── */
document.getElementById("askRunBtn").addEventListener("click", runAsk);
document.querySelectorAll(".quick-question").forEach((button) => {
  button.addEventListener("click", () => {
    document.getElementById("askQuestion").value = button.dataset.question;
    document.getElementById("askQuestion").focus();
  });
});
window.addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey || !["1", "2", "3", "4"].includes(event.key)) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  const button = document.querySelector(`.quick-question:nth-of-type(${Number(event.key)})`);
  if (button) button.click();
});

async function runAsk() {
  const statusEl = document.getElementById("askStatus");
  const resultEl = document.getElementById("askResult");
  statusEl.textContent = "";
  resultEl.innerHTML = "";

  const workspaceId = document.getElementById("askWorkspaceId").value.trim();
  const apiKey = document.getElementById("askApiKey").value.trim();
  const question = document.getElementById("askQuestion").value.trim();
  if (!workspaceId) { statusEl.textContent = "Workspace ID is required."; return; }
  if (!apiKey) { statusEl.textContent = "API Key is required."; return; }
  if (!question) { statusEl.textContent = "Question is required."; return; }

  const body = { question, include_narrative: document.getElementById("askNarrative").checked };
  const contextFields = {
    opportunity_id: "askOpportunityId", seller_id: "askSellerId",
    conversation_id: "askConversationId", subject_id: "askSubjectId",
    buyer_contact_id: "askBuyerContactId",
  };
  for (const [key, elId] of Object.entries(contextFields)) {
    const v = document.getElementById(elId).value.trim();
    if (v) body[key] = v;
  }

  resultEl.innerHTML = "<p>Thinking…</p>";
  let resp;
  try {
    resp = await fetch("/api/v1/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Workspace-Id": workspaceId, "X-Api-Key": apiKey },
      body: JSON.stringify(body),
    });
  } catch (e) {
    statusEl.textContent = "Request failed: " + e;
    resultEl.innerHTML = "";
    return;
  }
  const text = await resp.text();
  if (!resp.ok) {
    statusEl.textContent = "HTTP " + resp.status + ": " + text;
    resultEl.innerHTML = "";
    return;
  }
  const data = JSON.parse(text);
  resultEl.innerHTML = "";

  const header = document.createElement("div");
  header.innerHTML =
    "<h3>" + (data.answered ? "Answer" : "Could not answer") + "</h3>" +
    "<div class='result-scalar'>intent: <b>" + (data.intent_id || "?") + "</b>" +
    (data.confidence != null ? " (confidence " + data.confidence.toFixed(2) + ")" : "") + "</div>" +
    (data.reasoning ? "<div class='result-scalar'>reasoning: " + data.reasoning + "</div>" : "");
  resultEl.appendChild(header);

  if (document.getElementById("askVoice").checked && data.answered) {
    const audioText = data.narrative?.text || data.reasoning || JSON.stringify(data.result || {});
    const audioStatus = document.createElement("div");
    audioStatus.className = "result-scalar";
    audioStatus.textContent = "Preparing audio…";
    resultEl.appendChild(audioStatus);
    fetch("/api/v1/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Workspace-Id": workspaceId, "X-Api-Key": apiKey },
      body: JSON.stringify({ text: audioText }),
    }).then(async (audioResp) => {
      if (!audioResp.ok) throw new Error("TTS HTTP " + audioResp.status);
      const player = document.createElement("audio");
      player.controls = true;
      player.autoplay = false;
      player.src = URL.createObjectURL(await audioResp.blob());
      audioStatus.replaceWith(player);
    }).catch((error) => { audioStatus.textContent = "Audio unavailable; text answer remains available. " + error; });
  }

  for (const a of data.ambiguities || []) {
    const div = document.createElement("div");
    div.className = "ambiguity";
    div.textContent = (a.param ? "[" + a.param + "] " : "") + a.reason;
    resultEl.appendChild(div);
  }

  if (data.narrative) {
    const narrDiv = document.createElement("div");
    narrDiv.className = "result-key";
    narrDiv.textContent = "Narrative";
    resultEl.appendChild(narrDiv);
    const p = document.createElement("p");
    p.textContent = data.narrative.text;
    resultEl.appendChild(p);
    for (const c of data.narrative.citations || []) {
      const cDiv = document.createElement("div");
      cDiv.className = "citation";
      cDiv.textContent = "[" + c.claim_id + "] " + c.excerpt;
      resultEl.appendChild(cDiv);
    }
    for (const u of data.narrative.uncited_sentences || []) {
      const uDiv = document.createElement("div");
      uDiv.className = "uncited";
      uDiv.textContent = "(uncited) " + u;
      resultEl.appendChild(uDiv);
    }
  }

  if (data.result) {
    const resDiv = document.createElement("div");
    resDiv.className = "result-key";
    resDiv.textContent = "Result";
    resultEl.appendChild(resDiv);
    resultEl.appendChild(renderJson(data.result));
  }
}

/* ── Alerts (proactive digest, Increment 17) ─────────────────────────────── */
document.getElementById("alertsRunBtn").addEventListener("click", runAlerts);

async function runAlerts() {
  const statusEl = document.getElementById("alertsStatus");
  const resultEl = document.getElementById("alertsResult");
  statusEl.textContent = "";

  const workspaceId = document.getElementById("alertsWorkspaceId").value.trim();
  const apiKey = document.getElementById("alertsApiKey").value.trim();
  const sellerId = document.getElementById("alertsSellerId").value.trim();
  if (!workspaceId) { statusEl.textContent = "Workspace ID is required."; return; }
  if (!apiKey) { statusEl.textContent = "API Key is required."; return; }

  let path = "/api/v1/digest";
  if (sellerId) path += "?seller_id=" + encodeURIComponent(sellerId);

  resultEl.innerHTML = "<p>Loading…</p>";
  let resp;
  try {
    resp = await fetch(path, { headers: { "X-Workspace-Id": workspaceId, "X-Api-Key": apiKey } });
  } catch (e) {
    statusEl.textContent = "Request failed: " + e;
    resultEl.innerHTML = "";
    return;
  }
  const text = await resp.text();
  if (!resp.ok) {
    statusEl.textContent = "HTTP " + resp.status + ": " + text;
    resultEl.innerHTML = "";
    return;
  }
  const data = JSON.parse(text);
  resultEl.innerHTML =
    "<h3>Digest</h3><div class='result-scalar'>" +
    data.opportunity_count + " open opportunities, " + data.signals.length + " signal(s)</div>";
  resultEl.appendChild(renderJson(data.signals));
}

""" + _RENDER_JSON_JS + """
</script>
</body>
</html>
"""

_PANEL_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Opportunity Panel</title>
<style>
""" + _SHARED_STYLES + """
  body { margin: 0; padding: 12px; font-size: 13px; }
  h4 { margin: 14px 0 4px 0; }
  #status { color: var(--color-danger-text); font-size: 12px; }
</style>
</head>
<body>
<div id="status"></div>
<div id="content"></div>
<script>
// Injected server-side by GET /viz/panel (api/routes/viz.py) from the
// *validated* panel token -- never re-parsed from client-editable query
// params. See src/viz/panel_tokens.py.
const workspaceId = __WORKSPACE_ID_JSON__;
const opportunityId = __OPPORTUNITY_ID_JSON__;
const panelToken = __PANEL_TOKEN_JSON__;
const statusEl = document.getElementById("status");
const contentEl = document.getElementById("content");

""" + _RENDER_JSON_JS + """

async function loadPanel() {
  // Only the scoped panel token -- the real workspace API key never
  // reaches this page. api/dependencies.py::verify_api_key_or_panel_token
  // accepts this header on the 3 endpoints below as an alternative to a
  // real API key.
  const headers = { "X-Panel-Token": panelToken };

  await section("Buying committee", async () => {
    const r = await fetch("/api/v1/opportunities/" + encodeURIComponent(opportunityId) + "/buying-committee", { headers });
    return r.json();
  });

  await section("Open objections", async () => {
    const r = await fetch("/api/v1/qa/account-objections", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ opportunity_id: opportunityId }),
    });
    return r.json();
  });

  await section("Alerts for this deal", async () => {
    const r = await fetch("/api/v1/digest", { headers });
    const data = await r.json();
    return data.signals.filter(s => s.opportunity_id === opportunityId);
  });
}

async function section(title, fetchFn) {
  const h = document.createElement("h4");
  h.textContent = title;
  contentEl.appendChild(h);
  try {
    const data = await fetchFn();
    contentEl.appendChild(renderJson(data));
  } catch (e) {
    const err = document.createElement("div");
    err.className = "result-scalar";
    err.textContent = "Failed to load: " + e;
    contentEl.appendChild(err);
  }
}

loadPanel();
</script>
</body>
</html>
"""
