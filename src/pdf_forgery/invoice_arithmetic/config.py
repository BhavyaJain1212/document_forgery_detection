"""Central configuration for the invoice-arithmetic stage.

Every threshold, score value, tolerance, role-label vocabulary, and detector
toggle lives here so nothing magic is hard-coded in the detectors or scorer.
Pass an :class:`InvoiceConfig` to the public API; pass ``None`` for the defaults
(equivalent to ``InvoiceConfig()``).

Role labels are matched against header cells after NFC normalisation +
lower-casing + stripping of non-alphanumerics, so ``"Unit Price"`` and
``"unit-price"`` and ``"UnitPrice"`` all collapse to ``unitprice``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_role_labels() -> dict[str, tuple[str, ...]]:
    """Canonical column role -> the normalised header labels that imply it.

    Labels are compared after :func:`normalize_label` (lower-case, alphanumerics
    only). Order within a role does not matter; a header cell maps to the first
    role whose label list contains its normalised text.
    """
    return {
        # Quantities that multiply a unit rate. ``billable`` ranks as a quantity
        # because on usage invoices it is the quantity actually charged.
        "qty": (
            "qty", "quantity", "qnty", "units", "unit", "nos", "no",
            "billable", "billableqty", "days", "noofdays", "daysofstay",
            "quantitybilled",
        ),
        "rate": (
            "rate", "unitprice", "price", "unitrate", "ratepernight",
            "roomrate", "rateperday", "tariff", "mrp", "rateperunit",
        ),
        "amount": (
            "amount", "linetotal", "total", "lineamount", "value",
            "amountinr", "grossamount", "charge", "charges", "totalamount",
        ),
        "subtotal": (
            "subtotal", "subtotalamount", "grosstotal", "totalbeforetax",
            "pagesubtotal", "pagetotal",
        ),
        "discount": ("discount", "disc", "lessdiscount", "rebate", "concession"),
        "cgst": ("cgst", "cgstamount"),
        "sgst": ("sgst", "sgstamount", "ugst", "utgst"),
        "igst": ("igst", "igstamount"),
        "gst": ("gst", "tax", "taxamount", "totalgst", "totaltax", "gstamount"),
        "grand_total": (
            "grandtotal", "nettotal", "netpayable", "netamount", "amountdue",
            "amountpayable", "totalpayable", "balancedue", "totaldue",
            "finalamount", "payableamount",
        ),
        "deposit": ("deposit", "advance", "advancepaid", "amountpaid", "paid"),
        "balance": ("balance", "balanceamount", "duebalance", "outstanding"),
        "date": (
            "date", "admissiondate", "dischargedate", "admission", "discharge",
            "doa", "dod", "fromdate", "todate", "billdate", "invoicedate",
        ),
        "description": (
            "description", "particulars", "item", "items", "name",
            "productname", "service", "services", "details", "narration",
        ),
    }


@dataclass
class InvoiceConfig:
    """Tunable parameters for invoice-arithmetic extraction/detection/scoring."""

    # ------------------------------------------------------------------ #
    # Line / token grouping (passed to the shared glyph extractor)        #
    # ------------------------------------------------------------------ #

    line_baseline_tolerance: float = 0.5
    """Two glyphs share a row when their baselines (y0) differ by at most this
    fraction of the larger glyph size."""

    token_gap_ratio: float = 0.35
    """A horizontal gap wider than this fraction of glyph size starts a new cell
    even without an explicit space glyph."""

    # ------------------------------------------------------------------ #
    # Table / column identification                                       #
    # ------------------------------------------------------------------ #

    role_labels: dict[str, tuple[str, ...]] = field(default_factory=_default_role_labels)
    """Canonical column role -> normalised header labels that imply it."""

    min_header_role_matches: int = 2
    """A line is treated as a table header only if at least this many of its
    cells map to a known column role."""

    column_assign_tolerance: float = 0.0
    """Extra slack (PDF units) added to a column's midpoint boundary when
    assigning a data cell to a column. 0 = pure nearest-midpoint."""

    min_numeric_cells: int = 4
    """Below this many confidently-parsed numeric cells across all tables there
    is too little structure to reason about -> INCONCLUSIVE."""

    min_relationships: int = 1
    """At least this many relationships must be evaluable, else INCONCLUSIVE."""

    invoice_id_labels: tuple[str, ...] = (
        "invoice no", "invoice number", "bill no", "bill number",
    )
    """Labels used to extract explicit invoice anchors from page text."""

    invoice_title_labels: tuple[str, ...] = ("tax invoice", "invoice", "bill")
    """Page titles that can indicate a fresh invoice after a terminal total."""

    continuation_labels: tuple[str, ...] = ("continued", "continuation", "contd")
    """Explicit continuation markers that support a one-row headerless page."""

    continuation_column_tolerance: float = 24.0
    """Maximum centre drift, in PDF points, for repeated/inferred schemas."""

    headerless_min_valid_rows: int = 2
    """Minimum qty/rate/amount triples for an unlabelled continuation page."""

    # ------------------------------------------------------------------ #
    # Numeric tolerance                                                   #
    # ------------------------------------------------------------------ #

    abs_tolerance: float = 0.05
    """Absolute epsilon: ``|expected - stated|`` within this is reconciled
    (covers legitimate half-up/half-down rounding of a couple of paise/cents)."""

    rel_tolerance: float = 0.005
    """Relative epsilon (fraction of the expected magnitude). A relationship
    reconciles if it is within EITHER the absolute or the relative tolerance."""

    gross_rel_error: float = 0.05
    """A broken relationship is "gross" (clearly not rounding/extraction noise)
    when its relative error meets or exceeds this (default 5%). The Sejda tamper
    (249.69 -> 24019.69) and the Microsoft 37004.49 row are ~100x, far above."""

    rate_precision_aware: bool = True
    """For LINE_ITEM (qty * rate = amount) only: many real invoices (e.g. Azure
    usage bills) print ``rate`` rounded to a fixed number of decimals while the
    true rate carries more precision, so ``qty * shown_rate`` legitimately misses
    the stated amount — by a large absolute/relative margin when ``qty`` is big
    (2434.87 * 0.08 = 194.79 vs stated 204.53; true rate ~0.084). When True the
    line-item check also accepts an amount that falls inside the interval implied
    by the operands' printed decimal precision (each operand treated as
    ``shown ± ½·10^-decimals``). A genuine tamper (operand untouched, amount 100x
    off) still lands far outside this band and flags. Cannot be verified tighter
    than the rate's printed precision allows, so this is the correct bound."""

    # ------------------------------------------------------------------ #
    # Detector toggles                                                    #
    # ------------------------------------------------------------------ #

    enable_line_item: bool = True
    """qty * rate = amount per data row."""

    enable_subtotal: bool = True
    """sum(line amounts) = subtotal."""

    enable_grand_total: bool = True
    """subtotal + taxes - discounts = grand total."""

    enable_gst_split: bool = True
    """CGST + SGST (+ IGST) = total GST."""

    enable_deposit_balance: bool = True
    """deposit + balance = final amount."""

    enable_room_charge: bool = True
    """room rate * charged days = room charge."""

    enable_date_span: bool = True
    """admission/discharge date span agrees with charged days."""

    # ------------------------------------------------------------------ #
    # Convergence / localization                                          #
    # ------------------------------------------------------------------ #

    require_convergence_for_high: bool = True
    """HIGH requires that a single cell's correction reconciles >= the
    convergence threshold of independent broken equations. A lone broken
    equation (no corroboration) is capped at strong MEDIUM, per the owner's
    decision (the submitted bill alone, no original to compare)."""

    convergence_high_threshold: int = 2
    """How many independent broken equations a single cell must reconcile for
    HIGH (e.g. a line amount that breaks both qty*rate AND sum=subtotal)."""

    # ------------------------------------------------------------------ #
    # Score values within each band (HIGH 70-100 / MEDIUM 30-70 / LOW 0-30)
    # ------------------------------------------------------------------ #

    score_high_amount_convergence: int = 92
    """HIGH: a high-value (amount/date) cell whose correction reconciles
    multiple equations (convergence)."""

    score_high_convergence: int = 78
    """HIGH: convergence on a non-high-value cell."""

    score_medium_lone_gross: int = 65
    """Strong MEDIUM: a single gross broken relationship localized to one cell,
    with no convergence (could be source/extraction error, or a single-cell edit
    with nothing else to corroborate it)."""

    score_medium_lone_minor: int = 45
    """MEDIUM: a single broken relationship just outside tolerance (smaller
    relative error — more plausibly extraction/rounding noise)."""

    score_low_reconciled: int = 10
    """LOW: every evaluated relationship reconciled within tolerance."""

    # ------------------------------------------------------------------ #
    # Localization                                                        #
    # ------------------------------------------------------------------ #

    enable_localization: bool = True
    """When True, ``analyze_bytes`` captures per-page dimensions so the aggregate
    layer can normalize the output-cell bbox into the canonical [0,1] top-left
    space.  Set to False to skip the dimension capture (bbox will be None)."""
