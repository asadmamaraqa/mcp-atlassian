@description('Full name, e.g. privatelink.search.windows.net')
param name string
param env string
param location string = 'global'
param linkVNetId string
@description('Key: record name, value: target')
param cNames object = {}
@description('Key: record name, value: target')
param aRecords object = {}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: name
  location: location
}

// DNS zone links to our vnet
module localDnsZoneLink './dns-zone-links.bicep' = {
  name: 'localDnsZoneLink'
  params: {
    location: location
    zoneName: name
    links: [
      {
        linkName: 'link-local-${env}'
        vnetId: linkVNetId
      }
    ]
  }
  dependsOn: [
    privateDnsZone
  ]
}

resource cnameRecords 'Microsoft.Network/privateDnsZones/CNAME@2020-06-01' = [for cnameKey in objectKeys(cNames): {
  name: cnameKey
  parent: privateDnsZone
  properties: {
    ttl: 3600
     cnameRecord: {
      cname: cNames[cnameKey]
    }
  } 
}]

resource aRecordsRes 'Microsoft.Network/privateDnsZones/A@2020-06-01' = [for aKey in objectKeys(aRecords): {
  name: aKey
  parent: privateDnsZone
  properties: {
    ttl: 3600
    aRecords: [
      {
        ipv4Address: aRecords[aKey]
      }
    ]
  } 
}]
