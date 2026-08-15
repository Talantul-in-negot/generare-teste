"""Neo4j schema owned by the business-object layer.

Same `(tenant, id)` uniqueness convention as `graphrag/context_graph/schema.py`.
Wired into `Neo4jClient.init_schema()` the same way the Context Graph schema
is: imported and executed statement-by-statement after the base schema.
"""

BUSINESS_SCHEMA = (
    "CREATE CONSTRAINT biz_finding_id IF NOT EXISTS "
    "FOR (n:BizComplianceFinding) REQUIRE (n.tenant, n.id) IS UNIQUE",
    "CREATE CONSTRAINT biz_work_order_id IF NOT EXISTS "
    "FOR (n:BizWorkOrder) REQUIRE (n.tenant, n.id) IS UNIQUE",
    "CREATE CONSTRAINT biz_transition_id IF NOT EXISTS "
    "FOR (n:BizTransition) REQUIRE (n.tenant, n.id) IS UNIQUE",
    "CREATE INDEX biz_finding_tenant_status IF NOT EXISTS "
    "FOR (n:BizComplianceFinding) ON (n.tenant, n.status)",
    "CREATE INDEX biz_work_order_tenant_status IF NOT EXISTS "
    "FOR (n:BizWorkOrder) ON (n.tenant, n.status)",
    "CREATE INDEX biz_work_order_finding IF NOT EXISTS "
    "FOR (n:BizWorkOrder) ON (n.tenant, n.originating_finding_id)",
    "CREATE INDEX biz_transition_object IF NOT EXISTS "
    "FOR (n:BizTransition) ON (n.tenant, n.object_id, n.recorded_at)",
    "CREATE CONSTRAINT biz_approval_id IF NOT EXISTS "
    "FOR (n:BizApproval) REQUIRE (n.tenant, n.id) IS UNIQUE",
    "CREATE INDEX biz_approval_command IF NOT EXISTS "
    "FOR (n:BizApproval) ON (n.tenant, n.command_id)",
    "CREATE CONSTRAINT biz_command_receipt_id IF NOT EXISTS "
    "FOR (n:BizCommandReceipt) REQUIRE (n.tenant, n.command_id) IS UNIQUE",
    "CREATE CONSTRAINT biz_compensation_id IF NOT EXISTS "
    "FOR (n:BizCompensation) REQUIRE (n.tenant, n.id) IS UNIQUE",
    "CREATE INDEX biz_compensation_original_command IF NOT EXISTS "
    "FOR (n:BizCompensation) ON (n.tenant, n.original_command_id)",
)
