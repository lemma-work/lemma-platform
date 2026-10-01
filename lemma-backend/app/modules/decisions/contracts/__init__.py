"""The decisions module's published surface.

A leaf: importing it costs nothing. The operations live in `contracts.decide`,
which reaches the engines, so a module that only names the types does not pay
for them.
"""
