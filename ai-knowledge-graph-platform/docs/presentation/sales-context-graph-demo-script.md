# Sales Context Graph Demo Script

**Format:** 2:30 narrated product walkthrough.
**Audience:** AI platform engineers, enterprise architects, sales-operations
leaders, and hiring managers.
**Data boundary:** Use synthetic accounts, opportunities, stakeholders, and
interactions only. Label the recording as synthetic if no CRM sandbox is used.

## Story

An agent must recommend the next sales action from account history, opportunity
state, stakeholder relationships, policy, and recent interactions. The graph
must explain the recommendation and protect the CRM write path.

| Time | Scene | On screen | Voiceover |
|---|---|---|---|
| 0:00-0:15 | Business question | Synthetic-data banner, account, opportunity, and question. | "This demo asks what the next account action should be, using structured sales context rather than an isolated chat history." |
| 0:15-0:35 | Account context | Account, opportunity, stakeholders, contacts, and recent interactions connected in the graph. | "The Context Graph connects the account to its opportunity, stakeholders, meetings, emails, risks, and prior decisions. Every relationship is tenant-scoped and time-aware." |
| 0:35-0:55 | Evidence and provenance | Source interaction cards with timestamps, source IDs, confidence, and provenance links. | "The recommendation is grounded in dated interactions and source records. Provenance stays attached as context is expanded and summarized." |
| 0:55-1:15 | Policy-aware next action | Policy node, entitlement check, and ranked next-action candidates. | "The router compares candidate skills against account policy, opportunity stage, stakeholder role, and entitlement. Unsupported actions are rejected before the model can suggest them." |
| 1:15-1:35 | Recommendation | Ranked next action with evidence citations, policy reason, confidence, and decision trace ID. | "The agent recommends one next action and explains why. The response exposes the evidence, policy linkage, confidence, and trace identity needed for review." |
| 1:35-2:05 | Safe CRM write | Dry-run preview showing command ID, expected version, requested fields, approval requirement, and receipt. | "A write begins as a dry-run. The preview shows exactly what would change, checks optimistic concurrency, and requires an entitled human approval." |
| 2:05-2:20 | Replay and compensation | Approved write, idempotent replay, stale-version rejection, and compensation receipt. | "A replay does not duplicate the operation. A stale version is refused, and a compensating action creates a new immutable receipt while preserving the original history." |
| 2:20-2:30 | Close | Account-to-decision-to-receipt graph. | "The Sales Context Graph turns scattered CRM activity into an explainable, policy-aware, and safely actionable decision." |

## Recording checklist

- Show synthetic-data disclosure and tenant identity.
- Keep account, opportunity, stakeholder, interaction, policy, and decision
  identifiers visible but never display real customer data.
- Capture one successful dry-run, one approved write, one idempotent replay,
  and one stale-version rejection.
- End on the receipt and provenance links, not only on the generated answer.
