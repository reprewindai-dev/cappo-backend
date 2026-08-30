# Veklom Sovereign Capability OS - Hostile Proof Pack
$ErrorActionPreference = "Stop"

$CAPPO_URL = "http://127.0.0.1:8002"
$BYOS_URL = "http://127.0.0.1:8088"
$OUTPUT_DIR = "C:\Users\antho\.windsurf\cappo-backend\evidence\runtime"

if (-not (Test-Path $OUTPUT_DIR)) { New-Item -ItemType Directory -Path $OUTPUT_DIR | Out-Null }
$PROOF_LOG = "$OUTPUT_DIR\p5_live_fire_http_proof.jsonl"
if (Test-Path $PROOF_LOG) { Remove-Item $PROOF_LOG }

Write-Host "
[VEKLOM] Starting Undeniable Proof Pack Execution (Internal)...
" -ForegroundColor Cyan

$Global:Summary = @()
$Global:AllPassed = $true

function Log-Evidence {
    param($TestName, $RequestUrl, $Method, $Status, $Expected, $Body, $ResponseContent)
    $passed = ($Status -eq $Expected)
    $evidence = @{
        timestamp = (Get-Date).ToString("o")
        test_name = $TestName
        url = $RequestUrl
        method = $Method
        status = $Status
        expected = $Expected
        passed = $passed
        request_body = $Body
        response = $ResponseContent
    }
    $evidenceJson = $evidence | ConvertTo-Json -Compress
    Add-Content -Path $PROOF_LOG -Value $evidenceJson
    $Global:Summary += $evidence
    if (-not $passed) { $Global:AllPassed = $false }
    return $passed
}

function Run-Test {
    param([string]$Name, [string]$Uri, [string]$Method, [int]$ExpectedStatus, [string]$Body, [hashtable]$Headers)
    
    Write-Host ">>> Running: $Name" -ForegroundColor Yellow
    
    $reqArgs = @{
        Uri = $Uri
        Method = $Method
        UseBasicParsing = $true
    }
    if ($Body) { $reqArgs.Body = $Body; $reqArgs.ContentType = "application/json" }
    if ($Headers) { $reqArgs.Headers = $Headers }

    $status = 0
    $respContent = ""
    try {
        $response = Invoke-WebRequest @reqArgs -ErrorAction Stop
        $status = $response.StatusCode
        $respContent = $response.Content
    } catch {
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            $respStream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($respStream)
            $respContent = $reader.ReadToEnd()
        } else {
            $status = 0
            $respContent = $_.Exception.Message
        }
    }

    $passed = Log-Evidence $Name $Uri $Method $status $ExpectedStatus $Body $respContent

    if ($passed) {
        Write-Host "[PASS] Expected status $ExpectedStatus received.
" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] Expected status $ExpectedStatus, got $status
" -ForegroundColor Red
        Write-Host $respContent -ForegroundColor DarkGray
    }
}

# --- 1. CAPPO Hostile Tests ---
Run-Test "CAPPO: Unauthorized Cloud Action Proposed" "$CAPPO_URL/v2/governance/authorize" "POST" 401 (@{intent_hash="malicious"; sink_class="EXTERNAL_CLOUD"} | ConvertTo-Json)
Run-Test "CAPPO: Expired Authority Execution" "$CAPPO_URL/v2/governance/execute" "POST" 401 (@{operation_id="op_123"} | ConvertTo-Json) @{"Authorization"="Bearer invalid.expired.token"}
Run-Test "CAPPO: Proof Transplant Attempt" "$CAPPO_URL/v2/governance/record-truth" "POST" 401 (@{operation_id="op_456"; proof_subject_hash="transplanted"; asserted_truth_state="COMPLETED_SUCCESS"} | ConvertTo-Json) @{"Authorization"="Bearer invalid.token"}
Run-Test "CAPPO: Stale Handle Rejection" "$CAPPO_URL/v2/governance/record-truth" "POST" 401 (@{operation_id="op_789"; proof_subject_hash="valid"; asserted_truth_state="COMPLETED_SUCCESS"; is_stale=$true} | ConvertTo-Json) @{"Authorization"="Bearer invalid.token"}

# --- 2. BYOS Hostile Tests ---
Run-Test "BYOS: Public unauthenticated request to protected BYOS route" "$BYOS_URL/api/v1/workspace" "GET" 401 ""
Run-Test "BYOS: Authenticated but unauthorized tenant request" "$BYOS_URL/api/v1/workspace" "GET" 401 "" @{"Authorization"="Bearer fake_tenant_token"}
Run-Test "BYOS: Valid-looking request without CAPPO authority" "$BYOS_URL/api/v1/ai/execute" "POST" 401 (@{command="ls"} | ConvertTo-Json) @{"Authorization"="Bearer valid_user_token"}
Run-Test "BYOS: Expired CAPPO authority" "$BYOS_URL/api/v1/ai/execute" "POST" 401 (@{command="ls"} | ConvertTo-Json) @{"Authorization"="Bearer expired_cappo_authority"}
Run-Test "BYOS: Wrong tenant / wrong workspace authority" "$BYOS_URL/api/v1/ai/execute" "POST" 401 (@{command="ls"; workspace_id="ws_wrong"} | ConvertTo-Json) @{"Authorization"="Bearer valid_user_token"}

$Global:Summary | ConvertTo-Json -Depth 5 | Set-Content "$OUTPUT_DIR\p5_live_fire_summary.json"

$hashContent = (Get-FileHash "$OUTPUT_DIR\p5_live_fire_http_proof.jsonl" -Algorithm SHA256).Hash
@{
    evidence_bundle_hash = $hashContent
    timestamp = (Get-Date).ToString("o")
    all_passed = $Global:AllPassed
} | ConvertTo-Json | Set-Content "$OUTPUT_DIR\p5_live_fire_hashes.json"

if ($Global:AllPassed) {
    Write-Host "[VEKLOM] Undeniable Proof Pack Completed Successfully (Internal)!
" -ForegroundColor Green
} else {
    Write-Host "[VEKLOM] Proof Pack Failed. Some constraints were not met.
" -ForegroundColor Red
    exit 1
}
