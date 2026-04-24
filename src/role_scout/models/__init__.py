"""role_scout domain models — re-exported for convenience."""
from role_scout.models.api import (
    DataEnvelope,
    PipelineExtendData,
    PipelineResumeData,
    PipelineResumeRequest,
    PipelineStatusResponse,
    RunsListResponse,
    RunsPagination,
    TailorRequest,
    TailoredResume,
    TailorResponseBody,
    TopMatch,
    WatchlistAddRequest,
    WatchlistResponseBody,
)
from role_scout.models.core import (
    BaseSchema,
    CancelReason,
    CompanyName,
    ErrorDetail,
    ErrorEnvelope,
    HashId,
    JobStatus,
    Meta,
    RequestId,
    RunId,
    RunMode,
    RunStatus,
    SourceHealthEntry,
    SourceName,
    TriggerType,
)
from role_scout.models.records import (
    QualifiedJobRow,
    RunLogRow,
    SourceHealthBlob,
    TailoredResumeRecord,
)
from role_scout.models.state import JobSearchState, StateSizeExceeded, assert_state_size

__all__ = [
    "BaseSchema", "CancelReason", "CompanyName", "ErrorDetail", "ErrorEnvelope",
    "HashId", "JobStatus", "Meta", "RequestId", "RunId", "RunMode", "RunStatus",
    "SourceHealthEntry", "SourceName", "TriggerType",
    "QualifiedJobRow", "RunLogRow", "SourceHealthBlob", "TailoredResumeRecord",
    "JobSearchState", "StateSizeExceeded", "assert_state_size",
    "DataEnvelope", "PipelineExtendData", "PipelineResumeData", "PipelineResumeRequest",
    "PipelineStatusResponse", "RunsListResponse", "RunsPagination", "TailorRequest",
    "TailoredResume", "TailorResponseBody", "TopMatch", "WatchlistAddRequest", "WatchlistResponseBody",
]
