import * as g from 'globals/naming.bicep'

param env string
param pluginName string
param location string

var nameSuffix = 'mcp-${pluginName}-${env}'

module routeTableBackend 'br/public:avm/res/network/route-table:0.5.0' = {
  name: g.deployName('routeTable')
  params: {
    name: 'rt-${nameSuffix}'
    location: location
    enableTelemetry: false
  }
}

output routeTableId string = routeTableBackend.outputs.resourceId
output routeTableName string = routeTableBackend.outputs.name
