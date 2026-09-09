"""Grounded local generator for Talantul în Negoț biblical contest tests."""

from .generation import GenerationError
from .selection import SelectionError
from .validation import ValidationError


# Everything the caller can get wrong: an unparseable chapter range, a book the
# corpus lacks, a selection too thin to build a test from. These carry messages
# written for the person asking for the test, so they are shown as-is. Anything
# else is a defect in this program.
#
# Defined here, once, because both entrypoints classify errors by it and a
# second copy would drift: the web app must never echo an internal fault to a
# browser, and the CLI must never bury a fixable typo under a stack trace.
USER_ERRORS = (SelectionError, GenerationError, ValidationError)

__all__ = ["USER_ERRORS", "GenerationError", "SelectionError", "ValidationError"]
