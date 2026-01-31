# Deploy Dashboard to Azure Static Web Apps
# Prerequisites: Azure CLI logged in, Node.js installed

param(
    [string]$ResourceGroup = "polymarket-bot-rg",
    [string]$Location = "eastus2",
    [string]$AppName = "polymarket-signals-dashboard",
    [string]$ApiUrl = ""  # URL of your API server
)

$ErrorActionPreference = "Stop"

Write-Host "=== Deploying Polymarket Signals Dashboard to Azure ===" -ForegroundColor Cyan

# Check if logged in
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "Not logged in to Azure. Running 'az login'..." -ForegroundColor Yellow
    az login
}

Write-Host "Using subscription: $($account.name)" -ForegroundColor Green

# Create resource group if not exists
Write-Host "Creating resource group '$ResourceGroup'..." -ForegroundColor Yellow
az group create --name $ResourceGroup --location $Location --output none 2>$null

# Build the dashboard
Write-Host "Building dashboard..." -ForegroundColor Yellow
Push-Location "$PSScriptRoot/../dashboard"

# Set API URL environment variable for build
if ($ApiUrl) {
    $env:VITE_API_URL = $ApiUrl
}

npm install
npm run build

# Check if Static Web App exists
$swaExists = az staticwebapp show --name $AppName --resource-group $ResourceGroup 2>$null
if (-not $swaExists) {
    Write-Host "Creating Static Web App '$AppName'..." -ForegroundColor Yellow
    az staticwebapp create `
        --name $AppName `
        --resource-group $ResourceGroup `
        --location $Location `
        --sku Free `
        --output none
}

# Get deployment token
$deploymentToken = az staticwebapp secrets list --name $AppName --resource-group $ResourceGroup --query "properties.apiKey" -o tsv

# Deploy using SWA CLI
Write-Host "Deploying to Azure Static Web Apps..." -ForegroundColor Yellow
npx @azure/static-web-apps-cli deploy ./build `
    --deployment-token $deploymentToken `
    --env production

# Get the URL
$url = az staticwebapp show --name $AppName --resource-group $ResourceGroup --query "defaultHostname" -o tsv

Pop-Location

Write-Host ""
Write-Host "=== Deployment Complete ===" -ForegroundColor Green
Write-Host "Dashboard URL: https://$url" -ForegroundColor Cyan
Write-Host ""
Write-Host "To update API URL, rebuild with: VITE_API_URL=<your-api-url> npm run build" -ForegroundColor Yellow
