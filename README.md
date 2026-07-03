# Azure AI Foundry Guardrail Latency Benchmark

> **⚠️ Disclaimer:** Personal test/development project. Not official, not production-ready, no warranty. Not affiliated with or endorsed by Palo Alto Networks or Microsoft. Use at your own risk.

**License:** MIT — see [LICENSE](LICENSE)

---

## What This Is

When you deploy an AI model, you need guardrails — filters that inspect prompts before the model sees them and block harmful requests. Every guardrail adds latency. This project measures how much each type of guardrail actually costs, and whether the overhead buys better protection.

**Four legs run in parallel per prompt:**

| Leg | Deployment | Guardrail |
|---|---|---|
| `default` | `azure-default` | Microsoft.Default Azure RAI (system-managed) |
| `strict` | `azure-strict` | Custom Azure RAI — low-severity thresholds, prompt + completion |
| `prisma` | `prisma-airs` | Azure RAI pass-through + Prisma AIRS via AI Foundry native integration |
| `airs` | — | Prisma AIRS direct API only (no model call — scan latency baseline) |

**Model:** `gpt-5.4-nano` (`GlobalStandard`, `max_tokens=1`). One token keeps latency dominated by guardrail overhead, not generation.

**Prompts:** 20 prompts — 10 benign, 10 adversarial (synthesis instructions, bomb-making, malware, phishing, jailbreaks).

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
  System-assigned identity → Cognitive Services OpenAI User
  Fully provisioned by cloud-init — no manual setup needed
```

**Endpoint:** `https://cs-foundry-benchmark-subdomain.services.ai.azure.com/openai/v1`

Project names get a shared random 3-char suffix on each apply to avoid Azure soft-delete ETag conflicts on destroy/recreate cycles.

---

## Prerequisites

```bash
brew install terraform azure-cli
npm install -g @cdot65/prisma-airs-cli   # version 2.x

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

> **Subscription limit (confirmed 2026-07-02):** Only **one** third-party guardrail registration is allowed per Azure subscription. If you already have one registered elsewhere, delete it first — the portal will silently fail or error if the slot is occupied.

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
# → update terraform.tfvars, then:
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

The first output line tells you which path was taken.

### On the VM (recommended)

```bash
ssh azureuser@$(terraform output -raw bench_vm_public_ip)
# Wait ~2 min for cloud-init on first boot
cd bench && source .venv/bin/activate
python bench.py -n 20 -r 3 --seed 42
```

### Locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
python bench.py -n 5 -r 1 --seed 42     # smoke test
python bench.py -n 20 -r 3 --seed 42    # full run
```

### .env reference

```bash
AZURE_AI_ENDPOINT=https://cs-foundry-benchmark-subdomain.services.ai.azure.com/openai/v1
DEPLOYMENT_DEFAULT=azure-default
DEPLOYMENT_STRICT=azure-strict
DEPLOYMENT_PRISMA=prisma-airs

# Omit on VM (uses MSI) or with az login
AZURE_AI_API_KEY=<terraform output -raw ai_services_primary_key>

# Enables the airs direct leg
PRISMA_AIRS_DIRECT_API_KEY=<key>
PRISMA_AIRS_DIRECT_PROFILE_NAME=bench-direct-api
```

### Output

- `embedding_bench_<timestamp>.csv` — one row per request
- `embedding_bench_<timestamp>.summary.json` — percentiles, pairwise deltas, block counts, win rates

---

## Results (West US in-region VM, seed=42, 20 prompts × 3 reps)

| Leg | Mean | p50 | Blocked / 60 |
|---|---|---|---|
| `azure-default` | ~1.8s | ~1.7s | ~12 |
| `azure-strict` | ~1.9s | ~1.6s | ~12 |
| `prisma-airs` | ~1.9s | ~1.7s | ~13 |
| `airs direct` | ~334ms | ~314ms | 30 |

Prisma AIRS via Foundry adds ~28ms at the median over default — the mean delta is noisier because generation variance dominates. The `airs direct` leg confirms 100% adversarial catch rate; the Foundry-integrated leg catching 13/30 is consistent with the 300ms timeout constraint. Azure's own RAI blocks ~12/60 on all three Azure legs.

---

## Teardown

```bash
terraform destroy --auto-approve
```

Delete the Prisma AIRS portal registration manually before destroying, or it will leave an orphaned integration entry in the Foundry portal.

---

## References

- [Microsoft: Integrate third-party guardrails](https://learn.microsoft.com/en-us/azure/foundry/guardrails/third-party-integrations)
- [PANW: Integrate with Microsoft Foundry](https://docs.paloaltonetworks.com/ai-runtime-security/administration/integrate-microsoft-foundry)
