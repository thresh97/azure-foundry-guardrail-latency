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

# ==============================================================================
#           BENCHMARK VM — IN-REGION LATENCY MEASUREMENT (West US)
# ==============================================================================

resource "azurerm_virtual_network" "bench_vnet" {
  name                = "vnet-bench-westus"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  address_space       = ["10.10.0.0/24"]
  tags                = var.tags
}

resource "azurerm_subnet" "bench_subnet" {
  name                 = "snet-bench"
  resource_group_name  = azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.bench_vnet.name
  address_prefixes     = ["10.10.0.0/27"]
}

resource "azurerm_public_ip" "bench_pip" {
  name                = "pip-bench-vm"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_network_security_group" "bench_nsg" {
  name                = "nsg-bench-vm"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name

  security_rule {
    name                       = "AllowSSH"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefixes    = var.allowed_ssh_ips
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "DenyAllInbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  tags = var.tags
}

resource "azurerm_network_interface" "bench_nic" {
  name                = "nic-bench-vm"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name

  ip_configuration {
    name                          = "ipconfig-bench"
    subnet_id                     = azurerm_subnet.bench_subnet.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.bench_pip.id
  }

  tags = var.tags
}

resource "azurerm_network_interface_security_group_association" "bench_nic_nsg" {
  network_interface_id      = azurerm_network_interface.bench_nic.id
  network_security_group_id = azurerm_network_security_group.bench_nsg.id
}

resource "azurerm_linux_virtual_machine" "bench_vm" {
  name                            = "vm-bench-westus"
  location                        = azurerm_resource_group.rg.location
  resource_group_name             = azurerm_resource_group.rg.name
  size                            = var.vm_size
  admin_username                  = "azureuser"
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.bench_nic.id]

  admin_ssh_key {
    username   = "azureuser"
    public_key = var.vm_admin_ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
    disk_size_gb         = 30
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  custom_data = base64encode(templatefile("${path.module}/cloud-init.tpl", {
    ai_endpoint        = azurerm_cognitive_account.ai_services.endpoint
    deployment_default = azurerm_cognitive_deployment.model_default.name
    deployment_strict  = azurerm_cognitive_deployment.model_strict.name
    deployment_prisma  = azurerm_cognitive_deployment.model_prisma.name
  }))

  tags = var.tags
}
