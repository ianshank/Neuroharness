"""The two digests the harness decides with.

``FR-04``, ADR-0015 and ADR-0020 split one identity into two, because a single
digest could not answer both of the questions the harness asks.

*What is being asked, by whom, in which environment, under which rule set* is
the **proposal digest**. Approvals bind to it (``FR-40``), and an approval may
sit pending for up to a day, so this digest has exactly one job: change when
what the human agreed to changes, and not otherwise. It is therefore a narrow
projection -- see :data:`PROPOSAL_DIGEST_PROJECTION`, where every inclusion and
every exclusion is justified, since both directions are security decisions.

*Which single evaluation this was* is the **envelope digest**: the whole
envelope, facts, fetch times, repair iteration and all. Decision tokens bind to
it, and the broker recomputes it over what it is about to execute (``FR-21``).
That is the check that closes the time-of-check/time-of-use gap, and it only
closes it because the facts are inside the digest.

The module takes plain mappings rather than the envelope model on purpose. The
broker recomputes a digest over bytes it received, possibly under a different
schema version than the gateway that produced them; making it construct a model
first would make the digest depend on the model's parsing behaviour, so two
components with different model versions could disagree about a document neither
of them changed. Staying at the mapping level also lets a digest be recomputed
from an archived record long after the model that produced it has moved on.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from typing import Any, Final

from neuroharness.canonical.jcs import (
    DEFAULT_MAX_DEPTH,
    JSONValue,
    canonical_string,
    canonicalize,
)
from neuroharness.errors import CanonicalizationError
from neuroharness.models.common import Digest
from neuroharness.observability.logging import get_logger

__all__ = [
    "PROPOSAL_DIGEST_PROJECTION",
    "SAFE_INTEGER_BOUND",
    "digest_bytes",
    "digest_value",
    "envelope_digest",
    "proposal_digest",
    "renders_as_unsafe_integer",
]

_LOGGER = get_logger(__name__)

#: The fields the proposal digest covers, as paths into the envelope.
#:
#: This is a constant rather than a few lines of attribute access because it is
#: the definition of what a human approval authorises. Written as data it can be
#: read, reviewed and compared against the specification by someone who does not
#: read Python; written as logic, a field could be added or dropped inside a
#: refactor and nothing but a fixture would notice.
#:
#: Included, because changing any of them changes what the approver agreed to:
#:
#: * ``proposal.tool``, ``proposal.intent``, ``proposal.arguments`` -- the ask
#:   itself. These are the only parts of the agent-authored proposal that reach
#:   policy at all (``INV-01``, ``FR-12``).
#: * ``context.actor.agent_id``, ``actor.principal``, ``actor.delegation_chain``
#:   -- by whom, and through which chain of delegation. An approval that
#:   survived a change of principal or an extra delegation hop would be a
#:   confused-deputy grant (``FR-46``, ``SEC-13``).
#: * ``context.actor.environment`` -- "deploy to staging" and "deploy to
#:   production" are different asks, and the difference is the one an approver
#:   cares most about.
#: * ``context.policy_bundle.digest`` -- the rule set the disclosure was written
#:   under. An approval must not outlive the rules that justified requesting it
#:   (``FR-84``). The digest alone, not the whole reference: ``version`` is a
#:   label for the same bytes.
#:
#: Excluded, each for a reason, because a too-wide projection silently voids
#: approvals and a too-narrow one silently reuses them:
#:
#: * ``proposal.claims`` -- agent-controlled and never evaluated (``INV-08``).
#:   Including them would let an agent void its own pending approval, or churn
#:   one, by editing text that no rule ever reads.
#: * ``actor.agent_version`` and ``actor.model_identity`` -- deployment churn.
#:   A redeploy or a model upgrade between request and approval is unrelated to
#:   what was asked, and voiding a day-old human approval over it trains
#:   approvers to distrust the mechanism.
#: * the whole of ``context.action_class`` -- ``mode``, ``repair_budget``,
#:   ``approvable`` and ``escalate_on`` are mutable operational knobs. Promoting
#:   a class from ``shadow`` to ``enforce`` must not void the approvals that
#:   made the promotion safe, and the rule set is already bound through the
#:   bundle digest above.
#: * facts, timestamps, trace and session identifiers, ``repair_iteration`` and
#:   batch position -- all evaluation-scoped. Binding them here is exactly the
#:   defect ADR-0015 removed; that is what the envelope digest is for.
#:
#: Changing this tuple changes which approvals survive which edits, so it moves
#: only with ``FR-04`` and a new ADR (ADR-0020 supersedes the wider projection
#: ADR-0015 described; ADR-0015 itself supersedes ADR-0008).
PROPOSAL_DIGEST_PROJECTION: Final[tuple[tuple[str, ...], ...]] = (
    ("proposal", "tool"),
    ("proposal", "intent"),
    ("proposal", "arguments"),
    ("context", "actor", "agent_id"),
    ("context", "actor", "principal"),
    ("context", "actor", "delegation_chain"),
    ("context", "actor", "environment"),
    ("context", "policy_bundle", "digest"),
)

# Labels for the debug log, so one digest can be told from another in a trace
# without the log line having to carry the document.
_KIND_BYTES: Final[str] = "bytes"
_KIND_VALUE: Final[str] = "value"
_KIND_PROPOSAL: Final[str] = "proposal"
_KIND_ENVELOPE: Final[str] = "envelope"


def _sha256(data: bytes, kind: str) -> Digest:
    """Hash ``data`` and record that it happened.

    The log line carries the digest, the length and the kind, and never the
    bytes. Envelopes hold arguments and fact values; at debug level those would
    be the most sensitive text the harness handles (``NFR-18``). The digest
    alone is still enough to reconcile a mismatch, because a mismatch is
    precisely the case where two components computed different digests over
    documents they each already hold.
    """
    digest = Digest.from_hex(hashlib.sha256(data).hexdigest())
    _LOGGER.debug(
        "canonical.digest_computed", digest=str(digest), byte_length=len(data), kind=kind
    )
    return digest


def digest_bytes(data: bytes) -> Digest:
    """Digest bytes that are already canonical, or that have no canonical form.

    For input that is not a JSON value at all: the raw request bytes a
    ``SCHEMA_INVALID`` denial is recorded against (``FR-02``), a policy bundle,
    a decision-record body.
    """
    return _sha256(data, _KIND_BYTES)


#: Largest integer that survives an IEEE-754 double unchanged. RFC 8785 defines
#: number serialisation through ECMAScript, which has only doubles; this
#: implementation emits integers exactly so that an identifier is never rounded
#: while computing the digest that authorises acting on it (``ADR-0021``).
#: Outside this range the two encodings disagree, so a digested document may not
#: carry such a value: it travels as a string instead.
SAFE_INTEGER_BOUND: Final[int] = 2**53 - 1


#: A canonical number that a JSON parser reads back as an integer rather than
#: as a float. RFC 8785 emits a plain digit string for any value whose
#: ECMAScript form has no fraction and no exponent, and that string parses as an
#: ``int`` on the way back in -- whichever Python type produced it.
_INTEGER_LITERAL_RE: Final[re.Pattern[str]] = re.compile(r"-?[0-9]+")


def renders_as_unsafe_integer(value: int | float) -> bool:
    """True when ``value``'s canonical form is an out-of-range integer literal.

    The test is on the canonical *form*, never on the Python type, and that
    distinction is the whole point of this function. ``1e20`` is a ``float`` in
    Python, but RFC 8785 serialises it as ``100000000000000000000``; the broker
    parses those bytes back into an ``int`` before recomputing the digest
    (``FR-21``). A rule phrased over ``isinstance(value, int)`` therefore admits
    the document at the gateway and refuses it at the broker, which is a
    fail-closed refusal of an envelope nobody tampered with -- and one that only
    appears in production, on the side of the system that has already decided.

    Non-finite floats return ``False``: they have no canonical form at all, and
    :func:`~neuroharness.canonical.jcs.canonicalize` rejects them with the path
    that located them, which is the better error.
    """
    if isinstance(value, int):
        return abs(value) > SAFE_INTEGER_BOUND
    if not math.isfinite(value):
        return False
    rendered = canonical_string(value)
    if not _INTEGER_LITERAL_RE.fullmatch(rendered):
        return False
    return abs(int(rendered)) > SAFE_INTEGER_BOUND


def _reject_unsafe_numbers(
    value: Any, path: str = "", *, depth: int = 0, max_depth: int = DEFAULT_MAX_DEPTH
) -> None:
    """Refuse a digested document that a conforming JCS encoder would read differently.

    This restriction belongs here and not in :func:`canonicalize`. Canonicalising
    is serialisation; digesting is *identification*, and identity is the thing
    two implementations must agree on. Catching it at the boundary turns a
    silent cross-implementation digest mismatch - discovered much later, when a
    token inexplicably fails to verify - into a named rejection at the document
    that caused it.

    ``max_depth`` mirrors the canonicaliser's own bound and defaults to the same
    value. Without it this walk runs *first* and unbounded, so a document nested
    past the interpreter's stack limit raised ``RecursionError`` - a bare
    ``Exception`` that no caller catches and no reason code names - instead of
    the :class:`~neuroharness.errors.CanonicalizationError` the depth guard
    exists to produce. An error that escapes the fail-closed hierarchy is an
    inability to evaluate that does not stop execution (Constitution Art. II).
    """
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if renders_as_unsafe_integer(value):
            raise CanonicalizationError(
                f"number at {path or '<document root>'} canonicalises to an integer "
                f"outside the range that survives IEEE-754 ({SAFE_INTEGER_BOUND}); a "
                "digested document must carry such a value as a string (ADR-0021)"
            )
        return
    if isinstance(value, (Mapping, list, tuple)):
        if depth >= max_depth:
            raise CanonicalizationError(
                f"value at {path or '<document root>'} exceeds the maximum nesting "
                f"depth of {max_depth}"
            )
        members = (
            value.items()
            if isinstance(value, Mapping)
            else ((str(index), item) for index, item in enumerate(value))
        )
        for key, item in members:
            _reject_unsafe_numbers(item, f"{path}/{key}", depth=depth + 1, max_depth=max_depth)


def digest_value(value: JSONValue, *, max_depth: int = DEFAULT_MAX_DEPTH) -> Digest:
    """Canonicalise ``value`` and digest the result.

    Raises :class:`~neuroharness.errors.CanonicalizationError` if the value has
    no canonical form, or if it carries a number that canonicalises to an
    integer outside the IEEE-754 safe range (``ADR-0021``). That propagates
    rather than being softened into a
    sentinel digest: a value with no identity cannot be evaluated, and Article
    II says an inability to evaluate ends in no execution.
    """
    _reject_unsafe_numbers(value, max_depth=max_depth)
    return _sha256(canonicalize(value, max_depth=max_depth), _KIND_VALUE)


def _project(
    source: Mapping[str, Any], projection: tuple[tuple[str, ...], ...]
) -> dict[str, Any]:
    """Rebuild the subset of ``source`` named by ``projection``.

    The result keeps the original nesting, so the projected document is a
    sub-document of the envelope and the digest is defined over the shape the
    specification names rather than over a flattened invention of this function.

    A missing path is an error, never an omitted key. Silently projecting seven
    of eight fields would produce a perfectly valid digest of the wrong thing,
    and an approval would then bind to a proposal whose principal or policy
    bundle nobody recorded.
    """
    projected: dict[str, Any] = {}
    for path in projection:
        cursor: Any = source
        for depth, key in enumerate(path):
            if not isinstance(cursor, Mapping) or key not in cursor:
                located = "/" + "/".join(path[: depth + 1])
                raise CanonicalizationError(
                    f"cannot project {located}: the proposal digest is defined "
                    "over " + ", ".join("/" + "/".join(p) for p in projection)
                    + ", and every one of them must be present"
                )
            cursor = cursor[key]
        target = projected
        for key in path[:-1]:
            branch = target.setdefault(key, {})
            if not isinstance(branch, dict):
                raise CanonicalizationError(
                    f"projection {projection!r} is self-inconsistent at {key!r}"
                )
            target = branch
        target[path[-1]] = cursor
    return projected


def proposal_digest(
    envelope_like: Mapping[str, Any], *, max_depth: int = DEFAULT_MAX_DEPTH
) -> Digest:
    """Digest what an approver is actually approving (``FR-04``, ADR-0020).

    Stable across a fact refresh, a new ``proposed_at``, an agent redeploy, a
    model upgrade and an action-class tuning change. That stability is what lets
    a human approval outlive the sixty-second facts it was granted beside
    (ADR-0015) without ever authorising something other than what was shown in
    the disclosure (``FR-43``).

    Changes the moment the tool, the intent, the arguments, the agent, the
    principal, the delegation chain, the environment or the policy bundle
    changes -- which is what stops an approval being piggybacked onto a
    different request (``FR-41``, ``FR-46``).

    Raises :class:`~neuroharness.errors.CanonicalizationError`, naming the
    missing path, if any projected field is absent: an envelope that cannot
    supply them has no proposal identity, so nothing can be approved against it.

    No domain-separation tag is prepended. The specification defines this digest
    as JCS over exactly the projected document, published test vectors and
    non-Python components must reproduce it byte for byte, and the two digests
    are over documents of different shapes drawn from the same envelope, so
    neither can be passed off as the other.
    """
    projected = _project(envelope_like, PROPOSAL_DIGEST_PROJECTION)
    _reject_unsafe_numbers(projected, max_depth=max_depth)
    return _sha256(canonicalize(projected, max_depth=max_depth), _KIND_PROPOSAL)


def envelope_digest(
    envelope_like: Mapping[str, Any], *, max_depth: int = DEFAULT_MAX_DEPTH
) -> Digest:
    """Digest *this one evaluation*: the whole envelope, facts and times included.

    Deliberately fragile. Any change to any field -- a refreshed fact, a new
    fetch timestamp, a repair iteration, an agent version -- yields a different
    digest, so a token issued against one evaluation cannot authorise another
    (``FR-20``, ``FR-21``). That fragility is the property, not a cost of it.
    """
    _reject_unsafe_numbers(envelope_like, max_depth=max_depth)
    return _sha256(canonicalize(envelope_like, max_depth=max_depth), _KIND_ENVELOPE)
