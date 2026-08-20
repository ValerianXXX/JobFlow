function Enter-JobFlowFileLock {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$TimeoutCode,
        [int]$TimeoutSeconds = 30
    )

    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Path))
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw $TimeoutCode
    }
    $stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::ReadWrite
    )
    try {
        if ($stream.Length -lt 1) {
            $stream.SetLength(1)
            $stream.Flush()
        }
        $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1, $TimeoutSeconds))
        while ($true) {
            try {
                $stream.Lock(0, 1)
                return $stream
            }
            catch [IO.IOException] {
                if ([DateTime]::UtcNow -ge $deadline) {
                    throw $TimeoutCode
                }
                Start-Sleep -Milliseconds 50
            }
        }
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Exit-JobFlowFileLock {
    param([object]$Stream)
    if ($null -eq $Stream) { return }
    try { $Stream.Unlock(0, 1) } catch { }
    $Stream.Dispose()
}
