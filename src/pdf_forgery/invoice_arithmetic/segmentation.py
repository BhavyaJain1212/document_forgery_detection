"""Conservative page/table grouping into logical invoices.

The segmenter deliberately separates evidence extraction from arithmetic.  It
never invents a cross-page ownership claim merely because pages are adjacent:
explicit invoice anchors, terminal summaries, repeated schemas, and aligned
headerless rows determine whether invoice-level equations are safe to run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.glyphs import Glyph, TextLine, group_lines
from .config import InvoiceConfig
from .models import (
    Cell,
    ColumnRole,
    LogicalInvoice,
    SegmentationConfidence,
    Table,
)
from .table import infer_continuation_table, schemas_align, valid_line_item_rows


_ANCHOR_RE = re.compile(
    r"\b(?:invoice|bill)\s*(?:no\.?|number|#)\s*[:#-]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9._/-]*)",
    re.IGNORECASE,
)


@dataclass
class _Group:
    tables: list[Table] = field(default_factory=list)
    summaries: list[Cell] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    confidence: SegmentationConfidence = SegmentationConfidence.HIGH
    basis: list[str] = field(default_factory=list)
    anchor: str | None = None
    allow_cross: bool = True

    def add_page(
        self,
        page_index: int,
        table: Table | None,
        summaries: list[Cell],
        confidence: SegmentationConfidence,
        basis: str,
        anchor: str | None,
    ) -> None:
        if page_index not in self.pages:
            self.pages.append(page_index)
        if table is not None:
            self.tables.append(table)
        self.summaries.extend(summaries)
        self.basis.append(basis)
        if self.anchor is None and anchor:
            self.anchor = anchor
        self.confidence = _lower_confidence(self.confidence, confidence)
        if self.confidence is SegmentationConfidence.AMBIGUOUS:
            self.allow_cross = False


def segment_logical_invoices(
    glyphs: list[Glyph],
    tables: list[Table],
    summary_cells: list[Cell],
    config: InvoiceConfig | None = None,
) -> list[LogicalInvoice]:
    """Group page-local tables into deterministic logical invoice records."""
    cfg = config or InvoiceConfig()
    lines = group_lines(
        glyphs,
        line_baseline_tolerance=cfg.line_baseline_tolerance,
        token_gap_ratio=cfg.token_gap_ratio,
    )
    lines_by_page: dict[int, list[TextLine]] = {}
    for line in lines:
        lines_by_page.setdefault(line.page_index, []).append(line)
    tables_by_page: dict[int, list[Table]] = {}
    for table in tables:
        tables_by_page.setdefault(table.page_index, []).append(table)
    summaries_by_page: dict[int, list[Cell]] = {}
    for cell in summary_cells:
        summaries_by_page.setdefault(cell.page_index, []).append(cell)

    page_indexes = sorted(
        set(lines_by_page) | set(tables_by_page) | set(summaries_by_page)
    )
    groups: list[_Group] = []
    current: _Group | None = None

    for page_index in page_indexes:
        page_lines = lines_by_page.get(page_index, [])
        page_tables = sorted(
            tables_by_page.get(page_index, []), key=lambda table: -table.header_y
        )
        page_summaries = summaries_by_page.get(page_index, [])
        anchor = _page_anchor(page_lines)
        has_title = _page_has_label(page_lines, cfg.invoice_title_labels)
        continuation_marker = _page_has_label(page_lines, cfg.continuation_labels)

        if len(page_tables) > 1:
            if current is not None:
                groups.append(current)
                current = None
            groups.extend(_same_page_groups(page_tables, page_summaries))
            continue

        explicit_table = page_tables[0] if page_tables else None
        inferred_table: Table | None = None
        if explicit_table is None and current is not None and current.tables:
            inferred_table = infer_continuation_table(page_lines, current.tables[-1], cfg)
        table = explicit_table or inferred_table

        if table is None:
            if (
                current is not None
                and page_index == current.pages[-1] + 1
                and page_summaries
                and not has_title
                and not (anchor and current.anchor and anchor != current.anchor)
            ):
                current.add_page(
                    page_index,
                    None,
                    page_summaries,
                    SegmentationConfidence.MEDIUM,
                    "consecutive summary-only continuation page",
                    anchor,
                )
            continue

        if current is None:
            current = _start_group(
                page_index,
                table,
                page_summaries,
                SegmentationConfidence.HIGH,
                "first bounded table in logical invoice",
                anchor,
            )
            continue

        consecutive = page_index == current.pages[-1] + 1
        conflicting_anchor = bool(anchor and current.anchor and anchor != current.anchor)
        same_anchor = bool(anchor and current.anchor and anchor == current.anchor)
        previous_terminal = _page_has_terminal_summary(
            current.pages[-1], current.summaries
        )

        if conflicting_anchor:
            groups.append(current)
            current = _start_group(
                page_index,
                table,
                page_summaries,
                SegmentationConfidence.HIGH,
                f"explicit invoice anchor changed to {anchor}",
                anchor,
            )
            continue

        if same_anchor and consecutive:
            confidence = (
                SegmentationConfidence.HIGH
                if explicit_table is not None
                else SegmentationConfidence.MEDIUM
            )
            current.add_page(
                page_index,
                table,
                page_summaries,
                confidence,
                "same explicit invoice anchor on consecutive page",
                anchor,
            )
            continue

        if previous_terminal and (explicit_table is not None or has_title):
            groups.append(current)
            current = _start_group(
                page_index,
                table,
                page_summaries,
                SegmentationConfidence.HIGH,
                "previous invoice ended with terminal total; new table/title started",
                anchor,
            )
            continue

        if explicit_table is not None:
            aligned = consecutive and schemas_align(current.tables[-1], explicit_table, cfg)
            if aligned and not previous_terminal:
                current.add_page(
                    page_index,
                    explicit_table,
                    page_summaries,
                    SegmentationConfidence.MEDIUM,
                    "repeated aligned table header with no conflicting invoice anchor",
                    anchor,
                )
            else:
                groups.append(current)
                current = _start_group(
                    page_index,
                    explicit_table,
                    page_summaries,
                    SegmentationConfidence.AMBIGUOUS,
                    "new table could not be confidently joined to or separated from prior invoice",
                    anchor,
                )
            continue

        valid_rows = valid_line_item_rows(table)
        supported = valid_rows >= cfg.headerless_min_valid_rows or bool(
            same_anchor or continuation_marker
        )
        if consecutive and not previous_terminal and supported and not has_title:
            current.add_page(
                page_index,
                table,
                page_summaries,
                SegmentationConfidence.MEDIUM,
                f"headerless continuation with {valid_rows} aligned row triple(s)",
                anchor,
            )
        else:
            groups.append(current)
            current = _start_group(
                page_index,
                table,
                page_summaries,
                SegmentationConfidence.AMBIGUOUS,
                "headerless aligned rows found, but invoice boundary evidence was ambiguous",
                anchor,
            )

    if current is not None:
        groups.append(current)

    return [_freeze(group, index + 1) for index, group in enumerate(groups)]


def _start_group(
    page_index: int,
    table: Table,
    summaries: list[Cell],
    confidence: SegmentationConfidence,
    basis: str,
    anchor: str | None,
) -> _Group:
    group = _Group(confidence=confidence, allow_cross=confidence is not SegmentationConfidence.AMBIGUOUS)
    group.add_page(page_index, table, summaries, confidence, basis, anchor)
    return group


def _same_page_groups(tables: list[Table], summaries: list[Cell]) -> list[_Group]:
    assigned: list[list[Cell]] = [[] for _ in tables]
    for cell in summaries:
        y = cell.bbox[1]
        for index, table in enumerate(tables):
            lower = tables[index + 1].header_y if index + 1 < len(tables) else float("-inf")
            if lower < y < table.header_y:
                assigned[index].append(cell)
                break

    terminal_roles = {ColumnRole.SUBTOTAL, ColumnRole.GRAND_TOTAL, ColumnRole.BALANCE}
    clearly_bounded = all(
        any(cell.role in terminal_roles for cell in cells) for cells in assigned
    )
    if clearly_bounded:
        return [
            _start_group(
                table.page_index,
                table,
                assigned[index],
                SegmentationConfidence.MEDIUM,
                "same-page table bounded by its own summary region",
                None,
            )
            for index, table in enumerate(tables)
        ]

    group = _Group(
        tables=list(tables),
        summaries=list(summaries),
        pages=[tables[0].page_index],
        confidence=SegmentationConfidence.AMBIGUOUS,
        basis=["multiple same-page tables without uniquely bounded summaries"],
        allow_cross=False,
    )
    return [group]


def _freeze(group: _Group, number: int) -> LogicalInvoice:
    return LogicalInvoice(
        logical_invoice_id=f"invoice-{number:03d}",
        page_indexes=tuple(sorted(set(group.pages))),
        tables=tuple(group.tables),
        summary_cells=tuple(group.summaries),
        segmentation_confidence=group.confidence,
        segmentation_basis=tuple(dict.fromkeys(group.basis)),
        allow_cross_row_checks=group.allow_cross,
    )


def _page_text(lines: list[TextLine]) -> str:
    return "\n".join(" ".join(token.text for token in line.tokens) for line in lines)


def _page_anchor(lines: list[TextLine]) -> str | None:
    match = _ANCHOR_RE.search(_page_text(lines))
    return match.group(1).casefold() if match else None


def _page_has_label(lines: list[TextLine], labels: tuple[str, ...]) -> bool:
    text = _page_text(lines).casefold()
    return any(label.casefold() in text for label in labels)


def _page_has_terminal_summary(page_index: int, summaries: list[Cell]) -> bool:
    return any(
        cell.page_index == page_index
        and cell.role in (ColumnRole.GRAND_TOTAL, ColumnRole.BALANCE)
        for cell in summaries
    )


def _lower_confidence(
    left: SegmentationConfidence, right: SegmentationConfidence
) -> SegmentationConfidence:
    rank = {
        SegmentationConfidence.HIGH: 0,
        SegmentationConfidence.MEDIUM: 1,
        SegmentationConfidence.AMBIGUOUS: 2,
    }
    return left if rank[left] >= rank[right] else right

