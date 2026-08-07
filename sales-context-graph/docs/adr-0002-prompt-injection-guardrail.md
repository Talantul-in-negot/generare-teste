# ADR-0002 — Prompt-injection guardrail (belt-and-suspenders)

**Status:** Implemented (feature-flagged, log-only by default)
**Date:** 2026-08-07

## Context

`docs/evaluation.md`'s external architecture-review cross-check evaluated
a generic industry brief's recommendation to add a guardrail
classifier (NeMo Guardrails/Llama Guard-style) in front of transcript
extraction, defending against prompt injection embedded in call
transcripts. That analysis's own conclusion, unchanged by this ADR:

> The existing defense is structural: transcripts are delimited data, the
> extractor is given no tools, and outputs are schema-validated. A
> classifier in front adds a probabilistic filter to a problem currently
> handled deterministically.

`src/extraction/prompt.py`'s `SYSTEM_INSTRUCTIONS` delimits the transcript
as a fenced `<transcript>` block, explicitly instructs the model to treat
embedded text as content to extract from — never as instructions — and
states the model has no tools. `tests/security/test_prompt_injection_
fixture.py` proves this holds across three independent layers: the prompt
itself delimits correctly, a "compromised" model that echoes an injected
instruction back can only ever produce ordinary extracted data (no tool
surface to actually act on an instruction), and Pydantic schema validation
discards any field outside `ExtractionResult`'s schema regardless of what
the model returns. This is a real, verified, deterministic defense — not
an assumption.

The user reviewing `docs/evaluation.md` explicitly, and after this
rejection was raised directly, chose to implement the guardrail anyway as
part of "implement literally everything in this document, including the
items flagged as premature." This ADR documents that decision and the
minimal, lowest-risk form it took — not a reversal of the original
analysis, which stands: this system's structural defense was, and remains,
judged adequate at this vertical slice's scale.

## Decision

Add `src/extraction/guardrail.py`: a heuristic regex/keyword scan for
common injection phrasings (role-override language, "ignore previous
instructions," prompt-reveal requests, delimiter-escape attempts) run in
addition to — never in place of — the structural defenses above, on every
extraction window before it's sent to the LLM.

### Enforcement mode: log-only by default

`GUARDRAIL_ENFORCEMENT_MODE` (`log_only` | `block`, default `log_only`).
A flagged window is logged (`extraction.guardrail_flagged`) and metriced
(`scg_guardrail_flag_total`, `src/core/telemetry.py`) but extraction
proceeds normally. This default is deliberate: a probabilistic heuristic
classifier becoming a new hard-failure mode on top of already-adequate
deterministic defenses is a worse trade than staying observability-only
until real flagged-window data exists to justify blocking. `block` mode
exists and is fully implemented (`GuardrailBlockedError`) for an operator
who has reviewed real flag data and wants to opt in, but it is not the
default and this ADR does not recommend flipping it without that review.

## Consequences

- **Positive:** the item is closed for anyone reviewing `docs/evaluation.md`
  looking for "was the brief's guardrail recommendation acted on" — yes,
  in a form that can't regress the existing structural defense (additive,
  never a replacement) and can't silently start rejecting legitimate sales
  calls (log-only default).
- **Negative:** a heuristic pattern list is inherently incomplete and will
  have both false positives (ordinary transcript content that happens to
  match a pattern — harmless in log-only mode) and false negatives (a
  differently-worded injection attempt the patterns don't catch — no
  regression versus before this ADR, since the structural defense doesn't
  depend on this catching anything).
- **Deferred deliberately:** no ML-based classifier, no allow/deny-list
  tuning workflow, no per-workspace enforcement-mode override. None of
  these are justified without the "block"-mode graduation this ADR
  explicitly doesn't recommend yet.

## Not done in this ADR

Semantic/embedding-based injection detection, rate-limiting or
per-workspace guardrail tuning, and any automatic promotion from
`log_only` to `block` based on flag volume all remain out of scope.
