# Deploy 15-Min Market Maker to Azure
# Usage: ./deploy-market-maker.ps1 -ResourceGroup "polygon" -Location "eastus"

param(
    [Parameter(Mandatory=$true)]
    [string]$ResourceGroup,
    
    [Parameter(Mandatory=$false)]
    [string]$Location = "eastus",
    
    [Parameter(Mandatory=$false)]
    [string]$ImageTag = "latest"
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  🚀 Deploying 15-Min Market Maker to Azure" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Resource Group: $ResourceGroup"
Write-Host "  Location: $Location"
Write-Host "  Image Tag: $ImageTag"
Write-Host ""

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
}

Write-Host "Logged in as: $($account.user.name)" -ForegroundColor Green

# Create Resource Group if it doesn't exist
$rg = az group show --name $ResourceGroup 2>$null | ConvertFrom-Json
if (-not $rg) {
    Write-Host "Creating resource group: $ResourceGroup..." -ForegroundColor Yellow
    az group create --name $ResourceGroup --location $Location
}

# Deploy infrastructure
Write-Host ""
Write-Host "Deploying infrastructure..." -ForegroundColor Cyan
$deployment = az deployment group create `
    --resource-group $ResourceGroup `
    --template-file "$PSScriptRoot/market-maker.bicep" `
    --parameters imageTag=$ImageTag `
    --query properties.outputs `
    | ConvertFrom-Json

$acrLoginServer = $deployment.acrLoginServer.value
$apiUrl = $deployment.apiUrl.value

Write-Host "ACR: $acrLoginServer" -ForegroundColor Green
Write-Host "API URL: $apiUrl" -ForegroundColor Green

# Login to ACR
Write-Host ""
Write-Host "Logging into Container Registry..." -ForegroundColor Cyan
az acr login --name ($acrLoginServer -split '\.')[0]

# Build and push images
Write-Host ""
Write-Host "Building and pushing Market Maker image..." -ForegroundColor Cyan
docker build -t "$acrLoginServer/market-maker:$ImageTag" -f "$PSScriptRoot/Dockerfile.marketmaker" ..
docker push "$acrLoginServer/market-maker:$ImageTag"

Write-Host ""
Write-Host "Building and pushing API image..." -ForegroundColor Cyan
docker build -t "$acrLoginServer/market-maker-api:$ImageTag" -f "$PSScriptRoot/Dockerfile.api" ..
docker push "$acrLoginServer/market-maker-api:$ImageTag"

# Update environment variable for dashboard
Write-Host ""
Write-Host "Building dashboard..." -ForegroundColor Cyan
Set-Location "$PSScriptRoot/../dashboard"
$env:VITE_API_URL = $apiUrl
npm run build

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ✅ Deployment Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  API URL: $apiUrl" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "  1. Deploy dashboard/build to Azure Static Web Apps"
Write-Host "  2. Or host on any static file server"
Write-Host ""
