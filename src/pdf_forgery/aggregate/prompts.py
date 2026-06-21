"""The advisory LLM prompt.

The prompt TEXT is frozen content (the constraint that the model explain
grounded only in the supplied descriptors, cite ``finding_id``\\s, and never
re-judge the verdict). :func:`build_advisory_messages` renders it for a
concrete :class:`AdvisoryInput`, injecting pre-grouped findings + a per-type
glossary so the model explains rather than repeats.

See ``docs/STAGE7_DESIGN.md`` §3.
"""

from __future__ import annotations

import json

from .config import AggregateConfig
from .models import AdvisoryInput

# ---------------------------------------------------------------------------
# System prompt (verbatim — frozen content)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a decision-support assistant for a human fraud reviewer examining a
health-insurance claim document. An automated forgery-detection pipeline has
already analysed the document and produced grouped findings. Your ONLY job is
to explain those groups in plain, measured language so the reviewer can decide
what to do next.

Strict rules:
1. Ground every statement ONLY in the findings provided in the user message.
   Cite the finding_id(s) for each group you explain.
2. You have NOT been given the document, any names, amounts, dates, or
   identifiers. Do not guess, infer, or invent any such content. If a finding
   says an "amount" token diverged, say "an amount field was flagged" — do NOT
   state what the amount was.
3. Do NOT override, re-judge, raise, or lower the verdict. Report the overall
   tier exactly as given. If it is INCONCLUSIVE, say plainly that these methods
   could not assess the document — do not imply it is clean or forged.
4. This is advisory only. A human reviewer makes the final decision. Use
   decision-support phrasing ("a reviewer should verify..."), never absolute
   claims ("this document is fraudulent").
5. Findings are pre-grouped. Write ONE explanation per group. Do NOT repeat the
   same sentence for similar findings.
6. For each group, write three short parts: what the detector found (plain
   language), why it could matter for a forgery review, and what the reviewer
   should check next.
7. A glossary of finding types is provided — use it to explain what each type
   means; do not just restate the type name.
8. The summary must synthesize across groups into a 2–4 sentence narrative, not
   a list or count of findings.
9. Be concise. No preamble, no restating these rules.

Respond as JSON matching this shape:
{
  "summary": "<2-4 sentence narrative synthesizing the groups, naming the overall tier>",
  "tier_statement": "<one honest sentence stating the overall tier and what it means,
                      especially if INCONCLUSIVE>",
  "group_explanations": [
    {
      "finding_ids": ["<id1>", "<id2>"],
      "label": "<short human label, e.g. Text/image mismatch (amount fields, 3x, pages 1-2)>",
      "what_we_found": "<plain language, grounded in descriptors for this group>",
      "why_it_matters": "<what this could indicate for a forgery review>",
      "what_to_check": "<concrete next action for the reviewer>"
    }
  ]
}
"""

# ---------------------------------------------------------------------------
# User prompt template (frozen content; filled per analysis)
# ---------------------------------------------------------------------------

USER_PROMPT_TEMPLATE = """\
Overall tier: {tier} (score: {score})

Per-stage results:
{stages_block}

Finding type glossary (for the types present in this analysis):
{glossary_block}

Grouped findings (descriptors only — no document content was extracted for you):
{groups_json}

Write the decision-support explanation as instructed.
"""


def build_advisory_messages(
    advisory_input: AdvisoryInput,
    config: AggregateConfig | None = None,
) -> list["Message"]:  # noqa: F821 - Message imported lazily below to avoid a cycle
    """Render the system + user messages for ``advisory_input``.

    Groups ``advisory_input.findings`` by ``(stage, type, token_class)``, injects
    a per-type glossary for the types that appear, and pairs it with the
    :data:`SYSTEM_PROMPT`.
    """
    from .advisory import Message, _group_findings  # local import: advisory imports this module
    from .glossary import get_glossary_entry

    score_txt = "n/a" if advisory_input.score is None else str(advisory_input.score)
    stages_block = "\n".join(
        f"{s.stage}: {s.tier.value.upper()} (score "
        f"{'n/a' if s.score is None else s.score}), ran ok={s.ok}"
        for s in advisory_input.stages
    ) or "(no stages ran)"

    groups = _group_findings(advisory_input)

    # Glossary: only include types that appear in this analysis.
    types_present = sorted({g.type for g in groups})
    glossary_lines = []
    for type_token in types_present:
        meaning, implication = get_glossary_entry(type_token)
        glossary_lines.append(f"- {type_token}: {meaning} Implication: {implication}")
    glossary_block = "\n".join(glossary_lines) or "(no findings)"

    groups_json = json.dumps([_group_dict(g) for g in groups], indent=2)

    user_content = USER_PROMPT_TEMPLATE.format(
        tier=advisory_input.tier.value.upper(),
        score=score_txt,
        stages_block=stages_block,
        glossary_block=glossary_block,
        groups_json=groups_json,
    )
    return [
        Message(role="system", content=SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]


def _group_dict(group) -> dict:
    """Serialize a :class:`FindingGroup` to a plain dict for the prompt JSON."""
    return {
        "stage": group.stage,
        "type": group.type,
        "token_class": group.token_class,
        "tier": group.tier.value,
        "count": group.count,
        "pages": list(group.pages),
        "finding_ids": list(group.finding_ids),
    }


__all__ = ["SYSTEM_PROMPT", "USER_PROMPT_TEMPLATE", "build_advisory_messages"]
