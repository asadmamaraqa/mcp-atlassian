type VNetLink = {
  linkName: string
  vnetId: string
}
param location string = 'global'
param links VNetLink[]
param zoneName string

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: zoneName
  scope: resourceGroup()
}

resource vNetLinkResources 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for link in links:{
  name: link.linkName
  location: location
  parent: privateDnsZone
  properties: {
    virtualNetwork: {
      id: link.vnetId
    }
    registrationEnabled: false
  }
}]
