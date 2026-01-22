<#
.SYNOPSIS
    Deploys the Polymarket Arbitrage Bot to Azure Container Apps.

.DESCRIPTION
    This script:
    1. Builds the Docker image
    2. Pushes to Azure Container Registry
    3. Deploys or updates the Container App using Bicep

.PARAMETER ResourceGroupName
    Name of the Azure resource group

.PARAMETER Location
    Azure region (default: eastus)

.PARAMETER AcrName
    Name of the Azure Container Registry

.PARAMETER NamePrefix
    Prefix for resource names (default: polymarket-arb)

.PARAMETER EnvFile
    Path to .env file with secrets

.EXAMPLE
    .\deploy.ps1 -ResourceGroupName "rg-polymarket" -AcrName "acrpolymarket" -EnvFile "../.env"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [Parameter(Mandatory = $false)]
    [string]$Location = "eastus",

    [Parameter(Mandatory = $true)]
    [string]$AcrName,

    [Parameter(Mandatory = $false)]
    [string]$NamePrefix = "polymarket-arb",

    [Parameter(Mandatory = $true)]
    [string]$EnvFile
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Polymarket Arbitrage Bot Deployment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check prerequisites
Write-Host "`n[1/6] Checking prerequisites..." -ForegroundColor Yellow

# Check Azure CLI
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI (az) is required but not installed."
}

# Check Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required but not installed."
}

# Check logged in to Azure
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    throw "Not logged in to Azure. Run 'az login' first."
}
Write-Host "  Logged in as: $($account.user.name)" -ForegroundColor Green

# Load environment variables
Write-Host "`n[2/6] Loading configuration from $EnvFile..." -ForegroundColor Yellow

if (-not (Test-Path $EnvFile)) {
    throw "Environment file not found: $EnvFile"
}

$envVars = @{}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        $envVars[$matches[1].Trim()] = $matches[2].Trim()
    }
}

# Validate required variables
$requiredVars = @(
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET", 
    "POLYMARKET_API_PASSPHRASE",
    "PRIVATE_KEY",
    "WALLET_ADDRESS",
    "POLYGON_RPC_URL"
)

foreach ($var in $requiredVars) {
    if (-not $envVars.ContainsKey($var) -or [string]::IsNullOrEmpty($envVars[$var])) {
        throw "Required variable $var is not set in $EnvFile"
    }
}
Write-Host "  Configuration loaded successfully" -ForegroundColor Green

# Create resource group if needed
Write-Host "`n[3/6] Ensuring resource group exists..." -ForegroundColor Yellow

$rgExists = az group exists --name $ResourceGroupName | ConvertFrom-Json
if (-not $rgExists) {
    Write-Host "  Creating resource group: $ResourceGroupName" -ForegroundColor Cyan
    az group create --name $ResourceGroupName --location $Location | Out-Null
}
Write-Host "  Resource group: $ResourceGroupName" -ForegroundColor Green

# Create ACR if needed
Write-Host "`n[4/6] Ensuring Container Registry exists..." -ForegroundColor Yellow

$acrExists = az acr show --name $AcrName --resource-group $ResourceGroupName 2>$null
if (-not $acrExists) {
    Write-Host "  Creating Azure Container Registry: $AcrName" -ForegroundColor Cyan
    az acr create `
        --resource-group $ResourceGroupName `
        --name $AcrName `
        --sku Basic `
        --admin-enabled true | Out-Null
}
Write-Host "  Container Registry: $AcrName" -ForegroundColor Green

# Build and push Docker image
Write-Host "`n[5/6] Building and pushing Docker image..." -ForegroundColor Yellow

$imageName = "$AcrName.azurecr.io/polymarket-bot"
$imageTag = "$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$fullImageName = "${imageName}:${imageTag}"

# Login to ACR
Write-Host "  Logging in to ACR..." -ForegroundColor Cyan
az acr login --name $AcrName | Out-Null

# Build image
Write-Host "  Building Docker image..." -ForegroundColor Cyan
$dockerContext = Split-Path -Parent $PSScriptRoot
docker build -t $fullImageName -f "$PSScriptRoot/Dockerfile" $dockerContext

if ($LASTEXITCODE -ne 0) {
    throw "Docker build failed"
}

# Push image
Write-Host "  Pushing image to ACR..." -ForegroundColor Cyan
docker push $fullImageName

if ($LASTEXITCODE -ne 0) {
    throw "Docker push failed"
}

# Also tag as latest
docker tag $fullImageName "${imageName}:latest"
docker push "${imageName}:latest"

Write-Host "  Image pushed: $fullImageName" -ForegroundColor Green

# Deploy with Bicep
Write-Host "`n[6/6] Deploying to Azure Container Apps..." -ForegroundColor Yellow

$deploymentName = "polymarket-bot-$(Get-Date -Format 'yyyyMMddHHmmss')"

$deploymentParams = @{
    containerImage = $fullImageName
    location = $Location
    namePrefix = $NamePrefix
    polymarketApiKey = $envVars["POLYMARKET_API_KEY"]
    polymarketApiSecret = $envVars["POLYMARKET_API_SECRET"]
    polymarketApiPassphrase = $envVars["POLYMARKET_API_PASSPHRASE"]
    privateKey = $envVars["PRIVATE_KEY"]
    walletAddress = $envVars["WALLET_ADDRESS"]
    polygonRpcUrl = $envVars["POLYGON_RPC_URL"]
    minEdgeBps = [int]($envVars["MIN_EDGE_BPS"] ?? "50")
    maxPositionSize = [int]($envVars["MAX_POSITION_SIZE"] ?? "100")
}

# Convert to JSON for Bicep parameters
$paramsJson = $deploymentParams | ConvertTo-Json -Compress

# Create parameters file
$paramsFile = "$PSScriptRoot/params-temp.json"
@{
    "`$schema" = "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#"
    "contentVersion" = "1.0.0.0"
    "parameters" = @{
        "containerImage" = @{ "value" = $fullImageName }
        "location" = @{ "value" = $Location }
        "namePrefix" = @{ "value" = $NamePrefix }
        "polymarketApiKey" = @{ "value" = $envVars["POLYMARKET_API_KEY"] }
        "polymarketApiSecret" = @{ "value" = $envVars["POLYMARKET_API_SECRET"] }
        "polymarketApiPassphrase" = @{ "value" = $envVars["POLYMARKET_API_PASSPHRASE"] }
        "privateKey" = @{ "value" = $envVars["PRIVATE_KEY"] }
        "walletAddress" = @{ "value" = $envVars["WALLET_ADDRESS"] }
        "polygonRpcUrl" = @{ "value" = $envVars["POLYGON_RPC_URL"] }
        "minEdgeBps" = @{ "value" = [int]($envVars["MIN_EDGE_BPS"] ?? "50") }
        "maxPositionSize" = @{ "value" = [int]($envVars["MAX_POSITION_SIZE"] ?? "100") }
    }
} | ConvertTo-Json -Depth 10 | Set-Content $paramsFile

try {
    $result = az deployment group create `
        --resource-group $ResourceGroupName `
        --name $deploymentName `
        --template-file "$PSScriptRoot/container-app.bicep" `
        --parameters "@$paramsFile" `
        --output json | ConvertFrom-Json

    if ($LASTEXITCODE -ne 0) {
        throw "Deployment failed"
    }

    Write-Host "`n========================================" -ForegroundColor Green
    Write-Host "Deployment Successful!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Container App: $($result.properties.outputs.containerAppName.value)" -ForegroundColor Cyan
    Write-Host "Key Vault: $($result.properties.outputs.keyVaultName.value)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "To view logs:" -ForegroundColor Yellow
    Write-Host "  az containerapp logs show -n $($result.properties.outputs.containerAppName.value) -g $ResourceGroupName --follow" -ForegroundColor White
    Write-Host ""
    Write-Host "To stop the bot:" -ForegroundColor Yellow
    Write-Host "  az containerapp update -n $($result.properties.outputs.containerAppName.value) -g $ResourceGroupName --scale-rule-name manual --min-replicas 0 --max-replicas 0" -ForegroundColor White
}
finally {
    # Clean up temp file
    if (Test-Path $paramsFile) {
        Remove-Item $paramsFile -Force
    }
}
