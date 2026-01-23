# Complete Deployment Script for Polymarket Bot
# Deploys: Market Maker Bot + API Server + Dashboard
# Usage: ./deploy-full.ps1 -ResourceGroup "polymarket-rg" -Location "eastus"

param(
    [Parameter(Mandatory=$true)]
    [string]$ResourceGroup,
    
    [Parameter(Mandatory=$false)]
    [string]$Location = "eastus",
    
    [Parameter(Mandatory=$false)]
    [string]$ImageTag = "latest"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  🚀 Complete Polymarket Bot Deployment" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Resource Group: $ResourceGroup"
Write-Host "  Location: $Location"
Write-Host "  Image Tag: $ImageTag"
Write-Host ""

# Check prerequisites
Write-Host "[1/7] Checking prerequisites..." -ForegroundColor Yellow

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI not found. Install: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
    exit 1
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker not found. Install: https://www.docker.com/get-started"
    exit 1
}

# Check Azure login
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "Please login to Azure..." -ForegroundColor Yellow
    az login
    $account = az account show | ConvertFrom-Json
}

Write-Host "  ✓ Logged in as: $($account.user.name)" -ForegroundColor Green
Write-Host "  ✓ Subscription: $($account.name)" -ForegroundColor Green

# Create Resource Group
Write-Host ""
Write-Host "[2/7] Ensuring resource group exists..." -ForegroundColor Yellow
$rg = az group show --name $ResourceGroup 2>$null | ConvertFrom-Json
if (-not $rg) {
    Write-Host "  Creating resource group..." -ForegroundColor Cyan
    az group create --name $ResourceGroup --location $Location | Out-Null
}
Write-Host "  ✓ Resource group ready" -ForegroundColor Green

# Deploy infrastructure
Write-Host ""
Write-Host "[3/7] Deploying Azure infrastructure..." -ForegroundColor Yellow
$deployment = az deployment group create `
    --resource-group $ResourceGroup `
    --template-file "$PSScriptRoot/market-maker.bicep" `
    --parameters imageTag=$ImageTag `
    --query properties.outputs `
    | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) {
    Write-Error "Infrastructure deployment failed"
    exit 1
}

$acrLoginServer = $deployment.acrLoginServer.value
$apiUrl = $deployment.apiUrl.value

Write-Host "  ✓ Container Registry: $acrLoginServer" -ForegroundColor Green
Write-Host "  ✓ API URL: $apiUrl" -ForegroundColor Green

# Extract ACR name (remove .azurecr.io)
$acrName = ($acrLoginServer -split '\.')[0]

# Login to ACR
Write-Host ""
Write-Host "[4/7] Building and pushing Docker images..." -ForegroundColor Yellow
Write-Host "  Logging into Container Registry..." -ForegroundColor Cyan
az acr login --name $acrName | Out-Null

# Build Market Maker image
Write-Host "  Building market-maker image..." -ForegroundColor Cyan
$rootPath = Split-Path -Parent $PSScriptRoot
docker build -t "$acrLoginServer/market-maker:$ImageTag" -f "$PSScriptRoot/Dockerfile.marketmaker" $rootPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Market maker Docker build failed"
    exit 1
}
docker push "$acrLoginServer/market-maker:$ImageTag"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Market maker Docker push failed"
    exit 1
}
Write-Host "  ✓ Market maker image pushed" -ForegroundColor Green

# Build API image
Write-Host "  Building API image..." -ForegroundColor Cyan
docker build -t "$acrLoginServer/market-maker-api:$ImageTag" -f "$PSScriptRoot/Dockerfile.api" $rootPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "API Docker build failed"
    exit 1
}
docker push "$acrLoginServer/market-maker-api:$ImageTag"
if ($LASTEXITCODE -ne 0) {
    Write-Error "API Docker push failed"
    exit 1
}
Write-Host "  ✓ API image pushed" -ForegroundColor Green

# Build dashboard
Write-Host ""
Write-Host "[5/7] Building dashboard..." -ForegroundColor Yellow
$dashboardPath = Join-Path $rootPath "dashboard"
if (-not (Test-Path $dashboardPath)) {
    Write-Error "Dashboard directory not found: $dashboardPath"
    exit 1
}

Push-Location $dashboardPath
try {
    # Set API URL for build
    $env:VITE_API_URL = $apiUrl
    Write-Host "  API URL: $apiUrl" -ForegroundColor Cyan
    
    # Install dependencies
    if (-not (Test-Path "node_modules")) {
        Write-Host "  Installing npm dependencies..." -ForegroundColor Cyan
        npm install --silent 2>$null
    }
    
    # Build
    Write-Host "  Building React app..." -ForegroundColor Cyan
    npm run build
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Dashboard build failed"
        exit 1
    }
    
    if (-not (Test-Path "build")) {
        Write-Error "Build directory not found after build"
        exit 1
    }
    
    Write-Host "  ✓ Dashboard built successfully" -ForegroundColor Green
}
finally {
    Pop-Location
}

# Deploy dashboard to Blob Storage
Write-Host ""
Write-Host "[6/7] Deploying dashboard to Azure Blob Storage..." -ForegroundColor Yellow

# Generate storage account name
$storageSuffix = -join ((97..122) | Get-Random -Count 6 | ForEach-Object {[char]$_})
$storageAccountName = "polymktdash$storageSuffix"
$storageAccountName = $storageAccountName.ToLower() -replace '[^a-z0-9]', ''
if ($storageAccountName.Length -gt 24) {
    $storageAccountName = $storageAccountName.Substring(0, 24)
}

Write-Host "  Storage account: $storageAccountName" -ForegroundColor Cyan

# Create storage account
$storage = az storage account show --name $storageAccountName --resource-group $ResourceGroup 2>$null | ConvertFrom-Json
if (-not $storage) {
    Write-Host "  Creating storage account..." -ForegroundColor Cyan
    az storage account create `
        --name $storageAccountName `
        --resource-group $ResourceGroup `
        --location $Location `
        --sku Standard_LRS `
        --kind StorageV2 `
        --allow-blob-public-access true | Out-Null
}

# Enable static website
Write-Host "  Enabling static website hosting..." -ForegroundColor Cyan
az storage blob service-properties update `
    --account-name $storageAccountName `
    --static-website `
    --index-document index.html `
    --404-document index.html | Out-Null

# Get storage key
$storageKey = (az storage account keys list --account-name $storageAccountName --resource-group $ResourceGroup --query "[0].value" -o tsv)

# Upload dashboard files
Write-Host "  Uploading dashboard files..." -ForegroundColor Cyan
az storage blob upload-batch `
    --account-name $storageAccountName `
    --account-key $storageKey `
    --destination '$web' `
    --source "$dashboardPath/build" `
    --overwrite | Out-Null

# Get website URL
$dashboardUrl = (az storage account show --name $storageAccountName --resource-group $ResourceGroup --query "primaryEndpoints.web" -o tsv)

Write-Host "  ✓ Dashboard deployed" -ForegroundColor Green

# Wait for containers to start
Write-Host ""
Write-Host "[7/7] Waiting for containers to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# Check API status
Write-Host "  Checking API health..." -ForegroundColor Cyan
try {
    $healthCheck = Invoke-WebRequest -Uri "$apiUrl/" -TimeoutSec 10 -UseBasicParsing -ErrorAction SilentlyContinue
    if ($healthCheck.StatusCode -eq 200) {
        Write-Host "  ✓ API is responding" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ API returned status $($healthCheck.StatusCode)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  ⚠ API not responding yet (may take a few minutes)" -ForegroundColor Yellow
}

# Summary
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ✅ Deployment Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  📊 Dashboard URL:" -ForegroundColor Cyan
Write-Host "     $dashboardUrl" -ForegroundColor White
Write-Host ""
Write-Host "  🔌 API URL:" -ForegroundColor Cyan
Write-Host "     $apiUrl" -ForegroundColor White
Write-Host ""
Write-Host "  📋 Resource Details:" -ForegroundColor Yellow
Write-Host "     Resource Group: $ResourceGroup"
Write-Host "     Storage Account: $storageAccountName"
Write-Host "     Container Registry: $acrName"
Write-Host ""
Write-Host "  📝 Useful Commands:" -ForegroundColor Yellow
Write-Host "     View API logs: az containerapp logs show -n market-maker-api -g $ResourceGroup --follow"
Write-Host "     View Bot logs: az containerapp logs show -n market-maker-bot -g $ResourceGroup --follow"
Write-Host "     Stop bot: az containerapp update -n market-maker-bot -g $ResourceGroup --scale-rule-name manual --min-replicas 0"
Write-Host "     Start bot: az containerapp update -n market-maker-bot -g $ResourceGroup --scale-rule-name manual --min-replicas 1"
Write-Host ""
Write-Host "  💡 Note: The SQLite database is stored in Azure File Storage and persists across restarts."
Write-Host ""
