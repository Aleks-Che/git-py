"""Core layer: pure-Python Git logic on top of pygit2.

Hard rule (docs/DEVELOPMENT_RULES.md): modules in this package MUST NOT
import PySide6. Core stays UI-agnostic so it can be unit-tested without
a running Qt event loop.
"""
