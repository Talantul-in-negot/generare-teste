# Context Graph — E2E Video Script

> **Runtime:** 3:00–3:30  
> **Scenario:** determine whether a governed business action is allowed  
> **Audience:** product, data governance, and AI architecture stakeholders  
> **Tone:** cinematic, precise, quietly confident

This section is designed to drop into any domain demo after the knowledge graph
and retrieval scenes. The WPP campaign-placement scenario is the reference
example, but the same script works for automotive quality, aerospace compliance,
privacy, or financial policy decisions. It demonstrates the Context Graph as the
memory of a governed decision, not as another search screen.

## Before Recording

Open these tabs:

1. The domain demo UI at `http://localhost:8000/demo`.
2. Neo4j Browser at `http://localhost:7474`.
3. The generated movie at `docs/presentation/context_graph_e2e_narrated.mp4`.
4. The Context Graph API documentation or trace response.

Have the following data ready in the selected tenant:

- A domain document statement describing the proposed action.
- A policy statement describing the controlling restriction.
- The applicable policy version, for example `privacy-v4.2`.
- Three options: allow, deny, escalate for human review.
- One completed trace with a manifest hash and linked observations.

Do not display API keys, hidden chain-of-thought, or fabricated live counts.

## Scene 0 — Ingestion Completes the Workflow [0:00–0:25]

**On screen:** A short, pre-recorded terminal segment showing the real repository
ingestion command completing. Keep the final file and chunk summary visible, but
do not show configuration or environment values.

```powershell
python scripts/ingest_corpus.py --tenant marketing --commit
```

**Voiceover:**

> "The workflow starts before the question. Documents enter the knowledge graph
> through the ingestion pipeline. The pipeline chunks source material, extracts
> entities and relationships, creates embeddings, and preserves document and
> chunk identity so later retrieval can verify exactly where an answer came from."

**Action:** Show the terminal completion summary and a final document/chunk count.
Then transition from the Knowledge Graph evidence layer into the Context Graph
decision layer.

**Accuracy note:** The GitHub command `python -m app.ingest ... --subdir` is not
an entry point in this repository. Use the repository command above, or show an
equivalent local corpus ingestion recording.

## Scene 1 — The Question Becomes a Case [0:25–0:50]

**On screen:** Domain title card. Fade into the governed-action question.

**Voiceover:**

> "A normal RAG answer ends when the model returns text. In a governed
> environment, that is where the important part begins. We start with a case:
> determine whether this action is allowed under the current policy."

**Action:** Enter or reveal the question in the domain demo UI. Show the tenant
and case identifier. Do not pause on raw prompt text.

## Scene 2 — The Agent Run Is Captured [0:50–1:15]

**On screen:** Transition from the answer UI to the Context Graph movie or a
Neo4j view showing `CGCase` connected to `CGAgentRun`.

**Voiceover:**

> "The system opens a durable case and starts an agent run. This gives the
> request an identity, a tenant boundary, and a lifecycle. The run can now be
> connected to the context it used, the tools it called, and the decision it
> eventually produced."

**Action:** Highlight:

```text
CGAgentRun -[:ADDRESSES]-> CGCase
```

Show status moving from `running` to `completed` only later in the story.

## Scene 3 — Evidence Is Assembled [1:15–1:45]

**On screen:** Show the Campaign Brief and Data Privacy Policy flowing into a
manifest. Animate statement, chunk, and document nodes joining the graph.

**Voiceover:**

> "Retrieval finds the evidence: the source document, the policy statement, and
> the exact document and chunk versions behind them. The Context Graph records
> those references explicitly. It does not merely say that the model searched;
> it records what was available to the model at decision time."

**Action:** Highlight:

```text
CGContextManifest -[:INCLUDED_STATEMENT]-> Statement
CGContextManifest -[:INCLUDED_CHUNK]-> Chunk
CGContextManifest -[:INCLUDED_DOCUMENT]-> Document
```

Call out provenance, valid-time, transaction-time, ontology version, retrieval
mode, and model/prompt version in the manifest panel.

## Scene 4 — The Manifest Locks the Moment [1:45–2:10]

**On screen:** Zoom into the manifest. Reveal the SHA-256 hash resolving from
the canonical serialized content.

**Voiceover:**

> "This is the key difference between context and a loose conversation log.
> The manifest captures the inference moment: evidence, policy versions,
> retrieval configuration, model version, prompt version, temporal boundaries,
> and tool observations. Its canonical content produces an integrity hash.
> Reconstruct the same context, and the hash must match. Change a material
> input, and it must not."

**Action:** Show a compact manifest summary, then animate:

```text
manifest content -> canonical JSON -> SHA-256 -> integrity_hash
```

**Voiceover emphasis:**

> "Structured rationale is stored. Hidden chain-of-thought is not."

## Scene 5 — Tools Produce Auditable Observations [2:10–2:30]

**On screen:** Show a tool call for policy evaluation, followed by a structured
observation node.

**Voiceover:**

> "When the agent uses a tool, the call and its observation become part of the
> trace. We preserve the auditable result, not private internal reasoning:
> which policy was evaluated, what rule controlled, and what constraint was
> returned."

**Action:** Highlight:

```text
CGAgentRun -[:MADE_TOOL_CALL]-> CGToolCall
CGToolCall -[:PRODUCED]-> CGObservation
```

## Scene 6 — Alternatives Make the Decision Governed [2:30–3:00]

**On screen:** Three option nodes appear: `ALLOW`, `DENY`, `ESCALATE`.
The policy node illuminates `DENY`; rejected options retain reason codes.

**Voiceover:**

> "The agent does not write an unexplained verdict. It records the alternatives
> it considered. Allow is rejected because the controlling policy rule is not
> satisfied. Escalation is retained as a possible path, but the available
> evidence is sufficient for a policy decision. Deny is selected, with a concise
> structured rationale and explicit reason codes for the alternatives."

**Action:** Show:

```text
CGDecision -[:CONSIDERED]-> CGOption
CGDecision -[:SELECTED]-> CGOption
CGDecision -[:REJECTED]-> CGOption
CGDecision -[:SUPPORTED_BY]-> Statement
```

## Scene 7 — Policy Evaluation Is Linked [3:00–3:20]

**On screen:** Animate the applicable policy version and its evaluation result into the
decision. Show the policy version beside the selected option.

**Voiceover:**

> "The decision is also linked to the exact policy version and its evaluation.
> That matters when the policy changes. A later run can use a newer version and
> produce a different result without rewriting what happened here. The history
> remains append-only."

**Action:** Highlight:

```text
CGDecision -[:APPLIED_POLICY]-> CGPolicyVersion
CGDecision -[:HAS_POLICY_EVALUATION]-> CGPolicyEvaluation
```

## Scene 8 — Replay the Trace [3:20–3:45]

**On screen:** Neo4j Browser or trace API response. Traverse case → run →
manifest → evidence/policy → options → decision.

**Voiceover:**

> "Now we can answer the questions an enterprise actually asks: what case was
> handled, which run handled it, what evidence was available, which policy
> applied, which alternatives were considered, why they were rejected, which
> observations contributed, and whether the reconstructed manifest still
> matches its hash."

**Action:** Run a tenant-scoped trace query. Keep the result readable and show
the final `selected_option`, `reason_codes`, `policy_version`, and
`integrity_hash_valid: true`.

## Scene 9 — Closing Image [3:45–3:55]

**On screen:** Return to the animated graph. The decision node settles into a
calm gold state; evidence and policy remain visibly connected.

**Voiceover:**

> "The Knowledge Graph helps the system find what is true. The Context Graph
> records what the system knew, what it considered, and why it acted. That is
> the difference between an answer and an accountable decision."

## Recording Notes

- Use a slow zoom and avoid fast cursor movement.
- Keep the graph animation under the voiceover, not competing with it.
- Use one tenant and one domain scenario consistently throughout the scene.
- Never claim production scale from this demo; say "live-validated locally" when
  discussing infrastructure verification.
- If Neo4j is unavailable, use the generated MP4 and a deterministic saved trace
  response rather than inventing a live result.
