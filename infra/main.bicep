// Next Whistle contest environment. One resource group, one workload identity, everything tagged for one-step deletion.
// Deploy with infra/deploy.sh (it confirms the subscription first). Delete with infra/destroy.sh.
targetScope = 'resourceGroup'

@description('Azure region for most resources. Foundry model availability and quota differ by region.')
param location string = resourceGroup().location

@description('Region for the free Static Web Apps plan (it supports only a few).')
param staticSiteLocation string = 'eastus2'

@description('Prefix for resource names.')
param namePrefix string = 'nextwhistle'

@description('Core API image, pinned by digest after the first build, e.g. ghcr.io/<owner>/next-whistle-core@sha256:...')
param coreImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Agent Service image, pinned by digest after the first build, e.g. ghcr.io/<owner>/next-whistle-agent@sha256:...')
param agentImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@secure()
@description('Anonymous-session signing key, at least 32 characters.')
param sessionSigningKey string

@secure()
@description('Key used to hash client addresses.')
param ipHashKey string

@description('Deploy the Container App and Static Web App. Leave false while testing locally; the data, identity and Foundry pieces are always created.')
param deployApp bool = false

@description('Free tier is allowed once per subscription. Set false if this subscription already has a free Cosmos DB account.')
param cosmosFreeTier bool = true

@description('Object ID of your own Entra user, so you can call Foundry from your laptop for evaluations. Leave empty to skip.')
param developerPrincipalId string = ''

@description('Email addresses for budget alerts.')
param budgetContactEmails array

@description('Total project budget in USD. The whole contest must stay under this.')
param budgetUsd int = 20

@description('First day of the month the budget starts, YYYY-MM-01.')
param budgetStartDate string

@description('The one model every agent role uses. Capacity is thousands of tokens per minute and must fit your subscription quota.')
param model object = { name: 'DeepSeek-V4-Flash', version: '2026-04-23', sku: 'GlobalStandard', capacity: 20 }

param tags object = {
  Project: 'NextWhistle'
  Environment: 'Contest'
  Owner: 'Dhiraj'
  JudgingEnds: '2026-11-11T07:59:00Z'
  ExpiresOn: '2026-11-11T08:15:00Z'
}

var suffix = uniqueString(resourceGroup().id)
var storageName = take('st${namePrefix}${suffix}', 24)
var cosmosName = take('cosmos-${namePrefix}-${suffix}', 44)
var foundryName = take('ai-${namePrefix}-${suffix}', 64)

// Built-in role definitions.
var roleBlobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var cosmosDataContributor = '00000000-0000-0000-0000-000000000002'

// ---- the workload identity ("service account"): every service below is granted access to this one identity ----
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${namePrefix}'
  location: location
  tags: tags
}

// ---- monitoring --------------------------------------------------------------------------------------------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${namePrefix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.1') }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${namePrefix}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    SamplingPercentage: 20
  }
}

// ---- datasets and rules (read-only at runtime) ----------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for name in ['datasets', 'rules', 'narratives', 'temporary']: {
  parent: blobs
  name: name
  properties: { publicAccess: 'None' }
}]

resource storageIdentityRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, identity.id, roleBlobContributor)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleBlobContributor)
  }
}

// ---- state: Cosmos DB free tier, identity-only access (no account keys) ---------------------------------------
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: cosmosFreeTier
    disableLocalAuth: true
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [ { locationName: location, failoverPriority: 0, isZoneRedundant: false } ]
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: 'nextwhistle'
  properties: {
    resource: { id: 'nextwhistle' }
    options: { throughput: 400 }
  }
}

resource cosmosState 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'state'
  properties: {
    resource: {
      id: 'state'
      partitionKey: { paths: [ '/pk' ], kind: 'Hash' }
      defaultTtl: -1
    }
  }
}

resource cosmosIdentityRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, identity.id, cosmosDataContributor)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    principalId: identity.properties.principalId
    scope: cosmos.id
  }
}

// Optional: your own login may use the state database from a laptop (no key needed).
resource developerCosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (!empty(developerPrincipalId)) {
  parent: cosmos
  name: guid(cosmos.id, developerPrincipalId, cosmosDataContributor)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    principalId: developerPrincipalId
    scope: cosmos.id
  }
}

// ---- Foundry: account, project, model deployments (shared module, also used by foundry.bicep) --------------------
module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  params: {
    location: location
    name: foundryName
    tags: tags
    deployments: [
      { name: 'deepseek', format: 'DeepSeek', model: model.name, version: model.version, sku: model.sku, capacity: model.capacity }
    ]
    principals: concat(
      [ { key: 'workload', id: identity.properties.principalId, type: 'ServicePrincipal' } ],
      empty(developerPrincipalId) ? [] : [ { key: 'developer', id: developerPrincipalId, type: 'User' } ]
    )
  }
}

// ---- Core API on Container Apps --------------------------------------------------------------------------------
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = if (deployApp) {
  name: 'cae-${namePrefix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// The agent is reachable only from inside the environment (internal ingress), so it needs no login of its own.
resource agent 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: 'ca-${namePrefix}-agent'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: { external: false, targetPort: 8088, transport: 'auto', allowInsecure: false }
    }
    template: {
      containers: [
        {
          name: 'agent'
          image: agentImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AGENT_MODE', value: 'foundry' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'FOUNDRY_OPENAI_BASE_URL', value: 'https://${foundryName}.services.ai.azure.com/openai/v1' }
            { name: 'MODEL_TOOLS', value: 'deepseek' }
            { name: 'MODEL_SMALL', value: 'deepseek' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [ { name: 'http', http: { metadata: { concurrentRequests: '10' } } } ]
      }
    }
  }
}

resource core 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: 'ca-${namePrefix}-core'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      secrets: [
        { name: 'session-signing-key', value: sessionSigningKey }
        { name: 'ip-hash-key', value: ipHashKey }
      ]
    }
    template: {
      containers: [
        {
          name: 'core'
          image: coreImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'APP_ENV', value: 'production' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'STORE_BACKEND', value: 'cosmos' }
            { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
            { name: 'COSMOS_DATABASE', value: 'nextwhistle' }
            { name: 'COSMOS_CONTAINER', value: 'state' }
            { name: 'ALLOWED_ORIGINS', value: 'https://${site!.properties.defaultHostname}' }
            { name: 'AGENT_SERVICE_URL', value: 'https://${agent!.properties.configuration.ingress.fqdn}' }
            { name: 'TRUST_PROXY', value: 'true' }
            { name: 'SESSION_SIGNING_KEY', secretRef: 'session-signing-key' }
            { name: 'IP_HASH_KEY', secretRef: 'ip-hash-key' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [ { name: 'http', http: { metadata: { concurrentRequests: '20' } } } ]
      }
    }
  }
}

// ---- frontend ---------------------------------------------------------------------------------------------------
resource site 'Microsoft.Web/staticSites@2023-12-01' = if (deployApp) {
  name: 'swa-${namePrefix}-${suffix}'
  location: staticSiteLocation
  tags: tags
  sku: { name: 'Free', tier: 'Free' }
  properties: {}
}

// ---- cost guard: the contest ceiling, scoped to this resource group ----------------------------------------------
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-${namePrefix}'
  properties: {
    category: 'Cost'
    amount: budgetUsd
    timeGrain: 'Annually'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      usd5: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 25, thresholdType: 'Actual', contactEmails: budgetContactEmails }
      usd10: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 50, thresholdType: 'Actual', contactEmails: budgetContactEmails }
      usd15: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 75, thresholdType: 'Actual', contactEmails: budgetContactEmails }
      usd18: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 90, thresholdType: 'Actual', contactEmails: budgetContactEmails }
      usd1950: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 97, thresholdType: 'Actual', contactEmails: budgetContactEmails }
    }
  }
}

output identityClientId string = identity.properties.clientId
output coreUrl string = deployApp ? 'https://${core.?properties.configuration.ingress.fqdn}' : ''
output staticSiteHost string = deployApp ? '${site.?properties.defaultHostname}' : ''
output agentInternalUrl string = deployApp ? 'https://${agent.?properties.configuration.ingress.fqdn}' : ''
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output modelToolsDeployment string = 'deepseek'
output modelSmallDeployment string = 'deepseek'
output foundryAccountName string = foundry.outputs.accountName
output storageAccountName string = storage.name
