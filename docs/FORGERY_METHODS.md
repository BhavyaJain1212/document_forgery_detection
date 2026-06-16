# Canonical forgery_method & tag strings (FROZEN)

These literals are transcribed verbatim from @docs/document_forgery_reference.html. The
`forgery_method` field on every `ModuleResult` (§5) **must** use one of these exact
strings — same spelling, casing, punctuation, and spacing. If a needed method is missing
here, it is missing from the reference: stop and flag it rather than inventing one.

## Evidence tags (the `Evidence.tag` Literal, §5)
`PIXEL_MASK` · `BYTE_OBJECT_RANGE` · `METADATA_FLAG` · `MASK_RATIONALE` ·
`REVISION_CHAIN` · `SIGNATURE_FLAG` · `RISK_SCORE`

(The reference renders these with spaces/slashes — e.g. "BYTE / OBJECT RANGE",
"MASK + RATIONALE". In the envelope they are the underscore Literals above.)

## PDF forgery methods
| forgery_method (verbatim) | Reference output tag | Module (§9.1) |
|---|---|---|
| `Direct text/object editing` | BYTE / OBJECT RANGE | object_tree.py |
| `Incremental update ("save") attacks` | REVISION CHAIN | xref_revisions.py |
| `Metadata manipulation` | METADATA FLAG | metadata_regex.py |
| `Producer-tool identity forgery` | METADATA FLAG | metadata_regex.py |
| `Digital signature bypass` | SIGNATURE FLAG | signature.py |
| `Embedded image tampering` | PIXEL MASK (image layer) | embedded_images.py → handoff |
| `Print-and-rescan laundering` | PIXEL MASK (image layer) | embedded_images.py → handoff |
| `Fully AI-generated PDF` | RISK SCORE | synthesis (§10.2) |

## JPEG forgery methods
| forgery_method (verbatim) | Reference output tag | Module (§9.2) |
|---|---|---|
| `Direct pixel edit + re-save` | PIXEL MASK | catnet.py / ela.py |
| `Splicing` | PIXEL MASK | catnet.py / trufor.py |
| `Copy-move` | PIXEL MASK | mantranet.py |
| `AI inpainting / generative fill` | MASK + RATIONALE | mllm_rationale.py |
| `Recompression laundering` | PIXEL MASK (degraded precision) | trufor.py |
| `EXIF metadata forgery` | METADATA FLAG | exif_metadata.py |
| `Tampered text in document JPEG` | PIXEL MASK (character level) | doctamper.py |
| `Explainable / investigator-ready detection` | MASK + WRITTEN RATIONALE | mllm_rationale.py |

## PNG forgery methods
| forgery_method (verbatim) | Reference output tag | Module (§9.3) |
|---|---|---|
| `Splicing` | PIXEL MASK | prnu_noiseprint.py |
| `Copy-move` | PIXEL MASK | iml_vit.py / mantranet.py |
| `AI inpainting / generative fill` | MASK + RATIONALE | doctamper_textsleuth.py |
| `Object removal + background cloning` | PIXEL MASK | iml_vit.py / mantranet.py |
| `CFA / Bayer pattern disruption` | PIXEL MASK | cfa_trufor.py |
| `PNG metadata / chunk tampering` | METADATA FLAG | chunk_metadata.py |
| `Tampered text in document PNG` | MASK + RATIONALE | doctamper_textsleuth.py |
| `Fully synthetic document image` | RISK SCORE | synthesis (§10.2) |

## Routing/triage-emitted flag (§8.1)
| forgery_method (verbatim) | tag |
|---|---|
| `extension/format mismatch` | METADATA_FLAG |
| `active-content present` | METADATA_FLAG |

## Implementation note
Define the canonical strings once as constants/enums and import them everywhere; do not
re-type them at call sites. A single test should assert every module's emitted
`forgery_method` is a member of this frozen set.
