class BeyondPackError(Exception):
    """Base application error with a stable operator-facing error code."""

    code = "BP-000"


class ConfigurationError(BeyondPackError):
    code = "BP-CFG-001"


class AuthenticationError(BeyondPackError):
    code = "BP-AUTH-001"


class SourceError(BeyondPackError):
    code = "BP-SRC-001"


class DataValidationError(BeyondPackError):
    code = "BP-DATA-001"


class DuplicateProductKeyError(DataValidationError):
    code = "BP-DATA-002"


# Backward-compatible import name used by older integrations.
DuplicateFnskuError = DuplicateProductKeyError


class ProductNotFoundError(BeyondPackError):
    code = "BP-LOOKUP-001"


class InactiveProductError(BeyondPackError):
    code = "BP-LOOKUP-002"


class CountrySelectionRequiredError(BeyondPackError):
    code = "BP-LOOKUP-003"


class PackagingValidationError(BeyondPackError):
    code = "BP-PACK-001"
