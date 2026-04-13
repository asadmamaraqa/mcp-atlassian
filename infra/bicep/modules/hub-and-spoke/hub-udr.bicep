import * as g from 'globals/naming.bicep'

param routeTableName string
param hubFirewallIp string
param destinationCIDR string = '10.0.0.0/8'

resource routeTable 'Microsoft.Network/routeTables@2024-07-01' existing = {
  name: routeTableName
}

resource route 'Microsoft.Network/routeTables/routes@2024-07-01' = {
  parent: routeTable
  name: g.deployName('hubUDR')
  properties: {
    addressPrefix: destinationCIDR
    nextHopIpAddress: hubFirewallIp
    nextHopType: 'VirtualAppliance'
  }
}
