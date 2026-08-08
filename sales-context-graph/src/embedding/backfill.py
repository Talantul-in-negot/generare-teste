"""One-off batch job: populate contact_embeddings_v1 (src/graph/schema.py)
for every Contact in one workspace.

Phase 7 (docs/evaluation.md's B5 item) -- hard-gated on Phase 1's vector
tenant-filter fix (src/resolution/candidates.py::vector_candidates())
already being live: populating this index before that fix landed would
have turned a latent cross-tenant leak into a live one. That fix shipped
and is verified by tests/security/test_vector_candidates_tenant_isolation.py;
this job assumes it's already in place and does not re-check it itself.

Backfill only, not a write-time hook: population happens once per
workspace, run explicitly by an operator
(`python -m src.embedding.backfill <workspace_id>`). A write-time hook
that embeds every new/updated Contact automatically is a legitimate fast-
follow once this path is verified correct in practice, not required here.

Explicitly scoped to one workspace per invocation, never "every workspace
this cluster has" -- there is no cross-tenant listing anywhere else in
this codebase (tenant_query() structurally requires a workspace_id), and a
backfill script silently touching every tenant at once would be the one
exception to that discipline. An operator runs this once per workspace
they want backfilled.

Embeds Contact.name specifically, not some broader "about this contact"
text -- contact_embeddings_v1's actual purpose (src/resolution/
candidates.py::vector_candidates(), called from entity resolution) is
semantic/fuzzy name matching against a mention's surface text, the same
job src/resolution/pipeline.py's in-memory embedding scoring already does
with SentenceTransformerEmbeddingProvider. A name is the right text to
embed for that purpose.
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from src.core.config import get_settings
from src.embedding.openai_embedding_provider import (
    EmbeddingNotConfiguredError,
    OpenAIEmbeddingProvider,
)
from src.embedding.provider import EmbeddingProvider
from src.graph.execution import GraphExecutor
from src.graph.repositories.crm_repository import CrmRepository

log = structlog.get_logger(__name__)

_PAGE_SIZE = 100  # matches CrmRepository.list_contacts' default page size


async def backfill_workspace(
    workspace_id: str, *, executor: GraphExecutor | None = None, provider: EmbeddingProvider | None = None
) -> int:
    """Returns the number of Contacts embedded. Raises
    EmbeddingNotConfiguredError if EMBEDDING_API_KEY isn't set and no
    `provider` override was given -- never silently skips or writes a
    fabricated vector. `provider` is injectable (same DI pattern as
    src/resolution/pipeline.py's embedding_provider param) so tests can
    substitute a stub instead of making real OpenAI calls; production
    callers (the __main__ entry point below) always take the real one.
    """
    provider = provider or OpenAIEmbeddingProvider(api_key=get_settings().embedding_api_key)

    repo = CrmRepository(executor or GraphExecutor())
    embedded = 0
    offset = 0
    while True:
        contacts = await repo.list_contacts(workspace_id, limit=_PAGE_SIZE, offset=offset)
        if not contacts:
            break
        # One batched embed() call per page, never one call per contact --
        # same N+1-avoidance principle as everywhere else in this repo.
        vectors = await provider.embed([c.name for c in contacts])
        for contact, vector in zip(contacts, vectors, strict=True):
            await repo.set_contact_embedding(workspace_id, contact.contact_id, vector)
        embedded += len(contacts)
        log.info("embedding_backfill.page_complete", workspace_id=workspace_id, embedded=embedded)
        if len(contacts) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return embedded


async def _main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m src.embedding.backfill <workspace_id>", file=sys.stderr)
        sys.exit(1)
    workspace_id = sys.argv[1]
    try:
        total = await backfill_workspace(workspace_id)
    except EmbeddingNotConfiguredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"embedded {total} contact(s) in workspace {workspace_id}")


if __name__ == "__main__":
    asyncio.run(_main())
