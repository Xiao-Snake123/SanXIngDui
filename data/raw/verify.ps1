Get-ChildItem -Recurse -Filter *.pdf | ForEach-Object {
  $b = [System.IO.File]::ReadAllBytes($_.FullName)
  $magic = -join [char[]]$b[0..4]
  Write-Host ($_.FullName + " -> magic=[" + $magic + "] size=" + $b.Length)
}
