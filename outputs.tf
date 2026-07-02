# ==============================================================================
#                      CORE API ENDPOINT & CREDENTIALS
# ==============================================================================

output "ai_services_endpoint" {
  value       = azurerm_cognitive_account.ai_services.endpoint
  description = "The unified base endpoint URL for your Azure AI Services/Foundry account."
}

output "ai_services_primary_key" {
  value       = azurerm_cognitive_account.ai_services.primary_access_key
  sensitive   = true
  description = "The primary API key shared by all projects and model deployments for API header authentication."
}

output "ai_services_secondary_key" {
  value       = azurerm_cognitive_account.ai_services.secondary_access_key
  sensitive   = true
  description = "The fallback secondary API key."
}

# ==============================================================================
#                  PROJECT-SPECIFIC CONNECTION STRINGS
# ==============================================================================

output "project_default_connection_string" {
  value       = "${var.location}.api.azureml.ms;${var.subscription_id};${var.resource_group_name};${azurerm_ai_foundry_project.project_default.name}"
  description = "Pre-formatted connection string for the Default Safety Project (to use in python SDK)."
}

output "project_strict_connection_string" {
  value       = "${var.location}.api.azureml.ms;${var.subscription_id};${var.resource_group_name};${azurerm_ai_foundry_project.project_strict.name}"
  description = "Pre-formatted connection string for the Strict Azure Guardrails Project."
}

output "project_prisma_connection_string" {
  value       = "${var.location}.api.azureml.ms;${var.subscription_id};${var.resource_group_name};${azurerm_ai_foundry_project.project_prisma.name}"
  description = "Pre-formatted connection string for the Prisma AIRS Gateway Project."
}

# ==============================================================================
#                     DEPLOYED MODEL ENGINES (TARGET NAMES)
# ==============================================================================

output "deployment_default_model_name" {
  value       = azurerm_cognitive_deployment.model_default.name
  description = "Target deployment model string for benchmarking the baseline Microsoft.Default policy."
}

output "deployment_strict_model_name" {
  value       = azurerm_cognitive_deployment.model_strict.name
  description = "Target deployment model string for benchmarking the strict custom Azure policy."
}

output "deployment_prisma_model_name" {
  value       = azurerm_cognitive_deployment.model_prisma.name
  description = "Target deployment model string for benchmarking the synchronous Prisma AIRS proxy policy."
}

# ==============================================================================
#                        BENCHMARK VM
# ==============================================================================

output "bench_vm_public_ip" {
  value       = azurerm_public_ip.bench_pip.ip_address
  description = "Public IP of the in-region benchmark VM."
}

output "bench_vm_ssh" {
  value       = "ssh azureuser@${azurerm_public_ip.bench_pip.ip_address}"
  description = "Ready-to-run SSH command for the benchmark VM."
}

output "bench_vm_run" {
  value       = "ssh azureuser@${azurerm_public_ip.bench_pip.ip_address} 'cd bench && source .venv/bin/activate && python bench.py -n 20 -r 3 --seed 42'"
  description = "One-liner to run the full benchmark remotely once .env is populated."
}
