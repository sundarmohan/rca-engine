# Diagnostics execution policy

All diagnostics must be implemented as named, typed SDK/API operations or reviewed query templates. CLI/PowerShell support, if later needed, must use a fixed command registry with argument validation, process timeouts, output limits, no shell interpolation, and no user-supplied command strings. No unrestricted shell, arbitrary scripts, restart commands, deployments, or configuration writes.
