"""UI layer: passive Qt widgets; read ViewModel state, call ViewModel methods.

Hard rule (docs/DEVELOPMENT_RULES.md): widgets in this package do not
import anything from ``core/`` and do not store Git operation state.
The Stage 0 widgets below are placeholder shells — they show static
text and have no signal wiring yet.
"""
