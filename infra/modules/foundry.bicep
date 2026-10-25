// Foundry account, project, model deployments and the roles that let your identities call them. No keys: local auth is disabled.
targetScope = 'resourceGroup'

param location string
param name string
param projectName string = 'nextwhistle'
param tags object

@description('Each: { name, format, model, version, sku, capacity }. Capacity is in thousands of tokens per minute.')
param deployments array

@description('Each: { key, id, type } where key is a fixed label (used for a stable role-assignment name), id is the object ID and type is User or ServicePrincipal.')
param principals array

var roleFoundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d' // "Foundry User" (formerly "Azure AI User")
var roleOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: name
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Next Whistle', description: 'Contest project' }
}

// Deployments on one account are created one at a time.
@batchSize(1)
resource modelDeployments 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [for d in deployments: {
  parent: foundry
  name: d.name
  sku: { name: d.sku, capacity: d.capacity }
  properties: { model: { format: d.format, name: d.model, version: d.version } }
  dependsOn: [ project ]  // the account accepts one change at a time: project first, then each deployment
}]

resource foundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in principals: {
  scope: foundry
  name: guid(foundry.id, p.key, roleFoundryUser)
  properties: {
    principalId: p.id
    principalType: p.type
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
  }
}]

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in principals: {
  scope: foundry
  name: guid(foundry.id, p.key, roleOpenAiUser)
  properties: {
    principalId: p.id
    principalType: p.type
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleOpenAiUser)
  }
}]

output accountName string = foundry.name
output projectEndpoint string = 'https://${name}.services.ai.azure.com/api/projects/${project.name}'
output deploymentNames array = [for (d, i) in deployments: modelDeployments[i].name]
