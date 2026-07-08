# Azure AI Foundry Guardrail Latency Benchmark

> **Disclaimer:** Personal test/development project. Not official, not production-ready, no warranty. Not affiliated with or endorsed by Palo Alto Networks or Microsoft. Use at your own risk.

**License:** MIT — see [LICENSE](LICENSE)

---

## What This Is

When you deploy an AI model, you need guardrails — filters that inspect prompts and block harmful requests. Every guardrail adds latency. This project measures how much each guardrail approach actually costs, side-by-side in parallel, and what coverage each buys.

Six legs run in parallel per prompt:

| Leg | API | Type | Description |
|-----|-----|------|-------------|
| `default` | Azure AI Foundry / Responses API | Inference + guardrail | Microsoft.Default RAI (system-managed) |
| `strict` | Azure AI Foundry / Responses API | Inference + guardrail | Custom RAI — low-severity thresholds, prompt + completion |
| `prisma` | Azure AI Foundry / Responses API | Inference + guardrail | Azure RAI pass-through + Prisma AIRS via Foundry native integration |
| `analyze` | Azure AI Content Safety `text:analyze` | Scan-only | Harmful content detection: Hate, Sexual, SelfHarm, Violence |
| `shield` | Azure AI Content Safety `text:shieldPrompt` | Scan-only | Prompt injection / jailbreak detection |
| `airs` | Prisma AIRS sync scan API | Scan-only | Direct AIRS scan, no model call (GCP-hosted) |

**Model:** `gpt-5.4-nano` (`GlobalStandard`, `max_tokens=1`). One token keeps latency dominated by guardrail overhead, not generation.

---

## Architecture

```
Azure AI Services Account  (cs-foundry-benchmark-services, West US)
├── Deployments: azure-default, azure-strict, prisma-airs
├── RAI Policies: strict-azure-safety-policy, prisma-airs-safety-policy
└── Projects: mharms-proj-{default,strict,prisma}-{suffix}

Key Vault  (kv-foundry-secrets-bk)
└── Secret: Prisma-AIRS-API-Key

Managed Identity  (id-foundry-hub-identity)
  Roles: Key Vault Administrator, Azure AI Developer on account

Benchmark VM  (vm-bench-westus, West US)
  System-assigned identity roles:
    - Cognitive Services OpenAI User  (Foundry inference legs)
    - Cognitive Services User          (Content Safety scan legs)
  Fully provisioned by cloud-init — no manual setup needed
```

**Foundry endpoint:** `https://cs-foundry-benchmark-subdomain.services.ai.azure.com/openai/v1`
**Content Safety endpoint:** `https://cs-foundry-benchmark-subdomain.cognitiveservices.azure.com`

Project names get a shared random 3-char suffix on each apply to avoid Azure soft-delete ETag conflicts on destroy/recreate cycles.

---

## Standalone Guardrail APIs

The `analyze` and `shield` legs call Azure AI Content Safety directly — the same Cognitive Services account, without going through the Foundry/OpenAI proxy. These are scan-only: no model is invoked.

### A. Analyze Text API (`analyze` leg)

```
POST https://<resource>.cognitiveservices.azure.com/contentsafety/text:analyze?api-version=2024-09-01
```

Detects harmful content across four categories. Each returns a severity on the public 0 / 2 / 4 / 6 scale (Safe / Low / Medium / High), mapped from an internal 0–7 score.

```json
{
  "text": "...",
  "categories": ["Hate", "Sexual", "SelfHarm", "Violence"],
  "outputType": "FourSeverityLevels",
  "blocklistNames": [],
  "haltOnBlocklistHit": false
}
```

| Category | What it detects |
|----------|----------------|
| Hate | Pejorative or hostile language targeting protected attributes (race, gender, religion, etc.) |
| Sexual | Graphic sexual content, non-consensual content, grooming, exploitation |
| Violence | Physical harm, weapon manufacturing, terrorism, extremism, domestic abuse |
| SelfHarm | Instructions or encouragement for suicide, self-mutilation, eating disorders |

The bench blocks when any category severity > 0. Custom blocklists are supported via `blocklistNames` (managed through the Text Blocklist Management API).

### B. Prompt Shields API (`shield` leg)

```
POST https://<resource>.cognitiveservices.azure.com/contentsafety/text:shieldPrompt?api-version=2024-09-01
```

Detects adversarial intent to override model behavior or inject malicious instructions via retrieved documents.

```json
{
  "userPrompt": "...",
  "documents": ["..."]
}
```

`documents` is optional — used for RAG/retrieved-content scenarios (indirect attacks / XPIA).

**Direct attacks (jailbreaks):** system prompt overrides, DAN-style persona requests, obfuscation and encoding tricks.

**Indirect attacks (XPIA):** malicious directives injected into documents the model processes — hidden exfiltration commands, downstream code execution, capability blocking payloads.

The bench blocks when `userPromptAnalysis.attackDetected` is true.

### Gap Analysis

These capabilities are absent from the two standalone Content Safety APIs and require separate integrations:

| Gap | Standalone alternative |
|-----|----------------------|
| PII / sensitive data leakage | Azure AI Language — PII Detection API |
| Groundedness / hallucinations | Azure AI Content Safety — Groundedness Detection API (requires grounding sources alongside model output) |
| Task drift / adherence | Microsoft Foundry Evaluation Framework SDK (asynchronous) |
| Protected material (text/code) | Protected Material for Text API; Protected Material for Code API (GitHub Copilot dataset) |
| Prompt injection via system-level delimiters | Spotlighting (preview) — prompt-engineering architecture technique, not an API call |
| Cybercrime: phishing, fraud, synthesis instructions | Outside text:analyze categories — AIRS catches these via broader policy coverage |

> **Note on inference backend alignment:** `analyze` (text:analyze) and the `default` Foundry inference leg both block 20/100 on the same harmful prompt set, consistent with the inference backend using text:analyze internally. This cannot be confirmed from the outside.

---

## Approximate Monthly Cost (idle)

| Resource | SKU | ~$/month |
|---|---|---|
| Benchmark VM (`vm-bench-westus`) | Standard_B2s, West US | $30 |
| Public IP (Standard Static) | — | $4 |
| OS disk (30 GB Standard LRS) | — | $1 |
| AI Services account + deployments | GlobalStandard, pay-per-token | $0 idle |
| Key Vault | Standard | $0 idle |
| **Total** | | **~$35/month** |

The VM is the only meaningful cost. Destroy it when not benchmarking.

---

## Prerequisites

```bash
brew install terraform azure-cli

az login
az account set --subscription "<id>"

az provider register --namespace Microsoft.CognitiveServices --wait
az provider register --namespace Microsoft.KeyVault --wait
```

---

## Deploy

```bash
cp example.tfvars terraform.tfvars
# Fill in: subscription_id, prisma_airs_api_key_value,
#          prisma_airs_direct_api_key_value, vm_admin_ssh_public_key, allowed_ssh_ips

terraform init
terraform plan
terraform apply --auto-approve
```

---

## Prisma AIRS + Azure AI Foundry Integration

### Portal registration (manual — cannot be automated)

The `raiExternalSafetyProviders` ARM resource type returns `UnsupportedAction` on all API versions as of 2026-07-02. Registration must be done in the portal after `terraform apply`:

1. **ai.azure.com → New Foundry → Guardrails → Integrations**
2. Add Palo Alto Networks:
   - Key Vault: `kv-foundry-secrets-bk` / Secret: `Prisma-AIRS-API-Key`
   - Managed Identity: `id-foundry-hub-identity`
3. Assign to the `prisma-airs` deployment

> **Subscription limit (confirmed 2026-07-02):** Only **one** third-party guardrail registration is allowed per Azure subscription. If you already have one registered elsewhere, delete it first.

### Why two Prisma AIRS API keys

| Terraform var | Profile | Used by |
|---|---|---|
| `prisma_airs_api_key_value` | `ai-foundry-prisma-benchmark` | Azure Foundry → Prisma (stored in Key Vault) |
| `prisma_airs_direct_api_key_value` | `bench-direct-api` | `bench.py` `airs` direct leg |

Separate keys mean direct API calls and Foundry-mediated calls appear as distinct entries in Prisma AIRS session logs.

### Known constraints (as of 2026-07-02)

- **300ms inline timeout** — Prisma recommends enabling only prompt injection + toxic content detectors. Full detector suite risks timeouts that allow prompts through.
- **Text only** — images and audio are not supported.
- **West US region required** for Prisma AIRS; other regions are unsupported.
- **No tool call scanning** — only prompts and completions are inspected.

---

## Prisma AIRS Key Management

```bash
export PANW_MGMT_CLIENT_ID="<sa>@<tsg-id>.iam.panserviceaccount.com"
export PANW_MGMT_CLIENT_SECRET="<secret>"
export PANW_MGMT_TSG_ID="<tsg-id>"

airs runtime profiles list
airs runtime api-keys list

# Rotate a key
airs runtime api-keys regenerate <key-id> --interval 90 --unit days
# update terraform.tfvars, then:
terraform taint azurerm_linux_virtual_machine.bench_vm && terraform apply --auto-approve
```

---

## Running the Benchmark

### Authentication — auto-discovered at startup

```
AZURE_AI_API_KEY set   → API key (local dev)
Azure VM               → Managed Identity (no .env changes needed)
Neither                → az login credential (developer laptop)
```

Content Safety legs (`analyze`, `shield`) use a separate MSI token with scope `https://cognitiveservices.azure.com/.default`, requiring `Cognitive Services User` role on the account (provisioned by Terraform).

### On the VM (recommended)

```bash
ssh azureuser@$(terraform output -raw bench_vm_public_ip)
cd bench && source .venv/bin/activate
python bench.py -n 20 -r 3 --seed 42
```

### Locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bench.py -n 5 -r 1 --seed 42     # smoke test
python bench.py -n 20 -r 3 --seed 42    # full run
```

### .env reference

```bash
# Required
AZURE_AI_ENDPOINT=https://cs-foundry-benchmark-subdomain.services.ai.azure.com/openai/v1
DEPLOYMENT_DEFAULT=azure-default
DEPLOYMENT_STRICT=azure-strict
DEPLOYMENT_PRISMA=prisma-airs

# Omit on VM (uses MSI) or with az login
AZURE_AI_API_KEY=<terraform output -raw ai_services_primary_key>

# Enables analyze + shield legs
AZURE_CONTENT_SAFETY_ENDPOINT=https://cs-foundry-benchmark-subdomain.cognitiveservices.azure.com

# Enables airs leg
PRISMA_AIRS_DIRECT_API_KEY=<key>
PRISMA_AIRS_DIRECT_PROFILE_NAME=bench-direct-api
```

### Output

- `guardrail_bench_<timestamp>.csv` — one row per request
- `guardrail_bench_<timestamp>.summary.json` — percentiles, pairwise deltas, block counts, win rates

Before each run, all legs are probed for reachability. Optional legs (`analyze`, `shield`, `airs`) are skipped gracefully on failure; required legs (`default`, `strict`, `prisma`) abort the run.

---

## Results

West US in-region VM, harmful prompt set (10 benign + 10 adversarial), 20 prompts x 5 repeats = 100 requests per leg.

| Leg | Mean | p50 | p95 | Blocked / 100 |
|-----|------|-----|-----|---------------|
| `analyze` | 68ms | 63ms | 96ms | 20 |
| `shield` | 97ms | 88ms | 129ms | 5 |
| `airs` | 488ms | 247ms | 758ms | 50 |
| `strict` | 1702ms | 1685ms | 3485ms | 20 |
| `prisma` | 1627ms | 1545ms | 3775ms | 15 |
| `default` | 1742ms | 1606ms | 3909ms | 20 |

`analyze` and `default`/`strict` block the same 20 prompts (hate speech, self-harm, violence). `shield` fires only on the jailbreak prompt — expected, as it is scoped to prompt injection, not harm categories. `airs` blocks 50/100, catching phishing, fraud guides, synthesis instructions, and SQL injection that fall outside text:analyze categories. The `airs` p50 of 247ms is the relevant inline pipeline cost; p99 spikes (~5s) reflect occasional GCP routing outliers. `prisma` (Foundry-integrated) blocks only 15/100, consistent with the 300ms Foundry timeout limiting which AIRS detectors fire.

---

## Teardown

```bash
terraform destroy --auto-approve
```

Delete the Prisma AIRS portal registration manually before destroying, or it will leave an orphaned integration entry in the Foundry portal.

---

## References

- [Azure AI Content Safety overview](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/overview)
- [Analyze Text API quickstart](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/quickstart-text)
- [Prompt Shields — jailbreak detection concepts](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection)
- [Groundedness Detection API](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/groundedness)
- [Protected material detection](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/protected-material)
- [Manage guardrails in Azure AI Foundry](https://learn.microsoft.com/en-us/azure/foundry/guardrails/overview)
- [PANW: Integrate with Microsoft Foundry](https://docs.paloaltonetworks.com/ai-runtime-security/administration/integrate-microsoft-foundry)
