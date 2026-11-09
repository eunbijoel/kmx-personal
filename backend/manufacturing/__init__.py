"""Manufacturing data preprocessing and AAS pipeline for the KMX platform."""

from manufacturing.preprocess import PreprocessResult, preprocess_records

try:
    from manufacturing.pipeline import PipelineResult, run_pipeline
except ImportError:  # pragma: no cover - during partial checkouts before pipeline lands
    PipelineResult = None  # type: ignore[misc, assignment]
    run_pipeline = None  # type: ignore[assignment]

__all__ = [
    "PipelineResult",
    "PreprocessResult",
    "preprocess_records",
    "run_pipeline",
]
