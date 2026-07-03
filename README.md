# Azure AI Foundry Guardrail Latency Benchmark

> **⚠️ DISCLAIMER:** This is a personal test/development project. It is not official, not production-ready, carries no warranty, and is not supported by Palo Alto Networks or Microsoft. Use at your own risk.

Terraform + Python benchmark measuring inference latency across three Azure AI Foundry guardrail postures using `gpt-5.4-nano` via the Azure AI Responses API.

---

## License

MIT — see [LICENSE](LICENSE)

---

## What It Measures

Four legs run in parallel per prompt:

| Leg | Deployment | Guardrail |
|---|---|---|
| `default` | `azure-default` | Microsoft.Default RAI (system-managed) |
| `strict` | `azure-strict` | Custom Azure RAI — low-severity thresholds, prompt + completion |
| `prisma` | `prisma-airs` | Azure RAI pass-through + Prisma AIRS via AI Foundry native integration |
| `airs` | — | Prisma AIRS direct API (no Azure, scan-only, no generation) |

Model: `gpt-5.4-nano` (`GlobalStandard`, `max_tokens=1`). Generation is minimal to keep latency dominated by the guardrail path, not output length.

---

## Architecture

```
Azure Subscription
└── rg-mharms-ai-foundry-benchmarks (West US)
    ├── Microsoft.CognitiveServices/accounts  (cs-foundry-benchmark-services)
    │   ├── deployments: azure-default, azure-strict, prisma-airs
    │   ├── raiPolicies: strict-azure-safety-policy, prisma-airs-safety-policy
    │   └── projects: mharms-proj-default, mharms-proj-strict, mharms-proj-prisma
    ├── Microsoft.KeyVault/vaults             (kv-foundry-secrets-bk)
    │   └── secret: Prisma-AIRS-API-Key
    ├── Microsoft.ManagedIdentity             (id-foundry-hub-identity)
    └── Microsoft.Compute/virtualMachines     (vm-bench-westus)  ← in-region runner
```

**Endpoint:** `https://cs-foundry-benchmark-subdomain.services.ai.azure.com/openai/v1`

The Prisma AIRS native integration is registered in the Azure AI Foundry portal (portal-only step, not automatable via ARM API) and attached to the `prisma-airs` deployment via `prisma-airs-safety-policy`.

---

## Prerequisites

- Terraform ≥ 1.5
- Azure CLI, authenticated: `az login && az account set --subscription <id>`
- `airs` CLI (Prisma AIRS key management): `npm install -g @paloaltonetworks/airs-cli`
- Contributor + User Access Administrator on the target subscription

**Register providers (one-time):**

```bash
az provider register --namespace Microsoft.CognitiveServices --wait
az provider register --namespace Microsoft.KeyVault --wait
```

---

## Terraform Setup

```bash
cp example.tfvars terraform.tfvars
# Edit terraform.tfvars — fill in subscription_id, SSH key, Prisma keys

terraform init
terraform plan
terraform apply --auto-approve
```

**Key outputs:**

```bash
terraform output ai_services_endpoint
terraform output -raw ai_services_primary_key
terraform output bench_vm_ssh
```

---

## Prisma AIRS Key Management

Two separate API keys are used so Foundry-mediated scans and direct API scans are distinguishable in AIRS session logs:

| Key | Terraform var | AIRS profile | Used by |
|---|---|---|---|
| Foundry integration | `prisma_airs_api_key_value` | `ai-foundry-prisma-benchmark` | Azure Foundry → Prisma |
| Direct bench leg | `prisma_airs_direct_api_key_value` | `bench-direct-api` | `bench.py` `airs` leg |

**AIRS CLI credentials (from service account CSV):**

```bash
export PANW_MGMT_CLIENT_ID="<sa>@<tsg-id>.iam.panserviceaccount.com"
export PANW_MGMT_CLIENT_SECRET="<secret>"
export PANW_MGMT_TSG_ID="<tsg-id>"
```

**List profiles and keys:**

```bash
airs runtime profiles list
airs runtime api-keys list
```

**Regenerate a key:**

```bash
airs runtime api-keys regenerate <key-id> --interval 90 --unit days
```

Update the new value in `terraform.tfvars`, then taint and reapply the VM to push it through cloud-init:

```bash
terraform taint azurerm_linux_virtual_machine.bench_vm
terraform apply --auto-approve
```

---

## Prisma AIRS + Azure AI Foundry Integration Notes

The native Prisma AIRS guardrail registration is **portal-only** — `raiExternalSafetyProviders` returns `UnsupportedAction` on all ARM API versions. After `terraform apply`:

1. Go to **ai.azure.com → AI Foundry → Guardrails → Integrations**
2. Register Palo Alto Networks — Key Vault: `kv-foundry-secrets-bk`, secret: `Prisma-AIRS-API-Key`, identity: `id-foundry-hub-identity`
3. Assign to the `prisma-airs` deployment

**Known platform constraints:**
- 300 ms inline timeout — Prisma recommends enabling only prompt injection + toxic content detectors
- One third-party guardrail integration per Foundry resource (subscription limit)
- Text only — images/audio not supported
- West US region required for Prisma AIRS

**Reference:**
- [Microsoft: Integrate third-party guardrails](https://learn.microsoft.com/en-us/azure/foundry/guardrails/third-party-integrations)
- [PANW: Integrate with Microsoft Foundry](https://docs.paloaltonetworks.com/ai-runtime-security/administration/integrate-microsoft-foundry)

---

## Running the Benchmark

### Local (laptop)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in values
python bench.py -n 5 -r 1 --seed 42        # smoke test
python bench.py -n 20 -r 3 --seed 42       # full run
```

### In-Region VM

Cloud-init fully provisions the VM on first boot — no manual setup needed.

```bash
ssh azureuser@$(terraform output -raw bench_vm_public_ip)
# Wait ~2 min for cloud-init to finish, then:
cd bench && source .venv/bin/activate
python bench.py -n 20 -r 3 --seed 42
```

### Authentication

`bench.py` discovers credentials at startup (logged on first line):

| Environment | Auth used |
|---|---|
| `AZURE_AI_API_KEY` set in `.env` | API key |
| Azure VM (no key in `.env`) | Managed Identity (`IDENTITY_ENDPOINT` detected) |
| Laptop, no key | `az login` (Azure CLI credential) |

### `.env` reference

```bash
# Required
AZURE_AI_ENDPOINT=https://cs-foundry-benchmark-subdomain.services.ai.azure.com/openai/v1
DEPLOYMENT_DEFAULT=azure-default
DEPLOYMENT_STRICT=azure-strict
DEPLOYMENT_PRISMA=prisma-airs

# Optional — omit on VM (uses MSI)
AZURE_AI_API_KEY=<terraform output -raw ai_services_primary_key>

# Optional — enables airs direct leg
PRISMA_AIRS_DIRECT_API_KEY=<key>
PRISMA_AIRS_DIRECT_PROFILE_NAME=bench-direct-api

# Optional — used by Foundry integration (stored in KV; here for local reference only)
PRISMA_AIRS_API_KEY=<key>
PRISMA_AIRS_PROFILE_NAME=ai-foundry-prisma-benchmark
```

### Output

Each run produces:
- `embedding_bench_<timestamp>.csv` — one row per request (latency, status, request ID, region)
- `embedding_bench_<timestamp>.summary.json` — percentiles, pairwise deltas, fastest-leg win rate

---

## Observed Results (West US, in-region VM, seed=42, 20 prompts × 3 reps)

| Leg | Mean | p50 | Blocked |
|---|---|---|---|
| azure-default | ~1.8s | ~1.7s | ~12/60 (Azure RAI) |
| azure-strict | ~1.9s | ~1.6s | ~12/60 (Azure RAI) |
| prisma-airs | ~1.9s | ~1.7s | ~13/60 (Azure RAI + Prisma) |
| airs (direct) | ~334ms | ~314ms | 30/60 (all adversarial) |

Prisma via Foundry adds ~28ms at the median over default. AIRS direct is fast (scan only, no generation). High variance in generative legs is model latency, not guardrail overhead.
