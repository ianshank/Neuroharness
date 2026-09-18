"""Separating what the model said from what the harness knows (``FR-03``).

``FR-03``: context-shaped keys in the raw tool-call payload MUST be removed
before validation and listed in ``context.stripped_proposal_keys``.

The model already defends the *result* of this step and cannot perform it.
:class:`~neuroharness.models.envelope.Proposal` sets ``extra="forbid"``, so a
payload that still carries an ``actor`` or a ``context`` key raises rather than
being cleaned - which is correct, and means the stripping has to happen *before*
validation. Nothing did it. ``Context.stripped_proposal_keys`` has been a field
that nobody populates since the model was written.

**Why this matters more than it looks.** The keys being removed are the ones by
which a model would nominate its own identity, its own action class, its own
facts or its own session. Constitution Article I is "the model proposes; the
harness decides", and this function is where the proposing stops. An unstripped
``actor`` block is the model telling the harness who it is.

**The list is derived, not written down.** Anything in the raw payload that is
not a declared field of ``Proposal`` is context-shaped by definition. Hard-coding
``{"actor", "context", "action_class", ...}`` would mean that the day ``Proposal``
gains a field, a legitimate key starts being stripped - or worse, the day the
*envelope* gains a field, a new context-shaped key silently passes through.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from neuroharness.models.envelope import Proposal
from neuroharness.observability.logging import get_logger

__all__ = ["PROPOSAL_FIELDS", "StrippedProposal", "strip_context_keys"]

_LOG: Final = get_logger("neuroharness.envelope")

_EVENT_STRIPPED: Final[str] = "envelope.context_keys_stripped"

#: The only keys a proposal may carry, taken from the model rather than restated.
#: ``model_fields`` is pydantic's declared-field map, so this follows the model
#: automatically - including through an alias, which ``Proposal`` does not use
#: today and might.
PROPOSAL_FIELDS: Final[frozenset[str]] = frozenset(Proposal.model_fields)


@dataclass(frozen=True, slots=True)
class StrippedProposal:
    """The cleaned payload and the record of what was taken out of it.

    Both halves travel together because ``FR-03`` requires both: the envelope
    carries the cleaned proposal, and ``Context.stripped_proposal_keys`` carries
    the names. Returning only the cleaned payload would make the removal
    invisible, and a removal nobody recorded is indistinguishable from a payload
    that never carried the key - which is exactly the difference an auditor
    investigating an attempted identity nomination needs.
    """

    payload: Mapping[str, Any]
    stripped: tuple[str, ...]


def strip_context_keys(payload: Mapping[str, Any]) -> StrippedProposal:
    """Remove every key that is not a declared ``Proposal`` field.

    Sorted, so two payloads carrying the same stray keys in different orders
    produce the same ``stripped_proposal_keys`` and therefore the same envelope
    digest. An order-dependent list would give one proposal two identities
    (``FR-04``).

    Logged at warning, with the key *names* only. The names are the interesting
    signal - an ``actor`` key in a proposal is an attempted identity nomination,
    not a typo - and the values are model-authored text that has no business in
    an operator's log (``NFR-18``, ``SEC-07``).
    """
    stripped = tuple(sorted(key for key in payload if key not in PROPOSAL_FIELDS))
    if not stripped:
        return StrippedProposal(payload=dict(payload), stripped=())

    cleaned = {key: value for key, value in payload.items() if key in PROPOSAL_FIELDS}
    _LOG.warning(_EVENT_STRIPPED, stripped_keys=list(stripped), count=len(stripped))
    return StrippedProposal(payload=cleaned, stripped=stripped)
