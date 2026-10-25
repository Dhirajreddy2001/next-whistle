// Foundry only: the account, project and models, for testing agents from a laptop before the rest is deployed.
targetScope = 'resourceGroup'

param location string = resourceGroup().location
param namePrefix string = 'nextwhistle'
param tags object = {
  Project: 'NextWhistle'
  Environment: 'Contest'
  Owner: 'Dhiraj'
  JudgingEnds: '2026-11-11T07:59:00Z'
  ExpiresOn: '2026-11-11T08:15:00Z'
}

@description('Object ID of your Entra user, so your own login can call the models.')
param developerPrincipalId string

@description('Object ID of the local service account (sp-nextwhistle-local).')
param localServicePrincipalId string

@description('Models to deploy. Capacity is thousands of tokens per minute and must fit your subscription quota.')
param deployments array = [
  { name: 'deepseek', format: 'DeepSeek', model: 'DeepSeek-V4-Flash', version: '2026-04-23', sku: 'GlobalStandard', capacity: 20 }
]

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  params: {
    location: location
    name: take('ai-${namePrefix}-${uniqueString(resourceGroup().id)}', 64)
    tags: tags
    deployments: deployments
    principals: [
      { key: 'developer', id: developerPrincipalId, type: 'User' }
      { key: 'local-sp', id: localServicePrincipalId, type: 'ServicePrincipal' }
    ]
  }
}

output foundryAccountName string = foundry.outputs.accountName
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output deploymentNames array = foundry.outputs.deploymentNames
