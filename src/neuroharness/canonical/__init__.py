"""Canonical JSON and the digests taken over it.

One import site for the byte-level identity of a document, so that no component
can accidentally define its own. See :mod:`neuroharness.canonical.jcs` for the
canonical form and :mod:`neuroharness.canonical.digest` for the two digests the
harness decides with.
"""

from neuroharness.canonical.digest import (
    PROPOSAL_DIGEST_PROJECTION,
    digest_bytes,
    digest_value,
    envelope_digest,
    proposal_digest,
)
from neuroharness.canonical.jcs import (
    DEFAULT_MAX_DEPTH,
    JSONValue,
    canonical_string,
    canonicalize,
    parse_json,
    reject_duplicate_keys,
)

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "JSONValue",
    "PROPOSAL_DIGEST_PROJECTION",
    "canonical_string",
    "canonicalize",
    "digest_bytes",
    "digest_value",
    "envelope_digest",
    "parse_json",
    "proposal_digest",
    "reject_duplicate_keys",
]
