import * as g from 'globals/naming.bicep'

param location string
param env string
param spokeName string
@description('VNet address space')
param spokeCIDR string

var nameSuffix = 'mcp-${spokeName}-${env}'
var spokeVNetName = 'vnet-${nameSuffix}'

// Spoke vnet
module spokeVnet 'br/public:avm/res/network/virtual-network:0.7.0' = {
  name: g.deployName('spokeVnet')
  params: {
    name: spokeVNetName
    location: location
    enableTelemetry: false
    addressPrefixes: [
      spokeCIDR
    ]
  }
}

output spokeVNetName string = spokeVnet.outputs.name
output spokeVNetId string = spokeVnet.outputs.resourceId
