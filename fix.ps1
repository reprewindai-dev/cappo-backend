$f = "C:\Users\antho\.veklom\runtime\veklom-local.ps1"
$c = Get-Content $f
$c = $c -replace 'ConvertFrom-Json -AsHashtable', 'ConvertFrom-Json'
$c = $c -replace '\$pids\[\$Name\] = \$PidValue', 'Add-Member -InputObject $pids -MemberType NoteProperty -Name $Name -Value $PidValue -Force'
$c = $c -replace '\$pids = @\{\}', '$pids = New-Object PSObject'
$c = $c -replace 'foreach \(\$k in \$pids.Keys\)', 'foreach ($k in ($pids | Get-Member -MemberType NoteProperty).Name)'
$c = $c -replace '\$pidVal = \$pids\[\$k\]', '$pidVal = $pids.$k'
Set-Content $f -Value $c
