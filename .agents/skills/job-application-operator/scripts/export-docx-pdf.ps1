[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
try {
    $input = [System.IO.Path]::GetFullPath($InputPath)
    $output = [System.IO.Path]::GetFullPath($OutputPath)
    if (-not (Test-Path -LiteralPath $input -PathType Leaf)) { throw 'Input DOCX not found.' }
    $outputDirectory = [System.IO.Path]::GetDirectoryName($output)
    [System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($input, $false, $true)
    $document.ExportAsFixedFormat($output, 17)
}
finally {
    if ($null -ne $document) { $document.Close($false) }
    if ($null -ne $word) { $word.Quit() }
    if ($null -ne $document) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) }
    if ($null -ne $word) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
