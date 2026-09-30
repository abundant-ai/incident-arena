"""Typed, loud verifier failures."""


class VerifierError(RuntimeError):
    """Base class for contract, evidence, and verdict failures."""


class ContractError(VerifierError):
    """The public verification contract is malformed or contradictory."""


class EvidenceError(VerifierError):
    """Required protected evidence is missing or malformed."""


class SafeRepairFailure(VerifierError):
    """The active challenge proved an unsafe or non-durable candidate repair."""


class VerdictError(VerifierError):
    """A verdict/report/reward payload violates the stable v2 schema."""
