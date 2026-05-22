param env string
param location string
param pluginVnetCidrB int
param pluginName string
param mcpServerImage string
param mcpServerImagePort int
param mcpImageTag string = 'latest'
param acrName string
@secure()
param acrPassword string

// Atlassian OAuth params
param atlassianOauthClientId string
@secure()
param atlassianOauthClientSecret string
param atlassianOauthRedirectUri string
param atlassianOauthScope string
param publicBaseUrl string

module setup './modules/plugin-setup.bicep' = {
  name: '${deployment().name}-spoke'
  params: {
    env: env
    location: location
    pluginName: pluginName
    vNetCidrB: pluginVnetCidrB
  }
}

// custom workload here
// use subnet.bicep to create subnets in the spoke vnet,
// attach route table from setup.outputs.routeTableId to subnets that need to access to other spokes

module exampleWorkload './modules/example-workload.bicep' = {
  name: '${deployment().name}-exampleWorkload'
  params: {
    env: env
    location: location
    pluginName: pluginName
    vnetName: setup.outputs.vnetName
    routeTableId: setup.outputs.routeTableId
    workloadImage: mcpServerImage
    workloadImageTag: mcpImageTag
    workloadImagePort: mcpServerImagePort
    acrName: acrName
    acrPassword: acrPassword
    atlassianOauthClientId: atlassianOauthClientId
    atlassianOauthClientSecret: atlassianOauthClientSecret
    atlassianOauthRedirectUri: atlassianOauthRedirectUri
    atlassianOauthScope: atlassianOauthScope
    publicBaseUrl: publicBaseUrl
  }
}

// add resources, your vnet is spoke.outputs.vnetName
