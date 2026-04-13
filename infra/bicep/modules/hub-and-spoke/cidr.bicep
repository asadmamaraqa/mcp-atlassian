param pluginB int

@description('Whole plugin VNet address space')
output pluginVNetCIDR string = '10.${pluginB}.0.0/16'
@description('Subnet in plugin that is routed to the hub')
output pluginSubnetCIDR string = '10.${pluginB}.1.0/24'
