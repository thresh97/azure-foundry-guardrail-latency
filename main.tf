# 1. Active Client Context Reference
data "azurerm_client_config" "current" {}

# 2. Resource Group
resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

# 3. Managed Identity
resource "azurerm_user_assigned_identity" "ai_identity" {
  name                = var.user_assigned_identity_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags
}

# 4. Key Vault
resource "azurerm_key_vault" "kv" {
  name                       = var.key_vault_name
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  rbac_authorization_enabled = true
  tags                       = var.tags
}

# 5. Deployer Key Vault Access
resource "azurerm_role_assignment" "deployer_kv_admin" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# 6. Managed Identity Key Vault Read Access
resource "azurerm_role_assignment" "identity_kv_reader" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.ai_identity.principal_id
}

# 7. Prisma AIRS API Key in Key Vault
resource "azurerm_key_vault_secret" "prisma_api_key" {
  name         = "Prisma-AIRS-API-Key"
  value        = var.prisma_airs_api_key_value
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [azurerm_role_assignment.deployer_kv_admin]
}

# ==============================================================================
#            AZURE AI SERVICES ACCOUNT (new Foundry architecture)
# ==============================================================================

# 8. AI Services Account
resource "azurerm_cognitive_account" "ai_services" {
  name                  = var.cognitive_account_name
  location              = azurerm_resource_group.rg.location
  resource_group_name   = azurerm_resource_group.rg.name
  kind                  = "AIServices"
  sku_name              = "S0"
  custom_subdomain_name = var.custom_subdomain_name
  public_network_access_enabled = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }

  tags = var.tags
}

# 8b. Enable project management — required for new Foundry project type
resource "azapi_update_resource" "ai_services_project_mgmt" {
  type        = "Microsoft.CognitiveServices/accounts@2025-06-01"
  resource_id = azurerm_cognitive_account.ai_services.id

  body = {
    properties = {
      allowProjectManagement = true
    }
  }
}

# Shared suffix — same 3 chars across all three projects; changes on each destroy/apply
resource "random_string" "suffix" {
  length  = 3
  lower   = true
  upper   = false
  numeric = false
  special = false
}

# ==============================================================================
#            AI FOUNDRY PROJECTS (Microsoft.CognitiveServices/accounts/projects)
# ==============================================================================

# Project 1: Default Safety Baseline
resource "azapi_resource" "project_default" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name                      = "${var.prefix}-proj-default-${random_string.suffix.result}"
  parent_id                 = azurerm_cognitive_account.ai_services.id
  location                  = var.location
  schema_validation_enabled = false

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }

  body = {
    properties = {
      publicNetworkAccess = "Enabled"
    }
  }

  depends_on = [azapi_update_resource.ai_services_project_mgmt]
}

# Project 2: Strict Azure Content Filters
resource "azapi_resource" "project_strict" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name                      = "${var.prefix}-proj-strict-${random_string.suffix.result}"
  parent_id                 = azurerm_cognitive_account.ai_services.id
  location                  = var.location
  schema_validation_enabled = false

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }

  body = {
    properties = {
      publicNetworkAccess = "Enabled"
    }
  }

  depends_on = [azapi_update_resource.ai_services_project_mgmt]
}

# Project 3: Prisma AIRS External Guardrail
resource "azapi_resource" "project_prisma" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name                      = "${var.prefix}-proj-prisma-${random_string.suffix.result}"
  parent_id                 = azurerm_cognitive_account.ai_services.id
  location                  = var.location
  schema_validation_enabled = false

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ai_identity.id]
  }

  body = {
    properties = {
      publicNetworkAccess = "Enabled"
    }
  }

  depends_on = [azapi_update_resource.ai_services_project_mgmt]
}

# ==============================================================================
#         RESPONSIBLE AI POLICIES
# ==============================================================================

# Strict: low-severity thresholds both sides
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

# Prisma: standard Azure policy — Prisma registration done via new Foundry portal post-apply
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
#             MODEL DEPLOYMENTS
# ==============================================================================

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

  depends_on = [azapi_resource.strict_policy]
}

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

  depends_on = [azapi_resource.prisma_policy]
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
