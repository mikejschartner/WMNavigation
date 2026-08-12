# Publish WMNavigation update channel (run after installing GitHub CLI)
#
# 1) Install: https://cli.github.com/
# 2) Auth:   gh auth login
# 3) From this folder in PowerShell:
#      .\publish_release.ps1

$ErrorActionPreference = "Stop"
$git = @(
  "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\TeamFoundation\Team Explorer\Git\cmd\git.exe",
  "C:\Program Files\Git\cmd\git.exe",
  "git"
) | Where-Object { $_ -eq "git" -or (Test-Path $_) } | Select-Object -First 1

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  throw "gh not found. Install GitHub CLI and run: gh auth login"
}

Set-Location $PSScriptRoot
$version = (Select-String -Path "src\wmnavi\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches.Groups[1].Value
$tag = "v$version"
$exe = "dist\WMNavigation.exe"
if (-not (Test-Path $exe)) { throw "Missing $exe — run: pyinstaller build.spec --noconfirm" }

$latestPath = "dist\latest.json"
@{
  version = $version
  versionName = $version
  downloadUrl = "https://github.com/mikejschartner/WMNavigation/releases/download/$tag/WMNavigation.exe"
  releaseNotes = "WMNavigation $tag"
} | ConvertTo-Json | Set-Content $latestPath -Encoding UTF8

gh auth status
$repoCheck = gh repo view mikejschartner/WMNavigation 2>&1
if ($LASTEXITCODE -ne 0) {
  gh repo create mikejschartner/WMNavigation --public --source=. --remote=origin --push
} else {
  & $git push -u origin main
  & $git push origin $tag 2>$null
}

gh release create $tag $exe $latestPath --title $tag --notes "WMNavigation $tag — auto-update channel" --latest
Write-Host "Published: https://github.com/mikejschartner/WMNavigation/releases/tag/$tag"
Write-Host "latest.json: https://github.com/mikejschartner/WMNavigation/releases/latest/download/latest.json"
