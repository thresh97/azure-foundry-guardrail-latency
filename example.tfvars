# ==============================================================================
#                  AZURE SUBSCRIPTION & LOCATION CONFIGURATION
# ==============================================================================
subscription_id             = "00000000-0000-0000-0000-000000000000"
location                    = "eastus2"
resource_group_name         = "rg-ai-foundry-benchmarks"

# ==============================================================================
#                     CORE SERVICES & SECURITY KEYS
# ==============================================================================
cognitive_account_name      = "cs-foundry-benchmark-services"
custom_subdomain_name       = "cs-foundry-benchmark-subdomain"
user_assigned_identity_name = "id-foundry-hub-identity"
storage_account_name        = "stfoundrybenchmarks"
key_vault_name              = "kv-foundry-secrets-bk"

# SECURE CREDENTIALS
# Value can alternatively be passed via environment variable: export TF_VAR_prisma_airs_api_key_value="..."
prisma_airs_api_key_value   = "prisma-mock-api-key-string-xxx-yyy"
prisma_airs_url             = "https://api.prismacloud.io/v1/scan"

# ==============================================================================
#              OPTION A: DEFAULT GENERATIVE CHAT MODEL (ACTIVE)
# ==============================================================================
# Standard GPT model setup for complete text interaction scanning
model_name                  = "gpt-4o"
model_version               = "2024-05-13"

# ==============================================================================
#          OPTION B: LOW-LATENCY EMBEDDING MODEL (COMMENTED OUT)
# ==============================================================================
# Use this model configuration to isolate network and proxy overhead without text generation latency.
# model_name                  = "text-embedding-3-small"
# model_version               = "1"

# ==============================================================================
#                  BENCHMARK VM — IN-REGION RUNNER
# ==============================================================================
# CIDRs allowed to SSH. Add your egress IP(s): curl -s ifconfig.me
allowed_ssh_ips             = ["0.0.0.0/32"]   # REPLACE with your actual IP(s)

# Your SSH public key: cat ~/.ssh/id_ed25519.pub
vm_admin_ssh_public_key     = "ssh-ed25519 AAAA... user@host"

# Optional: override VM size (default Standard_B2s = 2 vCPU / 4 GB)
# vm_size                   = "Standard_B2s"
