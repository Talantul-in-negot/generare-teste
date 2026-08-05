"""Increment 15 — the first *real* LLM wiring in this repo.

Everything before this increment ran on FixtureExtractionProvider: the
ExtractionProvider protocol and LlmExtractionProvider's injected `chat_fn`
existed (src/extraction/llm_provider.py) but nothing anywhere constructed a
chat_fn backed by an actual API. This package closes that gap in one place:

- chat.py            — builds a real Anthropic-backed ChatFn from Settings.
- json_completion.py — the generic bounded retry/repair/validate loop, lifted
                       from the pattern proven in llm_provider.py so every new
                       LLM consumer (nlq, narrative, stakeholder classification)
                       shares one tested implementation instead of three copies.

Design rule kept from llm_provider.py: `chat_fn` stays a plain injected async
callable, so every test in this repo runs against a deterministic stub and the
suite never needs an API key or network access.
"""
