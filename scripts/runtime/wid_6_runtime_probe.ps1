$ErrorActionPreference = "Stop"

$TargetUrl = $env:CAPPO_URL
if (-not $TargetUrl) {
    $TargetUrl = "https://cappo.veklom.com"
}

Write-Host "Probe starting against $TargetUrl"

$OutputDir = "docs/evidence/runtime"
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$Timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$Summary = @{
    status = "SOURCE_OBSERVED"
    reason = "Scripts created. Runtime endpoints for WID-1..WID-5 not yet deployed or accessible for live verification."
    timestamp = $Timestamp
}
$Summary | ConvertTo-Json -Depth 10 | Set-Content "$OutputDir/wid6_probe_summary.json"

$ServiceIdentity = @{
    service = "cappo-backend"
    deployed_sha = "NOT_VERIFIED"
    environment = "production"
    hostname = "cappo.veklom.com"
}
$ServiceIdentity | ConvertTo-Json -Depth 10 | Set-Content "$OutputDir/wid6_service_identity.json"

$NegativeProbes = @(
    '{"probe": "missing_wid", "status": "NOT_VERIFIED"}',
    '{"probe": "missing_ect", "status": "NOT_VERIFIED"}',
    '{"probe": "missing_wpt", "status": "NOT_VERIFIED"}',
    '{"probe": "missing_veklom_authority", "status": "NOT_VERIFIED"}',
    '{"probe": "pgl_missing_identity_chain", "status": "NOT_VERIFIED"}'
)
$NegativeProbes | Set-Content "$OutputDir/wid6_negative_probes.jsonl"

$PositiveProbe = @{
    probe = "valid_identity_chain"
    status = "NOT_VERIFIED"
}
$PositiveProbe | ConvertTo-Json -Depth 10 | Set-Content "$OutputDir/wid6_positive_probe.json"

$RouteListener = @{
    status = "NOT_VERIFIED"
}
$RouteListener | ConvertTo-Json -Depth 10 | Set-Content "$OutputDir/wid6_route_listener_proof.json"

$Redaction = @{
    redacted_fields = @()
}
$Redaction | ConvertTo-Json -Depth 10 | Set-Content "$OutputDir/wid6_redaction_manifest.json"

$ArtifactHashes = @{
    hashes = @{}
}
$ArtifactHashes | ConvertTo-Json -Depth 10 | Set-Content "$OutputDir/wid6_artifact_hashes.json"

Write-Host "WID-6 scripts generated. Runtime proof is SOURCE_OBSERVED."
