# Lazy package — consumers import directly from submodules.
# Eager imports were removed to avoid pulling sentence_transformers into the
# API image. The `__all__` list that named LocalSearch, GlobalSearch,
# HybridRetriever and ContextBuilder was left behind, which advertised an API
# this module cannot resolve: `from graphrag.retrieval import *` raised
# AttributeError because none of those names are imported here. Declaring no
# `__all__` is the honest description of a lazy package.
