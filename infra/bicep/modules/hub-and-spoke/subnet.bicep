import * as g from 'globals/naming.bicep'

param vnetName string
param subnetName string
@description('If vNet is 10.1.0.0/16 and CBlock is 2, subnet will be 10.1.2.0/24')
param CBlock int?
@description('Override subnet size, default is /24')
param size int = 24
@description('Route table to associate. Give the routeTableId output from plugin setup module to connect with MCP Garden')
param routeTableResourceId string?
param delegation string?

resource vnet 'Microsoft.Network/virtualNetworks@2021-05-01' existing = {
  name: vnetName
}

// Calculate CIDR for the subnet from VNet's address space and the provided CBlock
var justRange = split(vnet.properties.addressSpace.addressPrefixes[0], '/')[0]
var octets = split(justRange, '.')
var cidr = '${octets[0]}.${octets[1]}.${CBlock}.0/${size}'

module subnet 'br/public:avm/res/network/virtual-network/subnet:0.1.2' = {
  name: g.deployName(subnetName)
  params: {
    enableTelemetry: false
    virtualNetworkName: vnetName
    name: subnetName
    addressPrefix: cidr
    routeTableResourceId: routeTableResourceId
    delegation: delegation
    defaultOutboundAccess: false // only way out is MCP Hub 
  }
}

output subnetResourceId string = subnet.outputs.resourceId
output subnetName string = subnetName
output subnetCIDR string = cidr
