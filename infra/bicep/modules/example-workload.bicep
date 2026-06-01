// this module uses Azure Verified Modules that make things easier,
// that is what the br/public:avm/res/ references are about.
// this is not necessary by any means, you can do everything with native resources
import * as g from 'hub-and-spoke/globals/naming.bicep'

@description('Our vnet')
param vnetName string
param env string
param location string
param pluginName string
param workloadImage string 
param workloadImageTag string
param workloadImagePort int
param acrName string
@secure()
param acrPassword string
param routeTableId string

// Atlassian OAuth params
param atlassianOauthClientId string
@secure()
param atlassianOauthClientSecret string
param atlassianOauthRedirectUri string
param atlassianOauthScope string

var nameSuffix = 'mcp-${pluginName}-${env}'

resource ourVNet 'Microsoft.Network/virtualNetworks@2021-05-01' existing = {
  name: vnetName
}

// create subnets from our VNet
module endpointSubnet 'hub-and-spoke/subnet.bicep' = {
  name: g.deployName('subnetEndpoint')
  params: {
    vnetName: vnetName
    subnetName: 'subnet-endpoint'
    CBlock: 2
  }
}

module caeSubnet 'hub-and-spoke/subnet.bicep' = {
  dependsOn: [
    endpointSubnet
  ]
  name: g.deployName('subnetCae')
  params: {
    vnetName: vnetName
    subnetName: 'subnet-cae'
    CBlock: 64
    size: 23
    delegation: 'Microsoft.App/environments'
    // This subnet will allow traffic from/to MCP Garden. Leave out if plugin-internal subnet.
    routeTableResourceId: routeTableId
  }
}

module cae 'br/public:avm/res/app/managed-environment:0.11.3' = {
  name: g.deployName('cae')
  params: {
    name: 'cae-${nameSuffix}'
    location: location
    enableTelemetry: false
    infrastructureSubnetResourceId: caeSubnet.outputs.subnetResourceId
    infrastructureResourceGroupName: 'rg-infra-cae-${nameSuffix}'  
    internal: true
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

module ca 'br/public:avm/res/app/container-app:0.18.1' = {
  name: g.deployName('ca')
  params: {
    name: 'ca-${nameSuffix}'
    location: location
    enableTelemetry: false
    ingressExternal: true
    ingressTargetPort: workloadImagePort
    ingressTransport: 'http'
    ingressAllowInsecure: true
    managedIdentities: {
      systemAssigned: true
    }
    scaleSettings: {
      minReplicas: 1
      maxReplicas: 3
    }
    secrets: [
      {
        name: 'acr-password'
        value: acrPassword
      }
      {
        name: 'atlassian-oauth-client-secret'
        value: atlassianOauthClientSecret
      }
    ]
    registries: [
      {
        server: '${acrName}.azurecr.io'
        username: acrName
        passwordSecretRef: 'acr-password'
      }
    ]
    containers: [
      {
        image: '${acrName}.azurecr.io/${workloadImage}:${workloadImageTag}'
        name: 'tool-backend'
        resources: {
          cpu: '0.5'
          memory: '1Gi'
        }
        probes: [
          {
            type: 'Startup'
            tcpSocket: {
              port: workloadImagePort
            }
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 10
            timeoutSeconds: 10
          }
          {
            type: 'Readiness'
            tcpSocket: {
              port: workloadImagePort
            }
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 10
          }
        ]
        env: [
          {
            name: 'NAME'
            value: pluginName
          }
          {
            name: 'ATLASSIAN_OAUTH_CLIENT_ID'
            value: atlassianOauthClientId
          }
          {
            name: 'ATLASSIAN_OAUTH_CLIENT_SECRET'
            secretRef: 'atlassian-oauth-client-secret'
          }
          {
            name: 'ATLASSIAN_OAUTH_REDIRECT_URI'
            value: atlassianOauthRedirectUri
          }
          {
            name: 'ATLASSIAN_OAUTH_SCOPE'
            value: atlassianOauthScope
          }
          {
            name: 'ATLASSIAN_OAUTH_PROXY_ENABLE'
            value: 'true'
          }
          {
            name: 'ATLASSIAN_OAUTH_REQUIRE_CONSENT'
            value: 'true'
          }
          {
            name: 'ATLASSIAN_OAUTH_ALLOWED_GRANT_TYPES'
            value: 'authorization_code,refresh_token'
          }
          {
            name: 'ATLASSIAN_OAUTH_ENABLE'
            value: 'true'
          }
          {
            name: 'ATLASSIAN_OAUTH_ALLOWED_CLIENT_REDIRECT_URIS'
            value: 'http://localhost:*,http://127.0.0.1:*,https://solitaire-dev.solitaservices.com/*'
          }
          {
            name: 'TRANSPORT'
            value: 'streamable-http'
          }
          {
            name: 'MCP_VERY_VERBOSE'
            value: 'true'
          }
        ]
      }
    ]
    environmentResourceId: cae.outputs.resourceId
  }
}

module dnsZone './hub-and-spoke/dns-zone.bicep' = {
  name: g.deployName('dnsZone')
  params: {
    location: 'global'
    env: env
    name: cae.outputs.defaultDomain
    linkVNetId: ourVNet.id
    aRecords: { 
      '*': cae.outputs.staticIp 
    }
  }
}
