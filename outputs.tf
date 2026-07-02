# ==============================================================================
#                      CORE API ENDPOINT & CREDENTIALS
# ==============================================================================

output "ai_services_endpoint" {
  value       = azurerm_cognitive_account.ai_services.endpoint
  description = "Base endpoint for all model deployments."
}

output "ai_services_primary_key" {
  value       = azurerm_cognitive_account.ai_services.primary_access_key
  sensitive   = true
  description = "Primary API key shared by all deployments."
}

output "ai_services_secondary_key" {
  value       = azurerm_cognitive_account.ai_services.secondary_access_key
  sensitive   = true
  description = "Secondary API key."
}

# ==============================================================================
#                  PROJECT ENDPOINTS (new Foundry architecture)
# ==============================================================================

output "project_default_endpoint" {
  value       = "https://${var.cognitive_account_name}.services.ai.azure.com/api/projects/${azapi_resource.project_default.name}"
  description = "New Foundry endpoint for the Default Safety project."
}

output "project_strict_endpoint" {
  value       = "https://${var.cognitive_account_name}.services.ai.azure.com/api/projects/${azapi_resource.project_strict.name}"
  description = "New Foundry endpoint for the Strict Safety project."
}

output "project_prisma_endpoint" {
  value       = "https://${var.cognitive_account_name}.services.ai.azure.com/api/projects/${azapi_resource.project_prisma.name}"
  description = "New Foundry endpoint for the Prisma AIRS project."
}

# ==============================================================================
#                     MODEL DEPLOYMENT NAMES
# ==============================================================================

output "deployment_default_model_name" {
  value       = azurerm_cognitive_deployment.model_default.name
  description = "Deployment name for the default RAI posture."
}

output "deployment_strict_model_name" {
  value       = azurerm_cognitive_deployment.model_strict.name
  description = "Deployment name for the strict RAI posture."
}

output "deployment_prisma_model_name" {
  value       = azurerm_cognitive_deployment.model_prisma.name
  description = "Deployment name for the Prisma RAI posture."
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
  description = "SSH command for the benchmark VM."
}

output "bench_vm_run" {
  value       = "ssh azureuser@${azurerm_public_ip.bench_pip.ip_address} 'cd bench && source .venv/bin/activate && python bench.py -n 20 -r 3 --seed 42'"
  description = "One-liner to run the full benchmark remotely once .env is populated."
}
