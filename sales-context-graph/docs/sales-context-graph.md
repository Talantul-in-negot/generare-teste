# Sales Context Graph extension

This document describes the sales-domain completion layered on the existing
tenant-isolated evidence graph. It is not a claim of CRM production adoption.

## Implemented

- Typed sales contracts in `src/domain/sales.py` for accounts, opportunities,
  stakeholders, interactions, commitments, policies, evidence, recommendations
  and governed CRM commands.
- Grounded next-action helper in `src/usecases/sales_intelligence.py`.
  Recommendations require source evidence and a policy version; otherwise the
  result is a structured abstention with missing evidence and collection steps.
- High-risk CRM patches (`stage`, `forecast_category`, `close_date`, and
  `discount`) cannot execute without explicit approval. Dry-run previews remain
  available before approval.

## Existing platform reused

The extension reuses the existing CRM/knowledge models, Neo4j tenant-scoped
repositories, API-key authentication, audit/receipt conventions, review
workflow, rate limiting and Prometheus instrumentation. It does not create a
second graph or bypass those controls.

## Boundary and limitations

The contracts are provider-neutral. A Salesforce/Dynamics connector, external
CRM mutation adapter, remote MCP transport and production policy catalogue are
pending integration-specific validation. Synthetic demo records must remain
labelled as synthetic and must not be presented as customer or production data.

## Verification

```powershell
python -m ruff check src/domain/sales.py src/usecases/sales_intelligence.py tests/unit/domain/test_sales_contracts.py
pytest -q tests/unit/domain/test_sales_contracts.py
```
