"""Async versions of Cod Sync's modules, which will replace the sync ones.

Each module here mirrors the sync module of the same name and shares its
exceptions, result types and pure helpers. Callers move over package by
package; the sync modules go away once nothing uses them.
"""
