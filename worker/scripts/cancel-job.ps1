param(
    [Parameter(Mandatory = $true)]
    [guid]$JobId,
    [ValidateSet('production', 'staging')]
    [string]$Environment = 'production'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$secretName = if ($Environment -eq 'production') { 'prod-owner-token' } else { 'owner-token' }
$secretPath = Join-Path $PSScriptRoot "../.secrets/$secretName"
if (-not (Test-Path -LiteralPath $secretPath)) {
    throw "Local OpenShelf owner key was not found for $Environment."
}
$ownerKey = (Get-Content -LiteralPath $secretPath -Raw).Trim()
if ($ownerKey.Length -lt 24) { throw 'Local OpenShelf owner key is invalid.' }

$apiHost = if ($Environment -eq 'production') { 'openshelf-api' } else { 'openshelf-api-staging' }
$uri = "https://$apiHost.johnkoeh.workers.dev/api/v1/generation-jobs/$JobId/cancel"
$result = Invoke-RestMethod -Uri $uri -Method Post -Headers @{ Authorization = "Bearer $ownerKey" }
Write-Output "Job $($result.id): $($result.state)"
