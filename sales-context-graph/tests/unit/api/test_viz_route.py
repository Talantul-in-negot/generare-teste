from __future__ import annotations

import re

import httpx
import pytest

from api.main import app
from api.routes import viz
from src.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_viz_page_serves_html() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Context Graph" in resp.text
    assert "/api/v1/context/build" in resp.text


async def test_viz_page_includes_ask_and_alerts_tabs() -> None:
    """Increment 20 — the free-text NL layer and the proactive digest each get
    their own tab, and the intent-runner tab is catalog-driven rather than a
    hardcoded per-endpoint JS array."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz")

    text = resp.text
    assert "/api/v1/ask" in text
    assert "/api/v1/digest" in text
    assert "/api/v1/qa/intents" in text


async def test_viz_page_includes_review_console() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz")

    text = resp.text
    assert "Review Console" in text
    assert "/api/v1/unresolved-mentions" in text
    assert "/top-objections" in text
    assert "/conflicts/" in text


async def test_public_demo_prefills_read_only_review_and_workflow_inputs(monkeypatch) -> None:
    """Later-added Review and Workflows tabs receive the same frictionless
    demo credentials as Graph, Ask and Alerts; API policy still makes writes
    unavailable to the public demo key."""
    monkeypatch.setenv("DEMO_PUBLIC_ACCESS_ENABLED", "true")
    monkeypatch.setenv("DEMO_PUBLIC_WORKSPACE_ID", "ws-public-demo")
    monkeypatch.setenv("DEMO_PUBLIC_API_KEY", "unit-demo-key")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz")

    assert 'id="reviewApiKey" type="password" value="unit-demo-key"' in resp.text
    assert 'id="workflowApiKey" type="password" value="unit-demo-key"' in resp.text
    assert 'id="reviewWorkspaceId" value="ws-public-demo"' in resp.text
    assert 'id="workflowWorkspaceId" value="ws-public-demo"' in resp.text
    assert f'id="reviewOpportunityId" value="{viz._DEMO_OPPORTUNITY_ID}"' in resp.text
    assert f'id="reviewerId" value="{viz._DEMO_REVIEWER_ID}"' in resp.text
    assert f'id="workflowSellerId" value="{viz._DEMO_SELLER_ID}"' in resp.text
    get_settings.cache_clear()


async def test_viz_has_accessible_responsive_workflow_pwa_surface() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/viz")
        manifest = await client.get("/viz/manifest.webmanifest")
        worker = await client.get("/viz/service-worker.js")

    assert "Sales workflows" in page.text
    assert 'role="tablist"' in page.text
    assert "ArrowRight" in page.text
    assert "@media (max-width: 760px)" in page.text
    assert "serviceWorker.register" in page.text
    assert manifest.status_code == 200
    assert worker.status_code == 200
    assert "Authenticated API responses are deliberately never cached" not in worker.text


async def test_viz_has_a_locale_contract_and_buyer_portal_keeps_tokens_out_of_queries() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        viz_page = await client.get("/viz", params={"locale": "ro"})
        buyer_page = await client.get("/viz/buyer")

    assert 'lang="ro"' in viz_page.text
    assert 'id="localeSelect"' in viz_page.text
    assert "data-i18n" in viz_page.text
    assert buyer_page.status_code == 200
    assert "X-Buyer-Token" in buyer_page.text
    assert "location.hash" in buyer_page.text
    assert 'name="referrer" content="no-referrer"' in buyer_page.text


@pytest.fixture
async def panel_token(monkeypatch) -> str:
    """A real, minted panel token (docs/evaluation.md's Showpad-compatibility
    analysis, item 3 -- src/viz/panel_tokens.py). GET /viz/panel now requires
    one instead of a raw API key in the URL; fakeredis stands in for the
    revocation-version store, same pattern as tests/unit/ingestion/test_queue.py."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    import src.viz.panel_tokens as panel_tokens

    monkeypatch.setenv("PANEL_TOKEN_SECRET", "unit-test-panel-secret")
    get_settings.cache_clear()
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(panel_tokens, "get_redis", lambda: client)
    token = await panel_tokens.mint_panel_token("ws-viz-panel-test", "opp-1")
    yield token
    await client.aclose()
    get_settings.cache_clear()


async def test_panel_route_rejects_missing_token() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel")

    assert resp.status_code == 422  # token is a required query param


async def test_panel_route_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setenv("PANEL_TOKEN_SECRET", "unit-test-panel-secret")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel", params={"token": "not-a-real-token"})

    assert resp.status_code == 401
    get_settings.cache_clear()


async def test_panel_route_serves_html_with_a_valid_token(panel_token) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel", params={"token": panel_token})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "opportunity_id" in resp.text
    # workspace/opportunity ids come from the validated token server-side,
    # not a client-supplied query param -- prove they made it into the page.
    assert "ws-viz-panel-test" in resp.text
    assert "opp-1" in resp.text
    # the real API key never appears on this page -- only the panel token.
    assert "X-Api-Key" not in resp.text
    assert "X-Panel-Token" in resp.text


async def test_panel_route_denies_embedding_by_default(monkeypatch, panel_token) -> None:
    monkeypatch.setenv("EMBED_ALLOWED_ORIGINS", "")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel", params={"token": panel_token})

    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
    get_settings.cache_clear()


async def test_panel_route_allows_configured_origins(monkeypatch, panel_token) -> None:
    monkeypatch.setenv("EMBED_ALLOWED_ORIGINS", "https://example.my.salesforce.com")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel", params={"token": panel_token})

    assert resp.headers["content-security-policy"] == "frame-ancestors https://example.my.salesforce.com"
    get_settings.cache_clear()


# ── Phase 9: brand/CSS theming (docs/evaluation.md's "Brand and visual
# layer" finding) ────────────────────────────────────────────────────────
# The bug this finding described wasn't the wrong colors -- it was that the
# same semantic color existed as multiple independent hex literals (CSS,
# inline style=, innerHTML, JS constants) with no shared token, so they
# could silently drift apart. These tests prove the fix is structural, not
# cosmetic: every one of those surfaces is generated from BRAND_PALETTE,
# not hand-typed a second time.

async def test_root_css_vars_declares_every_brand_palette_entry() -> None:
    css = viz._root_css_vars()
    assert css.startswith(":root {")
    for key, value in viz.BRAND_PALETTE.items():
        assert f"--color-{key}: {value};" in css
    for key, value in viz.TYPOGRAPHY.items():
        assert f"--font-{key}: {value};" in css


async def test_js_color_constants_values_match_brand_palette_exactly() -> None:
    """The core anti-drift proof: polarityColor/entityColor/literalColor's
    hex values are read out of BRAND_PALETTE, not retyped -- so it is
    structurally impossible for the JS graph renderer's colors to differ
    from the CSS custom properties the rest of the page uses for the same
    role."""
    js = viz._js_color_constants()

    polarity_match = re.search(r"const polarityColor = \{([^}]*)\};", js)
    assert polarity_match is not None
    polarity_body = polarity_match.group(1)
    assert f'AFFIRMED: "{viz.BRAND_PALETTE["affirmed"]}"' in polarity_body
    assert f'NEGATED: "{viz.BRAND_PALETTE["negated"]}"' in polarity_body
    assert f'HYPOTHETICAL: "{viz.BRAND_PALETTE["hypothetical"]}"' in polarity_body

    assert f'const entityColor = "{viz.BRAND_PALETTE["entity"]}";' in js
    assert f'const literalColor = "{viz.BRAND_PALETTE["literal"]}";' in js


async def test_legend_swatches_reference_the_same_css_vars_as_the_js_constants() -> None:
    """A swatch and its corresponding JS/CSS color trace back to the same
    dict key -- proves the legend can't show a different color than the
    graph it's labeling."""
    legend = viz._legend_swatches_html()
    for role in ("affirmed", "negated", "hypothetical", "entity", "literal"):
        assert f"var(--color-{role})" in legend


async def test_viz_page_contains_no_hardcoded_hex_literals_outside_the_palette() -> None:
    """Regression guard for the original finding (docs/evaluation.md:
    "~25 places across two languages"): every hex literal on the page must
    trace back to a BRAND_PALETTE value, not be a second, independent
    hand-typed copy of one."""
    known_hex_values = {v.lower() for v in viz.BRAND_PALETTE.values() if v.startswith("#")}
    # "#fff"/"#000"-shaped shorthand and any hex not in BRAND_PALETTE would
    # both fail this -- collect every hex literal actually present and
    # diff against the palette.
    found = {m.lower() for m in re.findall(r"#[0-9a-fA-F]{3,8}\b", viz._PAGE)}
    assert found <= known_hex_values, f"undeclared hex literals leaked into _PAGE: {found - known_hex_values}"


async def test_viz_and_panel_pages_load_the_same_root_css_vars() -> None:
    """_SHARED_STYLES is the single inclusion point for _root_css_vars() --
    both pages must carry the identical :root block, not two independently
    maintained copies."""
    assert viz._root_css_vars() in viz._PAGE
    assert viz._root_css_vars() in viz._PANEL_PAGE


async def test_viz_page_serves_the_generated_palette_end_to_end() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz")

    text = resp.text
    assert f"--color-navy: {viz.BRAND_PALETTE['navy']};" in text
    assert "polarityColor" in text
    assert viz.BRAND_PALETTE["affirmed"] in text
