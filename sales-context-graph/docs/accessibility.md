# Accessibility and language verification

/viz has semantic tabs, arrow/Home/End keyboard navigation, visible focus,
responsive layout, live status regions, an explicit English/Romanian locale
selector, and a standalone Buyer Space page. Automated route-contract tests
protect those structural controls; they do not replace human WCAG testing.

Before a release, record this checklist against the target browser/device:

1. Run axe or an equivalent WCAG 2.2 AA scan against /viz, /viz/panel and
   /viz/buyer.
2. Complete every flow using keyboard only: graph, Q&A, Ask, Alerts, Review,
   Workflows, Buyer Space and error states.
3. Test with NVDA + Firefox and VoiceOver + Safari: tab names, focus movement,
   status/error announcements, form labels and buyer-space content order.
4. Test 320 px viewport, 200% zoom and OS high-contrast mode.
5. Review English and Romanian strings with a native speaker; add an RTL locale
   only together with direction-aware visual testing.

The buyer invitation token belongs in the #token=... URL fragment. Browsers do
not send URL fragments in HTTP requests; the page moves it into
sessionStorage and uses X-Buyer-Token for API calls. Do not put buyer or
workspace API tokens in query parameters.
