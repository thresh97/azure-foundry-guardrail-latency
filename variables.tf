variable "subscription_id" {
  type        = string
  description = "The target Azure subscription ID for deployment."
}

variable "location" {
  type        = string
  default     = "eastus2"
  description = "Target Azure region for all provisioned services."
}

variable "resource_group_name" {
  type        = string
  default     = "rg-ai-foundry-benchmarks"
  description = "Name of the resource group."
}

variable "cognitive_account_name" {
  type        = string
  default     = "cs-foundry-benchmark-services"
  description = "Name of the parent Azure Cognitive Services (AI Services) instance."
}

variable "custom_subdomain_name" {
  type        = string
  default     = "cs-foundry-benchmark-subdomain"
  description = "Unique custom subdomain for Entra ID authentication and API access."
}

variable "user_assigned_identity_name" {
  type        = string
  default     = "id-foundry-hub-identity"
  description = "Name of the User Assigned Identity used by the AI Services Account."
}

variable "storage_account_name" {
  type        = string
  default     = "stfoundrybenchmarks"
  description = "Globally unique storage account name used for AI Foundry logging/assets."
}

variable "key_vault_name" {
  type        = string
  default     = "kv-foundry-secrets-bk"
  description = "Globally unique Key Vault name to store safety secrets securely."
}

variable "prisma_airs_api_key_value" {
  type        = string
  sensitive   = true
  description = "Secret API key string used to connect to Palo Alto Networks Prisma AIRS scanner endpoint."
}

variable "prisma_airs_url" {
  type        = string
  default     = "https://api.prismacloud.io/v1/scan"
  description = "Prisma AIRS runtime gateway scanning URL."
}

variable "model_name" {
  type        = string
  default     = "gpt-4o"
  description = "Target model definition from the Microsoft AI Catalog."
}

variable "model_version" {
  type        = string
  default     = "2024-05-13"
  description = "Version of the deployed model."
}

variable "tags" {
  type        = map(string)
  default     = {
    Environment = "benchmarking"
    ManagedBy   = "terraform"
    Team        = "secops"
  }
  description = "Common tags mapped across all resources."
}
