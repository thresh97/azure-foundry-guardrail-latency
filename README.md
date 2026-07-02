Azure AI Foundry Multi-Guardrail Latency Deployment Suite

This directory contains complete, non-modular Terraform configuration files to provision an enterprise-grade parent Azure AI Services Hub hosting three isolated Azure AI Foundry Projects. Each project implements a distinct security/safety posture to let you isolate network and classification latency over your inference pipelines.

Architectural Posture Setups

Model: text-embedding-3-small (version 1). Embeddings are used to isolate RAI policy latency overhead without text-generation jitter.

Project 1: Default Safety Baseline (proj-benchmark-default-safety)

Deployment: embedding-default-endpoint. Uses the system-managed Microsoft.Default RAI policy — Medium-severity blocking across Hate, Sexual, Violence, Self-Harm.

Project 2: Strict Azure Content Filters (proj-benchmark-strict-safety)

Deployment: embedding-strict-endpoint. Custom RAI policy with Low-severity thresholds on both Prompt and Completion sides, plus Jailbreak blocking.

Project 3: Prisma-Policy Safety (proj-benchmark-prisma-safety)

Deployment: embedding-prisma-endpoint. Uses a standard RAI policy (Medium thresholds, Microsoft.Default base) as the Azure-side gate. External Prisma AIRS scanning is applied at the application layer — see Benchmark section below.

Note: raiExternalSafetyProviders (the Azure-native Prisma integration API) is not yet GA and returns UnsupportedAction on all accounts. The Prisma AIRS security profile ai-foundry-prisma-benchmark (UUID: <your-profile-uuid>) is wired into the application-layer scan in bench.py instead.


The Identity & Governance Rule (Azure AD via AZ CLI)

Because your target subscription strictly requires all Microsoft Entra ID (Azure AD) directories, enterprise configurations, and authorization structures to be created outside Terraform, this HCL code contains no references to the azuread provider.

All core authorization mappings are established using Azure's native User Assigned Managed Identities (azurerm_user_assigned_identity), Role Assignments (azurerm_role_assignment), and Declarative Resource Providers (azapi_resource).


Prerequisites — Run Before Terraform

1. Authenticate and Set Subscription

az login
az account set --subscription "<your-subscription-id>"


2. Verify Role Permissions

The identity running Terraform needs Owner or (Contributor + User Access Administrator) at the subscription scope to create role assignments. Confirm your effective roles:

az role assignment list --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --subscription "<your-subscription-id>" \
  --output table


3. Register Required Resource Providers

These providers must be registered before Terraform can create resources under them. The commands are idempotent — safe to re-run.

az provider register --namespace Microsoft.CognitiveServices --wait
az provider register --namespace Microsoft.MachineLearningServices --wait
az provider register --namespace Microsoft.KeyVault --wait
az provider register --namespace Microsoft.Storage --wait

Verify registration:

az provider show --namespace Microsoft.CognitiveServices --query registrationState
az provider show --namespace Microsoft.MachineLearningServices --query registrationState


4. (Optional) Register Custom Directory Parameters

If your local corporate security rules require mapping directory groups or custom enterprise app integrations to access this space:

# Create custom security group
az ad group create \
  --display-name "SecOps-AI-Auditors" \
  --mail-nickname "ai-auditors"

# Bind group access to the deployment scope using Azure CLI
az role assignment create \
  --assignee "your-ad-group-object-id" \
  --role "Cognitive Services User" \
  --scope "/subscriptions/<your-subscription-id>/resourceGroups/rg-ai-foundry-benchmarks"


Prisma AIRS API Key

The Prisma AIRS API key is stored in terraform.tfvars as prisma_airs_api_key_value and is written to Key Vault by Terraform. It corresponds to the azure-ai-security-apk API key in the <your-service-account> tenant (TSG ID: <your-tsg-id>).

To retrieve or regenerate the key, use the airs CLI:

1. Install the CLI

npm install -g @cdot65/prisma-airs-cli


2. Set Credentials (from your <your-service-account> service account CSV)

export PANW_MGMT_CLIENT_ID="<your-sa>@<your-tsg-id>.iam.panserviceaccount.com"
export PANW_MGMT_CLIENT_SECRET="<your-client-secret>"
export PANW_MGMT_TSG_ID="<your-tsg-id>"


3. Regenerate the Key

airs runtime api-keys regenerate <your-api-key-id> \
  --interval 90 --unit days

Copy the Key: value from the output into terraform.tfvars.

The associated Prisma AIRS security profile for this benchmark is ai-foundry-prisma-benchmark (UUID: <your-profile-uuid>).


Deployment Instructions

Prerequisites

Terraform CLI (v1.5.0 or newer) installed.

Azure CLI installed and authenticated (see above).

airs CLI installed (npm install -g @cdot65/prisma-airs-cli).

Execution Plan

Initialize Providers & Modules:

terraform init


Populate Input Arguments:
Copy the sample tfvars file over if starting fresh:

cp example.tfvars terraform.tfvars

Open terraform.tfvars and verify all values. The prisma_airs_api_key_value must be a live key (see Prisma AIRS section above).

Alternatively, pass the key via environment variable to avoid writing it to disk:

export TF_VAR_prisma_airs_api_key_value="<key>"


Verify Deployment Blueprint:

terraform plan


Deploy the Architecture:

terraform apply --auto-approve


Prisma AIRS + Azure AI Foundry Integration Notes
(Knowledge captured: 2026-07-02)

The third-party guardrail integration is a BYOL (Bring Your Own License) model — Azure does not resell or broker Prisma AIRS; you must supply an existing license and API key.

Subscription / Tenant Limit: Neither the Microsoft Foundry docs nor the PANW docs state a limit of one integration per subscription or tenant. In practice, however, the Azure portal currently enforces a single active third-party integration per Foundry resource. If you hit this in the portal, work around it via the REST API or azapi_resource blocks (as this repo does).

Known Platform Limitations:

- Latency budget: Any Prisma AIRS detection round-trip exceeding 300 ms is rejected by the Foundry gateway. To stay within this threshold, PANW recommends enabling only prompt injection and toxic content detectors on the security profile attached to Foundry — do not enable the full detector suite.
- Single-detector verdicts: The first detector to flag a threat short-circuits the pipeline; remaining detectors do not run. Only that first threat type appears in violation and session logs.
- Text only: Images, audio, and other modalities are not supported by the integration at this time.
- No agent tool call I/O: Currently only prompts and model completions are scanned. Tool calls and tool responses are planned for a future release.
- Regional availability: Prisma AIRS is supported only in a subset of Azure regions. Mismatching regions between your Foundry project and the Prisma AIRS endpoint adds latency and risks timeouts.

  Supported regions (Prisma AIRS):
  - US: West US, West US 3, West Central US
  - Europe: West Europe, North Europe, France Central, Germany West Central, Italy North, Sweden Central, Norway East, Switzerland North, Switzerland West, UK South, UK West
  - Asia: South India, Southeast Asia, East Asia

Reference Links:
- Microsoft Foundry — Integrate third-party guardrails: https://learn.microsoft.com/en-us/azure/foundry/guardrails/third-party-integrations
- Palo Alto Networks — Integrate with Microsoft Foundry: https://docs.paloaltonetworks.com/ai-runtime-security/administration/integrate-microsoft-foundry
- Prisma AIRS onboarding (API intercept): https://docs.paloaltonetworks.com/ai-runtime-security/activation-and-onboarding/ai-runtime-security-api-intercept-overview/onboard-api-runtime-security-api-intercept-in-scm


Benchmark

bench.py runs the three embedding deployments in parallel per prompt, producing a long-format CSV and a JSON summary with percentiles and per-pair latency deltas.

Setup

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in your API key (or pull from: terraform output -raw ai_services_primary_key)


.env variables:

AZURE_AI_ENDPOINT=https://cs-foundry-benchmark-subdomain.cognitiveservices.azure.com/
AZURE_AI_API_KEY=<primary key>
DEPLOYMENT_DEFAULT=embedding-default-endpoint
DEPLOYMENT_STRICT=embedding-strict-endpoint
DEPLOYMENT_PRISMA=embedding-prisma-endpoint


Run

# Quick smoke test — 5 prompts, 1 repeat
python bench.py -n 5 --seed 1

# Full run — all 20 prompts, 3 repeats, fixed seed
python bench.py -n 20 -r 3 --seed 42 -o results/run1.csv

# Include adversarial prompts with a delay between rounds
python bench.py -n 20 -r 2 --delay 0.5 --seed 42


Output: <output>.csv (one row per request) + <output>.summary.json (percentiles, pairwise deltas, win rate).

Prompts file (prompts.txt) has 10 benign + 10 adversarial prompts. Lines starting with # are skipped.


Important Caveats (Production vs. Dev/Test Support)

Modified Content Filters: Bypassing or disabling Azure's internal Microsoft.Default content safety guardrails entirely to run baseline metrics without any safety filters requires subscription-level approval. You must submit a "Modified Content Filters" waiver application to Microsoft before the gateway allows deploying a completely unmonitored model endpoint.

Prisma AIRS Webhook Latency: Performance metrics collected on the Prisma AIRS endpoint represent a compound sum of network transit, TLS negotiation, and external security processing. Run your tests sequentially to factor out local network congestion anomalies.

Capitalization Sensitivity (Terraform Drift): The underlying Azure Cognitive Services Resource Manager API is highly sensitive to the casing of category fields. Content filter fields (Hate, Violence, Sexual, SelfHarm, Jailbreak) must remain in PascalCase within HCL blocks. Writing them in lowercase will trigger continuous drift detection on subsequent terraform plan operations.

raiExternalSafetyProviders: This resource type is NOT supported by the Azure API — all accounts return UnsupportedAction 400 (confirmed 2026-07-02). The azapi_resource.prisma_provider block has been removed from main.tf. Prisma scanning runs at the application layer via bench.py instead.

Identity Requirement: azurerm_ai_foundry and azurerm_ai_foundry_project both require type = "SystemAssigned, UserAssigned". Using UserAssigned-only causes a persistent InternalServerError: Received 400 from a service request after ~2 min during workspace provisioning — the workspace provisioner needs a SystemAssigned identity to initialize storage.
