# 1. Active Client Context Reference
data "azurerm_client_config" "current" {}

# 2. Resource Group
resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

# 3. Managed Identity (Required for Key Vault secrets and parent resource associations)
resource "azurerm_user_assigned_identity" "ai_identity" {
  name                = var.user_assigned_identity_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags
}

# 4. Storage Account (Core dependency for the AI Foundry Hub environment)
resource "azurerm_storage_account" "st" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  tags                     = var.tags

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices", "Logging", "Metrics"]
  }
}

# 5. Key Vault (Configured strictly for Azure RBAC auth; legacy access policies removed)
resource "azurerm_key_vault" "kv" {
  name                        = var.key_vault_name
  location                    = azurerm_resource_group.rg.location
  resource_group_name         = azurerm_resource_group.rg.name
  tenant_id                   = data.azurerm_client_config.current.tenant_id
  sku_name                    = "standard"
  purge_protection_enabled    = false
  soft_delete_retention_days  = 7
  rbac_authorization_enabled  = true

  tags = var.tags
}

# 6. Deployer Key Vault Access (Using RBAC Roles instead of legacy Access Policies)
resource "azurerm_role_assignment" "deployer_kv_admin" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# 7. Managed Identity Key Vault Read Access
resource "azurerm_role_assignment" "identity_kv_reader" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.ai_identity.principal_id
}

# 7b. Managed Identity Storage Access (required for AI Foundry hub initialization)
resource "azurerm_role_assignment" "identity_st_contributor" {
  scope                = azurerm_storage_account.st.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.ai_identity.principal_id
}

# 8. Create Key Vault Secret for Prisma AIRS Integration
resource "azurerm_key_vault_secret" "prisma_api_key" {
  name         = "Prisma-AIRS-API-Key"
  value        = var.prisma_airs_api_key_value
  key_vault_id = azurerm_key_vault.kv.id

  depends_on = [azurerm_role_assignment.deployer_kv_admin]
}

# 9. Parent Cognitive Services (AI Services) Account
resource "azurerm_cognitive_account" "ai_services" {
  name                  = var.cognitive_account_name
  location              = azurerm_resource_group.rg.location
  resource_group_name   = azurerm_resource_group.rg.name
  kind                  = "AIServices"
  sku_name              = "S0"
  custom_subdomain_name = var.custom_subdomain_name

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }

  tags = var.tags
}

# ==============================================================================
#            NATIVE AZURE AI FOUNDRY ARCHITECTURE
# ==============================================================================

# Hub Control Plane Container
resource "azurerm_ai_foundry" "ai_hub" {
  name                = "ai-hub-benchmark-control"
  location            = azurerm_cognitive_account.ai_services.location
  resource_group_name = azurerm_resource_group.rg.name
  key_vault_id        = azurerm_key_vault.kv.id
  storage_account_id  = azurerm_storage_account.st.id

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }

  tags = var.tags

  depends_on = [azurerm_role_assignment.identity_st_contributor]
}

# Connection: Hub → AI Services Account (surfaces deployments in Foundry portal/SDK)
resource "azapi_resource" "ai_services_hub_connection" {
  type      = "Microsoft.MachineLearningServices/workspaces/connections@2024-07-01-preview"
  name      = "ai-services-connection"
  parent_id = azurerm_ai_foundry.ai_hub.id

  body = {
    properties = {
      authType = "ApiKey"
      category  = "AIServices"
      target    = azurerm_cognitive_account.ai_services.endpoint
      credentials = {
        key = azurerm_cognitive_account.ai_services.primary_access_key
      }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_cognitive_account.ai_services.id
      }
    }
  }

  depends_on = [azurerm_cognitive_account.ai_services]
}

# Project 1: Default Safety Baseline Project
resource "azurerm_ai_foundry_project" "project_default" {
  name               = "proj-benchmark-default-safety"
  location           = azurerm_cognitive_account.ai_services.location
  ai_services_hub_id = azurerm_ai_foundry.ai_hub.id

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }
}

# Project 2: All Strict Azure Content Filter Guardrails Project
resource "azurerm_ai_foundry_project" "project_strict" {
  name               = "proj-benchmark-strict-safety"
  location           = azurerm_cognitive_account.ai_services.location
  ai_services_hub_id = azurerm_ai_foundry.ai_hub.id

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }
}

# Project 3: Prisma AIRS Synchronous Proxy Project
resource "azurerm_ai_foundry_project" "project_prisma" {
  name               = "proj-benchmark-prisma-safety"
  location           = azurerm_cognitive_account.ai_services.location
  ai_services_hub_id = azurerm_ai_foundry.ai_hub.id

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }
}

# ==============================================================================
#         RESPONSIBLE AI POLICIES & PROVIDERS DEPLOYED VIA AZAPI
# ==============================================================================

# Setup 1: Strict Azure Content Filter Guardrail
resource "azapi_resource" "strict_policy" {
  type      = "Microsoft.CognitiveServices/accounts/raiPolicies@2024-10-01"
  name      = "strict-azure-safety-policy"
  parent_id = azurerm_cognitive_account.ai_services.id

  body = {
    properties = {
      basePolicyName = "Microsoft.Default"
      mode           = "Blocking"
      contentFilters = [
        { name = "Hate",      severityThreshold = "Low",  blocking = true, enabled = true, source = "Prompt" },
        { name = "Violence",  severityThreshold = "Low",  blocking = true, enabled = true, source = "Prompt" },
        { name = "Sexual",    severityThreshold = "Low",  blocking = true, enabled = true, source = "Prompt" },
        { name = "SelfHarm",  severityThreshold = "Low",  blocking = true, enabled = true, source = "Prompt" },
        { name = "Jailbreak", severityThreshold = "High", blocking = true, enabled = true, source = "Prompt" },
        { name = "Hate",      severityThreshold = "Low",  blocking = true, enabled = true, source = "Completion" },
        { name = "Violence",  severityThreshold = "Low",  blocking = true, enabled = true, source = "Completion" },
        { name = "Sexual",    severityThreshold = "Low",  blocking = true, enabled = true, source = "Completion" },
        { name = "SelfHarm",  severityThreshold = "Low",  blocking = true, enabled = true, source = "Completion" }
      ]
    }
  }
}

# Setup 2: RAI Policy for Prisma project (raiExternalSafetyProviders not yet GA — using standard policy)
resource "azapi_resource" "prisma_policy" {
  type      = "Microsoft.CognitiveServices/accounts/raiPolicies@2024-10-01"
  name      = "prisma-airs-safety-policy"
  parent_id = azurerm_cognitive_account.ai_services.id

  body = {
    properties = {
      basePolicyName = "Microsoft.Default"
      mode           = "Blocking"
      contentFilters = [
        { name = "Hate",      severityThreshold = "Medium", blocking = true, enabled = true, source = "Prompt" },
        { name = "Violence",  severityThreshold = "Medium", blocking = true, enabled = true, source = "Prompt" },
        { name = "Sexual",    severityThreshold = "Medium", blocking = true, enabled = true, source = "Prompt" },
        { name = "SelfHarm",  severityThreshold = "Medium", blocking = true, enabled = true, source = "Prompt" },
        { name = "Jailbreak", severityThreshold = "High",   blocking = true, enabled = true, source = "Prompt" },
        { name = "Hate",      severityThreshold = "Medium", blocking = true, enabled = true, source = "Completion" },
        { name = "Violence",  severityThreshold = "Medium", blocking = true, enabled = true, source = "Completion" },
        { name = "Sexual",    severityThreshold = "Medium", blocking = true, enabled = true, source = "Completion" },
        { name = "SelfHarm",  severityThreshold = "Medium", blocking = true, enabled = true, source = "Completion" }
      ]
    }
  }
}

# ==============================================================================
#             DEPLOYMENTS MAPPED TO EACH CORRESPONDING BENCHMARK PROJECT
# ==============================================================================

# Model Deployment 1: Baseline Defaults
resource "azurerm_cognitive_deployment" "model_default" {
  name                 = "embedding-default-endpoint"
  cognitive_account_id = azurerm_cognitive_account.ai_services.id

  model {
    format  = "OpenAI"
    name    = var.model_name
    version = var.model_version
  }

  sku {
    name     = "Standard"
    capacity = 10
  }
}

# Model Deployment 2: Custom Strict Azure Safety Policy
resource "azurerm_cognitive_deployment" "model_strict" {
  name                 = "embedding-strict-endpoint"
  cognitive_account_id = azurerm_cognitive_account.ai_services.id
  rai_policy_name      = "strict-azure-safety-policy"

  model {
    format  = "OpenAI"
    name    = var.model_name
    version = var.model_version
  }

  sku {
    name     = "Standard"
    capacity = 10
  }

  depends_on = [
    azapi_resource.strict_policy
  ]
}

# Model Deployment 3: Prisma AIRS Synchronous Proxy Safety
resource "azurerm_cognitive_deployment" "model_prisma" {
  name                 = "embedding-prisma-endpoint"
  cognitive_account_id = azurerm_cognitive_account.ai_services.id
  rai_policy_name      = "prisma-airs-safety-policy"

  model {
    format  = "OpenAI"
    name    = var.model_name
    version = var.model_version
  }

  sku {
    name     = "Standard"
    capacity = 10
  }

  depends_on = [
    azapi_resource.prisma_policy
  ]
}
