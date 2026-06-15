"""PDF document-forgery detection.

A multi-stage pipeline. Each detection stage is an independent
:class:`~pdf_forgery.core.stage.Stage` that consumes raw PDF bytes plus a shared
:class:`~pdf_forgery.core.context.AnalysisContext` and emits a
:class:`~pdf_forgery.core.types.StageResult`. The orchestrator in
:mod:`pdf_forgery.pipeline` runs a list of stages and collects their results
(fusion into one combined report comes later).

Stages implemented:
    - ``revision_recovery`` — Stage 1: detect direct-text-editing forgery by
      recovering the historical revisions an incremental update leaves in a PDF.

Planned siblings (plug in at the pipeline level): ``font_fingerprint``,
``ocr_crosscheck``.
"""

__version__ = "0.1.0"
