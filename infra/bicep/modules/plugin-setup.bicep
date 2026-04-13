import * as g from 'hub-and-spoke/globals/naming.bicep'

param env string
param location string
param vNetCidrB int
param pluginName string

module cidr './hub-and-spoke/cidr.bicep' = {
  name: g.deployName('cidr')
  params: {
    pluginB: vNetCidrB
  }
}

module spoke './hub-and-spoke/spoke.bicep' = {
  name: g.deployName('spoke')
  params: {
    env: env
    location: location
    spokeName: pluginName
    spokeCIDR: cidr.outputs.pluginVNetCIDR
  }
}

module routeTable 'hub-and-spoke/hubroutetable.bicep' = {
  name: g.deployName('spokeRouteTable')
  params: {
    location: location
    env: env
    pluginName: pluginName
  }
}

output vnetName string = spoke.outputs.spokeVNetName
output vnetId string = spoke.outputs.spokeVNetId
output spokeVNetCIDR string = cidr.outputs.pluginVNetCIDR
output routeTableId string = routeTable.outputs.routeTableId
output routeTableName string = routeTable.outputs.routeTableName
