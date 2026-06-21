param(
    [Parameter(Mandatory=$true)]
    [string]$FilePath,
    [string]$Arguments,
    [string]$WorkingDirectory
)

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $FilePath
$psi.Arguments = $Arguments
if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

$p = [System.Diagnostics.Process]::Start($psi)
Write-Output $p.Id