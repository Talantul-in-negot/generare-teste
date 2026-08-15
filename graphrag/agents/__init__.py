# Lazy package — consumers import directly from submodules.
# EvaluationAgent eagerly imports ragas+datasets; the eager imports were
# removed to keep the workers image lean. The stale `__all__` that still named
# IngestionAgent, QueryAgent and EvaluationAgent was removed with them — it
# made `from graphrag.agents import *` raise AttributeError.
