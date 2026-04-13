@export()
func deployName(name string) string => '${name}'

@export()
func resName(resType string, name string, env string) string => '${resType}-mcp-${name}-${env}'
