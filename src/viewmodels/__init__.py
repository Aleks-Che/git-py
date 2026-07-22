"""ViewModel layer: bridges Core and UI via Qt signals/slots.

ViewModels use PySide6 (so they can subclass ``QObject`` and emit
``Signal``) but must not know about specific widgets; they expose
properties/signals the passive widgets in ``src/ui/`` consume.
"""
