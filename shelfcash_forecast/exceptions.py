class ForecastCoreError(Exception):
    """Base exception for predictable Forecast Core failures."""


class DataValidationError(ForecastCoreError):
    """Raised when canonical input cannot be made safe for modelling."""


class InsufficientDataError(ForecastCoreError):
    """Raised when there is not enough chronological history."""


class ArtifactError(ForecastCoreError):
    """Raised when model artifacts are missing or incompatible."""


class FeatureSchemaError(ForecastCoreError):
    """Raised when runtime features do not match the saved schema."""


class FeatureTypeError(ForecastCoreError):
    """Raised when a model feature cannot be represented as a numeric dtype."""
