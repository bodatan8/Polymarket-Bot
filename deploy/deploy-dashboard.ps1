# Deploy Dashboard to Azure Blob Storage (Static Website)
# Usage: ./deploy-dashboard.ps1 -ResourceGroup "polygon" -ApiUrl "https://your-api.azurecontainerapps.io"

param(
    [Parameter(Mandatory=$true)]
    [string]$ResourceGroup,
    
    [Parameter(Mandatory=$false)]
    [string]$Location = "eastus",
    
    [Parameter(Mandatory=$false)]
    [string]$StorageAccountName = "",
    
    [Parameter(Mandatory=$false)]
    [string]$ApiUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  🌐 Deploying Dashboard to Azure Blob Storage" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Check Azure CLI
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI not found. Please install: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
    exit 1
}

# Login check
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "Please login to Azure..." -ForegroundColor Yellow
    az login
    $account = az account show | ConvertFrom-Json
}

Write-Host "Logged in as: $($account.user.name)" -ForegroundColor Green
Write-Host "Subscription: $($account.name)" -ForegroundColor Green

# Create Resource Group if it doesn't exist
$rg = az group show --name $ResourceGroup 2>$null | ConvertFrom-Json
if (-not $rg) {
    Write-Host "Creating resource group: $ResourceGroup..." -ForegroundColor Yellow
    az group create --name $ResourceGroup --location $Location | Out-Null
}

# Generate storage account name if not provided
if (-not $StorageAccountName) {
    $suffix = -join ((97..122) | Get-Random -Count 6 | ForEach-Object {[char]$_})
    $StorageAccountName = "polymktdash$suffix"
}

# Ensure name is valid (lowercase, no special chars, 3-24 chars)
$StorageAccountName = $StorageAccountName.ToLower() -replace '[^a-z0-9]', ''
if ($StorageAccountName.Length -gt 24) {
    $StorageAccountName = $StorageAccountName.Substring(0, 24)
}

Write-Host ""
Write-Host "Storage Account: $StorageAccountName" -ForegroundColor Cyan

# Create Storage Account
Write-Host "Creating storage account..." -ForegroundColor Yellow
$storage = az storage account show --name $StorageAccountName --resource-group $ResourceGroup 2>$null | ConvertFrom-Json
if (-not $storage) {
    az storage account create `
        --name $StorageAccountName `
        --resource-group $ResourceGroup `
        --location $Location `
        --sku Standard_LRS `
        --kind StorageV2 `
        --allow-blob-public-access true | Out-Null
}

# Enable static website hosting
Write-Host "Enabling static website hosting..." -ForegroundColor Yellow
az storage blob service-properties update `
    --account-name $StorageAccountName `
    --static-website `
    --index-document index.html `
    --404-document index.html | Out-Null

# Get storage account key
$storageKey = (az storage account keys list --account-name $StorageAccountName --resource-group $ResourceGroup --query "[0].value" -o tsv)

# Build dashboard with API URL
Write-Host ""
Write-Host "Building dashboard..." -ForegroundColor Cyan
$dashboardPath = Join-Path $PSScriptRoot ".." "dashboard"
Set-Location $dashboardPath

# Set environment variable for build
$env:VITE_API_URL = $ApiUrl
Write-Host "  API URL: $ApiUrl"

# Install dependencies and build
npm install --silent 2>$null
npm run build

# Upload to Azure Blob Storage
Write-Host ""
Write-Host "Uploading to Azure..." -ForegroundColor Cyan
az storage blob upload-batch `
    --account-name $StorageAccountName `
    --account-key $storageKey `
    --destination '$web' `
    --source "./build" `
    --overwrite | Out-Null

# Get the website URL
$websiteUrl = (az storage account show --name $StorageAccountName --resource-group $ResourceGroup --query "primaryEndpoints.web" -o tsv)

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ✅ Dashboard Deployed Successfully!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  🌐 Dashboard URL: $websiteUrl" -ForegroundColor Cyan
Write-Host ""
Write-Host "  📋 Details:" -ForegroundColor Yellow
Write-Host "     Storage Account: $StorageAccountName"
Write-Host "     Resource Group: $ResourceGroup"
Write-Host "     API URL: $ApiUrl"
Write-Host ""
