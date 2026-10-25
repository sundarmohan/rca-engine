# Azure provider adapter (planned)

Implement read-only adapters using Azure SDKs and the Log Analytics Query API. Use a dedicated Microsoft Entra service principal or managed identity with only the required Reader/Monitoring Reader and workspace query permissions. Never grant Contributor/Owner for investigation.

Planned modules: resource inventory, Azure Monitor alerts/metrics, Log Analytics KQL, Activity Log, App Service, VM, Azure SQL, AKS and network diagnostics.
