// Azure Container Apps deployment for 15-Min Market Maker
// Includes: Market Maker Bot + API Server + Static Web App for Dashboard

@description('Location for all resources')
param location string = resourceGroup().location

@description('Container Registry name')
param acrName string = 'polymktacr${uniqueString(resourceGroup().id)}'

@description('Container Apps Environment name')
param envName string = 'polymarket-env'

@description('Market Maker image tag')
param imageTag string = 'latest'

// Log Analytics Workspace
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'polymarket-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// Container Registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

// Container Apps Environment
resource containerEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: envName
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

// Shared Storage for SQLite database
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'polymktstorage${uniqueString(resourceGroup().id)}'
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  name: '${storageAccount.name}/default'
  properties: {}
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileService
  name: 'polymarket-data'
  properties: {
    shareQuota: 1
  }
}

// Market Maker Bot Container App
resource marketMakerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'market-maker-bot'
  location: location
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'market-maker'
          image: '${acr.properties.loginServer}/market-maker:${imageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          volumeMounts: [
            {
              volumeName: 'data-volume'
              mountPath: '/app/data'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'data-volume'
          storageType: 'AzureFile'
          storageName: fileShare.name
          storageAccountName: storageAccount.name
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// API Server Container App
resource apiApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'market-maker-api'
  location: location
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${acr.properties.loginServer}/market-maker-api:${imageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          volumeMounts: [
            {
              volumeName: 'data-volume'
              mountPath: '/app/data'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'data-volume'
          storageType: 'AzureFile'
          storageName: fileShare.name
          storageAccountName: storageAccount.name
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// Static Web App for Dashboard
resource staticWebApp 'Microsoft.Web/staticSites@2022-09-01' = {
  name: 'polymarket-dashboard'
  location: 'eastus2'  // Static Web Apps have limited locations
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {}
}

// Outputs
output acrLoginServer string = acr.properties.loginServer
output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
output dashboardUrl string = staticWebApp.properties.defaultHostname
