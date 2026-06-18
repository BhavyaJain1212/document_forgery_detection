"""Localise a revision-recovery finding's added / changed text on its page.

Given a finding's revision pair and its added / changed ``after``-tokens, find the
word bounding boxes the incremental update introduced on the relevant page.

The selection is a TEXT-DRIVEN MULTISET diff between the prior and current
reconstructed revisions: for each wanted token ``T`` we box ``count_in_current(T) -
count_in_prior(T)`` occurrences. Position (``locate_position_tolerance_pt``) is only
a tie-breaker used to choose *which* of several same-text occurrences to box when
some-but-not-all are new. Because selection is by text count, not coordinates, an
insertion that reflows unchanged text past the tolerance can never be misread as
"added".

Read-only and never raises:

* current-revision word extraction is load-bearing — if it fails while a finding
  has substantive added tokens, the finding is left unlocalised AND a diagnostic
  note is emitted (never a silent empty);
* prior-revision word extraction is best-effort — on failure we degrade to
  current-only matching (duplicate-disambiguation skipped) with a note, never
  zeroing the output.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Callable, Sequence

from ..config import Config
from ..models import BoxPt, Finding, FindingLocation
from .normalize import normalize, tokenize


@dataclass(frozen=True)
class _PageWords:
    """One page's words (pdfplumber space) with the page's visual dimensions."""

    boxes: tuple[BoxPt, ...]
    texts: tuple[str, ...]  # normalised, aligned 1:1 with ``boxes``
    width_pt: float
    height_pt: float
    rotation: int


def _extract_pages(data: bytes, cfg: Config) -> list[_PageWords]:
    """Per-page words + visual dims via pdfplumber. ``[]`` on any failure.

    Coordinates are pdfplumber space (top-left origin, points, rotation applied).
    """
    kwargs: dict = {}
    if cfg.locate_word_x_tolerance is not None:
        kwargs["x_tolerance"] = cfg.locate_word_x_tolerance
    if cfg.locate_word_y_tolerance is not None:
        kwargs["y_tolerance"] = cfg.locate_word_y_tolerance

    pages: list[_PageWords] = []
    try:
        import pdfplumber

        with pdfplumber.open(BytesIO(data)) as pdf:
            for page in pdf.pages:
                words = page.extract_words(**kwargs) or []
                boxes: list[BoxPt] = []
                texts: list[str] = []
                for w in words:
                    boxes.append(
                        BoxPt(
                            x0=float(w.get("x0", 0.0)),
                            top=float(w.get("top", 0.0)),
                            x1=float(w.get("x1", 0.0)),
                            bottom=float(w.get("bottom", 0.0)),
                        )
                    )
                    texts.append(normalize(w.get("text", ""), cfg))
                pages.append(
                    _PageWords(
                        boxes=tuple(boxes),
                        texts=tuple(texts),
                        width_pt=float(page.width),
                        height_pt=float(page.height),
                        rotation=int(getattr(page, "rotation", 0) or 0),
                    )
                )
    except Exception:  # malformed / encrypted / corrupt: never crash
        return []
    return pages


def _positionally_novel(
    curr_boxes: Sequence[BoxPt], prior_boxes: Sequence[BoxPt], tol: float
) -> list[BoxPt]:
    """Current boxes with no prior box of the same text within ``tol`` (x and y)."""
    novel: list[BoxPt] = []
    for cb in curr_boxes:
        if not any(
            abs(cb.x0 - pb.x0) <= tol and abs(cb.top - pb.top) <= tol
            for pb in prior_boxes
        ):
            novel.append(cb)
    return novel


def _after_tokens(finding: Finding, cfg: Config) -> list[str]:
    """Normalised tokens added / changed on the *after* side of this finding."""
    tokens: list[str] = []
    for tc in finding.token_changes:
        if tc.after:
            tokens.extend(tokenize(normalize(tc.after, cfg)))
    return tokens


def _locate_one(
    finding: Finding,
    pages_for: Callable[[int], list[_PageWords]],
    cfg: Config,
    notes: list[str],
) -> FindingLocation | None:
    """Boxes for one finding, or ``None`` (object-only / removed-only / failure)."""
    if finding.page_index is None:
        return None
    wanted = _after_tokens(finding, cfg)
    if not wanted:
        # No added / changed text to locate (object-only or removed-only edit).
        return None

    page = finding.page_index
    curr_pages = pages_for(finding.to_revision)
    if not curr_pages or page >= len(curr_pages):
        notes.append(
            f"localization: word geometry unavailable for revision "
            f"{finding.to_revision} page {page + 1}; finding not localized"
        )
        return None
    curr = curr_pages[page]

    prior_pages = pages_for(finding.from_revision)
    prior = (
        prior_pages[page]
        if prior_pages and page < len(prior_pages)
        else None
    )
    if prior is None:
        notes.append(
            f"localization: prior revision {finding.from_revision} word geometry "
            f"unavailable; duplicate-disambiguation skipped on page {page + 1}"
        )

    curr_by_text: dict[str, list[BoxPt]] = defaultdict(list)
    for box, text in zip(curr.boxes, curr.texts):
        curr_by_text[text].append(box)
    prior_by_text: dict[str, list[BoxPt]] = defaultdict(list)
    if prior is not None:
        for box, text in zip(prior.boxes, prior.texts):
            prior_by_text[text].append(box)

    tol = cfg.locate_position_tolerance_pt
    selected: list[BoxPt] = []
    seen: set[tuple[float, float, float, float]] = set()
    for token in dict.fromkeys(wanted):  # unique, preserve reading order
        curr_t = curr_by_text.get(token, [])
        if not curr_t:
            continue
        prior_t = prior_by_text.get(token, [])
        n_new = max(0, len(curr_t) - len(prior_t))
        if n_new <= 0:
            # Token present in prior in equal/greater count -> not added (the
            # reflow-safe case: an unchanged duplicate that merely moved).
            continue
        if n_new >= len(curr_t):
            chosen = list(curr_t)
        else:
            chosen = _positionally_novel(curr_t, prior_t, tol)[:n_new]
            if len(chosen) < n_new:
                rest = [b for b in curr_t if b not in chosen]
                chosen += rest[: n_new - len(chosen)]
        for box in chosen:
            key = (box.x0, box.top, box.x1, box.bottom)
            if key not in seen:
                seen.add(key)
                selected.append(box)

    if not selected:
        return None
    return FindingLocation(
        boxes=tuple(selected),
        page_width_pt=curr.width_pt,
        page_height_pt=curr.height_pt,
        page_rotation=curr.rotation,
    )


def locate_findings(
    findings: Sequence[Finding],
    recon_result,
    config: Config | None = None,
    notes: list[str] | None = None,
) -> list[Finding]:
    """Return ``findings`` with a :class:`FindingLocation` attached where possible.

    ``recon_result`` is the :class:`~pdf_forgery.revision_recovery.models.ReconstructionResult`
    whose ``revisions`` hold per-revision bytes (``from_revision`` / ``to_revision``
    on each finding index directly into it). Never raises; diagnostics go to
    ``notes``.
    """
    cfg = config or Config()
    if notes is None:
        notes = []
    if not cfg.enable_localization:
        return list(findings)

    revisions = getattr(recon_result, "revisions", ())
    cache: dict[int, list[_PageWords]] = {}

    def pages_for(idx: int) -> list[_PageWords]:
        if idx not in cache:
            if 0 <= idx < len(revisions):
                cache[idx] = _extract_pages(revisions[idx].data, cfg)
            else:
                cache[idx] = []
        return cache[idx]

    out: list[Finding] = []
    for finding in findings:
        location = _locate_one(finding, pages_for, cfg, notes)
        out.append(replace(finding, location=location) if location is not None else finding)
    return out
