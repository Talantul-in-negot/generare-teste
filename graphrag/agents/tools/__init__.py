"""Tool functions registered with :class:`graphrag.agents.tool_policy.ToolPolicy`.

``evaluation_tools`` (score_answer / log_kpi) was removed with the rest of the
ADK scaffolding: it was only ever referenced from ``EvaluationAgent._tools()``,
which never ran because ``google-adk`` is not a dependency. The remaining
modules are live — ToolPolicy.from_defaults() registers them, and
``POST /agent/tool`` dispatches through it.
"""

from graphrag.agents.tools.neo4j_tools import get_community, get_neighbors, search_graph
from graphrag.agents.tools.retrieval_tools import global_search, local_search

__all__ = [
    "search_graph", "get_community", "get_neighbors",
    "local_search", "global_search",
]
