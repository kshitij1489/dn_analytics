"""
Product "core" identity for silent-reuse detection.

A PetPooja itemid / addonid is treated by the clustering pipeline as a stable
key: the first order that carries an id establishes its menu-item mapping, and
every later order with the same id inherits that mapping via exact match
(services/clustering_service.py). That is correct as long as the id keeps
pointing at the same product.

PetPooja occasionally recycles an id onto a *different* product (observed:
itemid 1283886195 served "Eggless Chocolate Ice Cream" for months, then
"Just Chocolate (Andra) Ice Cream" — silently booked as the former because the
mapping was already verified and verified rows never re-enter the resolutions
queue).

String similarity cannot separate that real reuse ("Just Chocolate" vs
"Eggless Chocolate": different product, high word overlap) from a cosmetic
relabel ("Banoffee" vs "Eggless Banoffee": same product, lower overlap) — the
two live in the same, even inverted, distance space. Only a human who knows the
menu can decide. So detection is deliberately NOT a similarity threshold; it is
an identity change: reduce a raw name to a normalized "core" (type + flavor,
with known-cosmetic tokens stripped) and flag when a *new* core appears on an id
that already carried a different one. Cosmetic relabels collapse to the same
core and stay silent; a genuinely different product produces a new core and is
surfaced for human triage.
"""

from __future__ import annotations

import re
from typing import Optional

from utils.clean_order_item import clean_order_item_name

# Tokens that vary between labels of the SAME product and must not, on their own,
# make a name look like a different product. "eggless" (recipe labels drift in
# and out of it), the trailing "ice cream" noun, and seasonal "navratri" tags.
_COSMETIC_WORD_RE = re.compile(r"\b(?:eggless|navratri)\b")
_ICE_CREAM_RE = re.compile(r"\bice\s+cream\b")
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_WS_RE = re.compile(r"\s+")


def _flavor_core(cleaned_name: str) -> str:
    """Reduce an already-cleaned menu name to its bare flavor identity."""
    s = (cleaned_name or "").lower()
    s = s.replace("&", " and ")
    s = _PARENTHETICAL_RE.sub(" ", s)   # drop "(andra)", stray "(navratri)", etc.
    s = _ICE_CREAM_RE.sub(" ", s)       # drop the trailing product noun
    s = _COSMETIC_WORD_RE.sub(" ", s)   # drop eggless / navratri
    s = _NON_ALNUM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def mapping_core_key(raw_name: str) -> Optional[str]:
    """
    Deterministic core identity for an incoming raw order-item / addon name.

    Returns a "<type>|<flavor core>" string, or None for an empty name. The type
    prefix means a genuine category change (e.g. Dessert -> Ice Cream on one id)
    also registers as a new core even when the flavor words match.

    Pure and side-effect free; safe to call on the ingest hot path.
    """
    if not raw_name or not str(raw_name).strip():
        return None

    cleaned = clean_order_item_name(str(raw_name))
    flavor = _flavor_core(cleaned.get("name", ""))
    item_type = (cleaned.get("type") or "").strip().lower()

    if not flavor:
        # Degenerate name (all cosmetic tokens); fall back to the cleaned string
        # so it still produces a stable, comparable key instead of None.
        flavor = _WS_RE.sub(" ", _NON_ALNUM_RE.sub(" ", (cleaned.get("name") or "").lower())).strip()

    return f"{item_type}|{flavor}"
