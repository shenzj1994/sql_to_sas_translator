"""sql_to_sas_translator package initialization.

Expose the `translate` API at package level for convenience.
"""
from .translator import translate

__all__ = ["translate"]
