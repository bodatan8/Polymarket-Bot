// Azure Container Apps Bicep template for Polymarket Arbitrage Bot
// Deploys: Container Apps Environment, Container App, Key Vault, Log Analytics

@description('Location for all resources')
param location string = 'eastus'

@description('Name prefix for resources')
param namePrefix string = 'polymarket-arb'

@description('Container image to deploy')
param containerImage string

@description('Polymarket API Key')
@secure()
param polymarketApiKey string

@description('Polymarket API Secret')
@secure()
param polymarketApiSecret string

@description('Polymarket API Passphrase')
@secure()
param polymarketApiPassphrase string

@description('Wallet Private Key')
@secure()
param privateKey string

@description('Wallet Address')
param walletAddress string

@description('Polygon RPC URL')
@secure()
param polygonRpcUrl string

@description('Minimum edge in basis points')
param minEdgeBps int = 50

@description('Maximum position size in USDC')
param maxPositionSize int = 100

// Variables
var logAnalyticsName = '${namePrefix}-logs'
var keyVaultName = '${namePrefix}-kv'
var containerAppEnvName = '${namePrefix}-env'
var containerAppName = '${namePrefix}-bot'

// Log Analytics Workspace
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// Key Vault for secrets
resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// Secrets in Key Vault
resource secretApiKey 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'polymarket-api-key'
  properties: {
    value: polymarketApiKey
  }
}

resource secretApiSecret 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'polymarket-api-secret'
  properties: {
    value: polymarketApiSecret
  }
}

resource secretApiPassphrase 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'polymarket-api-passphrase'
  properties: {
    value: polymarketApiPassphrase
  }
}

resource secretPrivateKey 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'wallet-private-key'
  properties: {
    value: privateKey
  }
}

resource secretPolygonRpc 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'polygon-rpc-url'
  properties: {
    value: polygonRpcUrl
  }
}

// Container Apps Environment
resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: containerAppEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// Container App
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: [
        {
          name: 'polymarket-api-key'
          value: polymarketApiKey
        }
        {
          name: 'polymarket-api-secret'
          value: polymarketApiSecret
        }
        {
          name: 'polymarket-api-passphrase'
          value: polymarketApiPassphrase
        }
        {
          name: 'private-key'
          value: privateKey
        }
        {
          name: 'polygon-rpc-url'
          value: polygonRpcUrl
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'polymarket-bot'
          image: containerImage
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            {
              name: 'POLYMARKET_API_KEY'
              secretRef: 'polymarket-api-key'
            }
            {
              name: 'POLYMARKET_API_SECRET'
              secretRef: 'polymarket-api-secret'
            }
            {
              name: 'POLYMARKET_API_PASSPHRASE'
              secretRef: 'polymarket-api-passphrase'
            }
            {
              name: 'PRIVATE_KEY'
              secretRef: 'private-key'
            }
            {
              name: 'POLYGON_RPC_URL'
              secretRef: 'polygon-rpc-url'
            }
            {
              name: 'WALLET_ADDRESS'
              value: walletAddress
            }
            {
              name: 'MIN_EDGE_BPS'
              value: string(minEdgeBps)
            }
            {
              name: 'MAX_POSITION_SIZE'
              value: string(maxPositionSize)
            }
            {
              name: 'LOG_LEVEL'
              value: 'INFO'
            }
            {
              name: 'JSON_LOGGING'
              value: 'true'
            }
            {
              name: 'KILL_SWITCH'
              value: 'false'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// Role assignment for Key Vault access
resource keyVaultRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, containerApp.id, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Outputs
output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn ?? 'N/A (no ingress)'
output keyVaultName string = keyVault.name
output logAnalyticsWorkspaceId string = logAnalytics.id
