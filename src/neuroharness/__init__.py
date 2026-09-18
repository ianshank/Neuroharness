"""Neuroharness: a fail-closed policy-and-verification harness for agent tool calls.

The package is layered so that the parts carrying the constitution's guarantees
stay small and independently testable:

``reason``, ``errors``, ``defaults``, ``seams``, ``config``, ``observability``
    The contract layer. No decision logic, no I/O beyond logging.
``models``
    The wire vocabulary: envelopes, decision records, and the shared enums.
``canonical``
    Canonicalisation and the two digests that give an action its identity.
``registry``
    The signed source of every per-class policy value.
``resolve``
    The verdict procedure. A pure function over explicit inputs.
``tokens``, ``evidence``
    Binding a decision to exactly one execution, and making it replayable.

Nothing here reaches the network or a database; those arrive later through the
protocol seams in :mod:`neuroharness.seams` and the store protocols.
"""

from __future__ import annotations

from neuroharness.version import PACKAGE_VERSION as __version__

__all__ = ["__version__"]
