param localVNetId string
param remoteVNetId string
param peeringName string

var localVNetName = last(split(localVNetId, '/'))

var remoteVNetRg = split(remoteVNetId, '/')[4]
var remoteVNetName = last(split(remoteVNetId, '/'))
var remoteVNetSubscriptionId = split(remoteVNetId, '/')[2]

resource localVNet 'Microsoft.Network/virtualNetworks@2023-11-01' existing = {
  scope: resourceGroup() // local resource group
  name: localVNetName
}

resource remoteVNet 'Microsoft.Network/virtualNetworks@2023-11-01' existing = {
  scope: resourceGroup(remoteVNetSubscriptionId, remoteVNetRg) // remote resource group
  name: remoteVNetName
}

resource localToRemotePeering 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-07-01' = {
  name: peeringName
  parent: localVNet
  properties: {
    remoteVirtualNetwork: {
      id: remoteVNet.id
    }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}
