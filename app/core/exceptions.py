from typing import Any


class ShelfCashError(Exception):
    code = "SHELFCASH_ERROR"
    default_message = "ShelfCash request failed."
    http_status = 400

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(ShelfCashError):
    code = "VALIDATION_ERROR"
    default_message = "Dữ liệu không hợp lệ."
    http_status = 422


class InventorySnapshotError(ValidationError):
    """Fail-closed errors for immutable, date-only snapshot observations."""
    def __init__(self, code: str, message: str, details=None, *, http_status: int = 422):
        self.code, self.http_status = code, http_status
        super().__init__(message, details)


class StoreNotFoundError(ShelfCashError):
    code = "STORE_NOT_FOUND"
    default_message = "Không tìm thấy cửa hàng."
    http_status = 404

    def __init__(self, store_id: str):
        super().__init__(details={"store_id": store_id})


class ResourceNotFoundError(ShelfCashError):
    code = "RESOURCE_NOT_FOUND"
    default_message = "Không tìm thấy tài nguyên."
    http_status = 404


class VersionConflictError(ShelfCashError):
    code = "VERSION_CONFLICT"
    default_message = "Phiên bản dữ liệu đã thay đổi."
    http_status = 409


class DuplicateRequestError(ShelfCashError):
    code = "DUPLICATE_REQUEST"
    default_message = "Idempotency key đã được dùng cho request khác."
    http_status = 409


class DuplicateResourceError(ShelfCashError):
    code = "DUPLICATE_REQUEST"
    default_message = "Tài nguyên đã tồn tại."
    http_status = 409


class ImportNotFoundError(ResourceNotFoundError):
    code = "IMPORT_NOT_FOUND"
    default_message = "Không tìm thấy import."


class ImportNotReadyError(ShelfCashError):
    code = "IMPORT_NOT_READY"
    default_message = "Import chưa sẵn sàng."
    http_status = 425


class ImportProcessingError(ShelfCashError):
    code = "IMPORT_PROCESSING"
    default_message = "Import đang được xử lý."
    http_status = 409


class MappingIncompleteError(ShelfCashError):
    code = "MAPPING_INCOMPLETE"
    default_message = "Mapping chưa đầy đủ."
    http_status = 422


class ModelNotReadyError(ShelfCashError):
    code = "MODEL_NOT_READY"
    default_message = "Model chưa sẵn sàng."
    http_status = 503


class BudgetExceededError(ShelfCashError):
    code = "BUDGET_EXCEEDED"
    default_message = "Vượt quá ngân sách."
    http_status = 409


class InvalidStateTransitionError(ShelfCashError):
    code = "INVALID_STATE_TRANSITION"
    default_message = "Chuyển trạng thái không hợp lệ."
    http_status = 409


class MenuError(ShelfCashError):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None,
        *, http_status: int = 422,
    ):
        self.code = code
        self.http_status = http_status
        super().__init__(message, details)


class DatabaseNotReadyError(ShelfCashError):
    code = "DATABASE_NOT_READY"
    default_message = "Database chưa sẵn sàng."
    http_status = 503


class ForecastError(ShelfCashError):
    def __init__(self, code: str, message: str, details=None, *, http_status: int = 422):
        self.code, self.http_status = code, http_status
        super().__init__(message, details)


class InsufficientTrainingDataError(ForecastError):
    def __init__(self, message="Chưa đủ dữ liệu lịch sử để huấn luyện mô hình.", details=None):
        super().__init__("INSUFFICIENT_TRAINING_DATA", message, details)


class PlanningError(ShelfCashError):
    def __init__(self, code: str, message: str, details=None, *, http_status: int = 422):
        self.code, self.http_status = code, http_status
        super().__init__(message, details)


class BusinessConstraintError(ShelfCashError):
    def __init__(self, code: str, message: str, details=None, *, http_status: int = 422):
        self.code, self.http_status = code, http_status
        super().__init__(message, details)


class BusinessIdentityConflictError(ShelfCashError):
    def __init__(self, code: str, message: str, details=None):
        self.code, self.http_status = code, 409
        super().__init__(message, details)


class LLMUnavailableError(ShelfCashError):
    code = "LLM_UNAVAILABLE"
    default_message = "Không thể sử dụng dịch vụ diễn giải dữ liệu lúc này và chưa có ánh xạ rule-based đủ tin cậy."
    http_status = 503

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message or self.default_message, details)


class LLMProviderError(ShelfCashError):
    code = "LLM_PROVIDER_ERROR"
    default_message = "Dịch vụ LLM gặp lỗi trong quá trình xử lý."
    http_status = 502

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None, *, http_status: int = 502):
        self.http_status = http_status
        super().__init__(message or self.default_message, details)
