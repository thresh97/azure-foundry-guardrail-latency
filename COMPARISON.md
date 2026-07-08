# AIRS vs. Azure AI Safety — Protection Type Comparison

> **Status: NEEDS FURTHER VALIDATION**
> This comparison was generated via automated deep research (96 agents, 14 sources, 25 adversarially verified claims) and manual review. Known gaps and corrections are noted inline. Do not treat as complete or authoritative without hands-on verification against current product documentation.

---

## AIRS API Intercept Security Profile — protection types

| AIRS Protection | Categories / Detail |
|---|---|
| **Toxic Content** | 7 categories: hate speech, sexual content, violence, criminal actions, regulated substances, self-harm, profanity — two severity tiers (Moderate, High), both **default Allow** |
| **Contextual Grounding** | Hallucination detection — response-only (not applied to requests), returns binary grounded/ungrounded verdict |
| **AI Agent Protection** | Adversarial agentic attacks: schema leak, direct tool invocation, memory manipulation — degrades to model-only protections if no AI Agent framework configured |
| **Prompt Injection** | Part of "AI Model Protection" grouping — confirmed present, subtype taxonomy not fully verified |
| **Custom Topics** | User-defined topic guardrails — managed via `airs runtime topics` CLI; generation and audit supported |

> **Validation note:** AIRS docs use "includes references to" phrasing for the 7 toxic content categories — these may be illustrative rather than exhaustive. Full enumeration of the AI Model Protection grouping (prompt injection subtypes, any additional types) needs direct doc review.

---

## Azure AI Content Safety — 11 protection categories

| Azure Protection | Scope | Notes |
|---|---|---|
| Hate and Fairness | Input + Output | — |
| Sexual | Input + Output | — |
| Violence | Input + Output | — |
| Self-Harm | Input + Output | — |
| Prompt Shields | Input only | 2 attack vectors, 14 named subtypes (4 user-prompt, 10 document/indirect) |
| Groundedness | Output only | Non-Reasoning (binary) + Reasoning mode (per-segment explanations) |
| Protected Material for Text | Output only | Copyright / IP detection |
| Protected Material for Code | Output only | Code IP detection |
| PII Detection | Output only | Analyzes LLM completions, not prompts |
| Task Adherence | Agentic | Detects tool-call intent drift; returns `taskRiskDetected` boolean + reasoning string; preview |
| Custom Categories | Input + Output | Standard (ML-trained, hours) or Rapid (LLM-backed, no training step) |

---

## Head-to-head verdict

| AIRS Protection | Azure Equivalent | Verdict |
|---|---|---|
| Toxic Content (7 categories) | Analyze text — 4 categories | **PARTIAL** — Azure gaps: criminal actions, regulated substances, profanity |
| Contextual Grounding | Groundedness Detection | **DIRECT** — Azure superset (adds per-segment Reasoning mode) |
| AI Agent Protection | Task Adherence | **PARTIAL / ORTHOGONAL** — AIRS = external adversarial hijacking; Azure = internal intent drift; different threat axes, complementary not overlapping |
| Prompt Injection | Prompt Shields | **AZURE SUPERSET** — Prompt Shields has 14 labeled subtypes across 2 vectors; AIRS injection subtype taxonomy not verified at same depth |
| Custom Topics | Custom Categories | **DIRECT** — both allow user-defined content policies beyond built-in categories; implementation differences (ML-trained vs. LLM-backed vs. prompt-defined) need validation |

## Azure-only (no confirmed AIRS equivalent)

- **PII Detection** — output-scoped, completion-layer only
- **Protected Material for Text** — copyright / IP detection
- **Protected Material for Code** — code IP detection

## AIRS-only (no confirmed Azure equivalent)

- **Criminal actions** as a distinct toxic content category
- **Regulated substances** as a distinct category
- **Profanity** as a distinct category
- **Network-layer / API intercept architecture** — AIRS sits inline at the network level; Azure guards are model-serving-layer only. Placement difference is independent of feature parity and may affect coverage in multi-model or non-Azure-hosted scenarios.

---

## Known gaps / open questions

1. **Custom topics vs. custom categories implementation delta** — AIRS custom topics appear prompt-defined; Azure offers ML-trained (standard) and LLM-backed (rapid) flavors. Needs side-by-side comparison of configuration model, latency, and supported content types.
2. **AIRS AI Model Protection full enumeration** — the grouping includes at minimum prompt injection, contextual grounding, and custom topics; there may be additional sub-types not surfaced in this research.
3. **AIRS prompt injection subtype taxonomy** — AIRS has prompt injection detection but specific subtypes were not verified. Azure Prompt Shields has 14 named subtypes (4 direct, 10 indirect). Needs AIRS doc review to determine if the coverage gap is real or a documentation depth difference.
4. **AIRS default posture risk** — both Moderate and High toxic content tiers default to **Allow**. Azure content filters default to enabled. Verify deployed AIRS profile actions are set to Block.
5. **Azure Task Adherence (preview)** — limited regional availability; 100K character limit; behavior in production may differ from documentation.
6. **Foundry integration block rate discrepancy** — bench shows `airs` direct blocks 50% vs. `prisma` (Foundry-integrated) blocks 15% despite identical profile configurations. Hypothesis: Azure Foundry AIRS integration may run in observe/monitor mode rather than inline block. Needs verification in Azure AI Foundry portal guardrail settings.
