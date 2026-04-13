@description('Deployment environment (last part of naming convention)')
param env string
@description('Plugin name (middle part of naming convention)')
param pluginName string
@description('Comma-separated list of DNS zone names to link')
param mcpDnsZoneNames string
@description('The MCP client VNet resource ID')
param mcpClientVNetId string
@description('The MCP hub VNet resource ID')
param mcpHubVNetId string
@description('The MCP hub firewall private IP address')
param mcpHubFirewallIp string
@description('What traffic is routed through MCP Hub firewall')
param mcpClientCIDR string = '10.0.0.0/8'

var dnsZoneList = split(mcpDnsZoneNames, ',')
var spokeVNetId = resourceId('Microsoft.Network/virtualNetworks', 'vnet-mcp-${pluginName}-${env}')
var mcpHubVNetRG = split(mcpHubVNetId, '/')[4]
var mcpHubVNetSubscriptionId = split(mcpHubVNetId, '/')[2]

// Peer our spoke vnet to the MCP hub vnet
module hubPeeringLocal './modules/hub-and-spoke/vnet-peering.bicep' = {
  name: 'hubPeeringLocal'
  scope: resourceGroup() // current resource group
  params: {
    peeringName: 'to-hub-${env}'
    localVNetId: spokeVNetId
    remoteVNetId: mcpHubVNetId
  }
}

// Peer MCP hub vnet to our spoke vnet
module hubPeeringRemote './modules/hub-and-spoke/vnet-peering.bicep' = {
  name: 'hubPeeringRemote'
  scope: resourceGroup(mcpHubVNetSubscriptionId, mcpHubVNetRG) // MCP hub resource group
  params: {
    peeringName: 'to-${pluginName}-${env}'
    localVNetId: mcpHubVNetId
    remoteVNetId: spokeVNetId
  }
}

// DNS zone links to MCP client vnet (locals are already made)
module dnsZoneLinks 'modules/hub-and-spoke/dns-zone-links.bicep' = [for dnsZoneName in dnsZoneList: {
  name: 'dnsZoneLink-MCP${indexOf(dnsZoneList, dnsZoneName)}'
  params: {
    zoneName: dnsZoneName
    links: [{
      linkName: 'link-mcp-${env}'
      vnetId: mcpClientVNetId
    }]
  }
}]

// Create the Hub route in existing Route Table
module HubUDR 'modules/hub-and-spoke/hub-udr.bicep' = {
  name: 'rt-udr-hub'
  params: {
    routeTableName: 'rt-mcp-${pluginName}-${env}'
    hubFirewallIp: mcpHubFirewallIp
    destinationCIDR: mcpClientCIDR 
  }
}
