param endpointNme string
param location string
param subnetId string
param privateLinkServiceId string
param subResource string
param dnsZoneId string?

resource privateEndpointResource 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: endpointNme
  location: location
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'pls-connection'
        properties: {
          privateLinkServiceId: privateLinkServiceId
          groupIds: [
            subResource
          ]
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = if (dnsZoneId != null) {
  name: 'default'
  parent: privateEndpointResource
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config-${endpointNme}'
        properties: {
          privateDnsZoneId: dnsZoneId
        }
      }
    ]
  }
}
