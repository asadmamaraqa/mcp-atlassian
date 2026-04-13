ENV=$1

export FWIP=$(az network firewall show \
    -n afw-mcp-hub-${ENV} \
    -g rg-mcp-hub-${ENV} \
    --subscription sub-mcp-garden-${ENV} \
    --query "ipConfigurations[0].privateIPAddress" -o tsv)
echo "FWIP: $FWIP"
export MCP_HUB_VNET_ID=$(az network vnet show \
    -g rg-mcp-hub-${ENV} \
    -n vnet-mcp-hub-${ENV} \
    --subscription sub-mcp-garden-${ENV} \
    --query "id" -o tsv)
echo "MCP_HUB_VNET_ID: $MCP_HUB_VNET_ID"
export CLIENT_HUB_VNET_ID=$(az network vnet show \
    -g rg-solitaire-${ENV}-vnet \
    -n vnet-solitaire-${ENV} \
    --subscription sub-solitaire-lz-${ENV} \
    --query "id" -o tsv)
echo "CLIENT_HUB_VNET_ID: $CLIENT_HUB_VNET_ID"

echo "MCP_HUB_FIREWALL_IP=$FWIP" >> $GITHUB_ENV
echo "MCP_HUB_VNET_ID=$MCP_HUB_VNET_ID" >> $GITHUB_ENV
echo "CLIENT_HUB_VNET_ID=$CLIENT_HUB_VNET_ID" >> $GITHUB_ENV
