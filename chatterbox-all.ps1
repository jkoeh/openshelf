$ErrorActionPreference = "Stop"

# Build Chatterbox renditions for all local catalog books, auto-selecting narrators on GPU, and upload.
# Run from the repo root: powershell -ExecutionPolicy Bypass -File .\chatterbox-all.ps1

# openshelf is run from source (no console script installed), so point at pipeline/src.
$env:PYTHONPATH = "C:\Users\johnk\Documents\Projects\openshelf\pipeline\src"
$python = "C:\Users\johnk\Documents\Projects\openshelf\.venv\Scripts\python.exe"
$cli = @("-m", "openshelf.pipeline.cli", "books", "process")
$catalogCli = @("-m", "openshelf.pipeline.cli", "ops", "catalog")

$aliceEpub = "download/books/standard-ebooks/lewis-carroll/alices-adventures-in-wonderland.epub"

$epubs = @(
  "download/books/gutenberg/charlotte-perkins-gilman/the-yellow-wallpaper.epub"
  "download/books/gutenberg/edgar-allan-poe/the-cask-of-amontillado.epub"
  "download/books/gutenberg/franz-kafka/metamorphosis.epub"
  "download/books/gutenberg/jonathan-swift/a-modest-proposal.epub"
  "download/books/gutenberg/jules-verne/twenty-thousand-leagues.epub"
  "download/books/gutenberg/kafka-franz/metamorphosis.epub"
  "download/books/gutenberg/o-henry/the-gift-of-the-magi.epub"
  "download/books/standard-ebooks/charlotte-bronte/jane-eyre.epub"
  "download/books/standard-ebooks/emily-bronte/wuthering-heights.epub"
  "download/books/standard-ebooks/franz-kafka/the-castle.epub"
  "download/books/standard-ebooks/herman-melville/moby-dick.epub"
  "download/books/standard-ebooks/h-g-wells/the-war-of-the-worlds.epub"
  "download/books/standard-ebooks/jane-austen/pride-and-prejudice.epub"
  "download/books/standard-ebooks/lewis-carroll/alices-adventures-in-wonderland.epub"
  "download/books/standard-ebooks/mark-twain/the-adventures-of-huckleberry-finn.epub"
  "download/books/standard-ebooks/mark-twain/the-adventures-of-tom-sawyer.epub"
  "download/books/standard-ebooks/mary-shelley/frankenstein.epub"
  "download/books/standard-ebooks/nathaniel-hawthorne/the-scarlet-letter.epub"
  "download/books/standard-ebooks/oscar-wilde/the-picture-of-dorian-gray.epub"
  "download/books/standard-ebooks/robert-louis-stevenson/the-strange-case-of-dr-jekyll-and-mr-hyde.epub"
  "download/books/standard-ebooks/robert-louis-stevenson/treasure-island.epub"
)

$failed = @()

function Invoke-OpenShelfBook {
  param(
    [Parameter(Mandatory = $true)][string]$Epub,
    [Parameter(Mandatory = $true)][int]$Index,
    [Parameter(Mandatory = $true)][int]$Total,
    [switch]$NewVoiceDirection
  )

  Write-Host "`n===== [$Index/$Total] $Epub =====" -ForegroundColor Cyan
  if ($NewVoiceDirection) {
    Write-Host "Mode: forced new voice direction" -ForegroundColor Yellow
  } else {
    Write-Host "Mode: reuse cached voice direction when available; force audio regeneration" -ForegroundColor Yellow
  }

  $extraArgs = @("--force")
  if ($NewVoiceDirection) {
    $extraArgs += "--new-voice-direction"
  }

  & $python @cli `
    --epub $Epub `
    --engine chatterbox `
    --device cuda --performance-direction batched `
    --upload `
    @extraArgs

  if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED: $Epub (exit $LASTEXITCODE)" -ForegroundColor Red
    $script:failed += $Epub
    return
  }

  Write-Host "Refreshing uploaded catalog ..." -ForegroundColor DarkCyan
  & $python @catalogCli
  if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED catalog refresh after: $Epub (exit $LASTEXITCODE)" -ForegroundColor Red
    $script:failed += "$Epub (catalog refresh)"
  }
}

$orderedEpubs = @($aliceEpub) + @($epubs | Where-Object { $_ -ne $aliceEpub })
$i = 0
foreach ($epub in $orderedEpubs) {
  $i++
  Invoke-OpenShelfBook `
    -Epub $epub `
    -Index $i `
    -Total $orderedEpubs.Count `
    -NewVoiceDirection:($epub -ne $aliceEpub)
}

Write-Host "`n===== Done. $($orderedEpubs.Count - $failed.Count)/$($orderedEpubs.Count) succeeded =====" -ForegroundColor Green
if ($failed.Count -gt 0) {
  Write-Host "Failed books:" -ForegroundColor Red
  $failed | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
  exit 1
}
