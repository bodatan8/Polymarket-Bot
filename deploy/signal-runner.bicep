// Azure Container App for Signal Runner
// Runs every 10 seconds to check for trading signals

@description('Location for all resources')
param location string = resourceGroup().location

@description('Container App Environment ID')
param containerAppEnvId string

@description('Container Registry Login Server')
param containerRegistryServer string

@description('Container Registry Username')
param containerRegistryUsername string

@secure()
@description('Container Registry Password')
param containerRegistryPassword string

@description('Image name and tag')
param imageName string = 'signal-runner:latest'

@secure()
@description('Supabase URL')
param supabaseUrl string

@secure()
@description('Supabase Service Role Key')
param supabaseServiceKey string

@secure()
@description('Polymarket Bot URL (optional)')
param polymarketBotUrl string = ''

resource signalRunner 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'signal-runner'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnvId
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: [
        {
          name: 'registry-password'
          value: containerRegistryPassword
        }
        {
          name: 'supabase-url'
          value: supabaseUrl
        }
        {
          name: 'supabase-key'
          value: supabaseServiceKey
        }
        {
          name: 'polymarket-bot-url'
          value: polymarketBotUrl
        }
      ]
      registries: [
        {
          server: containerRegistryServer
          username: containerRegistryUsername
          passwordSecretRef: 'registry-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'signal-runner'
          image: '${containerRegistryServer}/${imageName}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'SUPABASE_URL'
              secretRef: 'supabase-url'
            }
            {
              name: 'SUPABASE_SERVICE_ROLE_KEY'
              secretRef: 'supabase-key'
            }
            {
              name: 'POLYMARKET_BOT_URL'
              secretRef: 'polymarket-bot-url'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1  // Only need 1 instance
      }
    }
  }
}

output signalRunnerName string = signalRunner.name
