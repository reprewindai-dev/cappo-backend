$ErrorActionPreference = 'Stop'
$file = "C:\Users\antho\.veklom\runtime\veklom-local.ps1"
$content = Get-Content $file

$content = $content -replace "postgresql\+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test", "postgresql+asyncpg://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test"

Set-Content $file -Value $content
