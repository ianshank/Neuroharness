"""The action-class and resource-key registries (``FR-30``--``FR-35``).

The registry is the harness's only authority for per-class policy. Everything
the decision path needs to know about an action -- what arguments it takes, what
it can do to the world, which critics judge it, whether a human may approve it,
how long its token lives -- is loaded from a signed, versioned document rather
than written into code. That is what makes "no hardcoded values" structural
rather than aspirational: a policy knob that is not a field here does not exist.

Typical use::

    from neuroharness.registry import load_registry_file

    registry = load_registry_file("registry.json")          # digest-verified
    action_class = registry.resolve("deployment.apply", "deploy_service")
"""

from neuroharness.registry.loader import (
    UNSIGNED_FIELDS,
    DigestVerifier,
    NullVerifier,
    SignatureVerifier,
    canonical_bytes,
    compute_registry_digest,
    load_registry,
    load_registry_file,
)
from neuroharness.registry.models import (
    FACT_ESCALATION_REASONS,
    ActionClass,
    ActionClassRegistry,
    BatchPolicy,
    ClassKey,
    ConnectorKind,
    CriticRef,
    FactRequirement,
)
from neuroharness.registry.resource_keys import (
    MAX_RESOURCE_KEY_LENGTH,
    RESOURCE_KEY_PATTERN,
    ResourceKeyRegistry,
    is_resource_key,
    render_template,
    template_placeholders,
)

__all__ = [
    # models
    "ActionClass",
    "ActionClassRegistry",
    "BatchPolicy",
    "ClassKey",
    "ConnectorKind",
    "CriticRef",
    "FactRequirement",
    "FACT_ESCALATION_REASONS",
    # resource keys
    "ResourceKeyRegistry",
    "RESOURCE_KEY_PATTERN",
    "MAX_RESOURCE_KEY_LENGTH",
    "is_resource_key",
    "render_template",
    "template_placeholders",
    # loading and integrity
    "SignatureVerifier",
    "NullVerifier",
    "DigestVerifier",
    "canonical_bytes",
    "compute_registry_digest",
    "load_registry",
    "load_registry_file",
    "UNSIGNED_FIELDS",
]
