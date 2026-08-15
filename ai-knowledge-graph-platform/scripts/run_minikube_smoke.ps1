[CmdletBinding()]
param([string]$Profile = "graphrag-local")

$ErrorActionPreference = "Stop"
$namespace = "graphrag"

minikube profile $Profile | Out-Null
minikube image load ai-knowledge-graph-platform-mcp:latest --profile $Profile
minikube image load ai-knowledge-graph-platform-api:latest --profile $Profile

# The production tree deliberately excludes secrets. These disposable values
# are sufficient for process startup and must never be reused outside Minikube.
kubectl -n $namespace create secret generic graphrag-secrets `
    --from-literal=NEO4J_URI=bolt://neo4j:7687 `
    --from-literal=NEO4J_USER=neo4j `
    --from-literal=NEO4J_PASSWORD=local-only `
    --from-literal=RABBITMQ_URL=amqp://rabbitmq `
    --from-literal=REDIS_URL=redis://redis:6379 `
    --from-literal=TIMESCALE_DB_URL=postgresql://postgres:postgres@timescaledb:5432/postgres `
    --from-literal=JWT_SECRET_KEY=local-only-jwt `
    --from-literal=SESSION_SECRET_KEY=local-only-session `
    --from-literal=OPENAI_API_KEY=local-only `
    --from-literal=DEEPSEEK_API_KEY=local-only `
    --from-literal=GROQ_API_KEY=local-only `
    --dry-run=client -o yaml | kubectl apply -f - | Out-Null

kubectl apply -k deploy/kubernetes | Out-Null
kubectl -n $namespace scale deployment graphrag-ingestion-worker graphrag-query-worker --replicas=0
kubectl -n $namespace set image deployment/graphrag-api api=ai-knowledge-graph-platform-api:latest
kubectl -n $namespace set image deployment/graphrag-mcp mcp=ai-knowledge-graph-platform-mcp:latest
kubectl -n $namespace rollout restart deployment/graphrag-api deployment/graphrag-mcp
kubectl -n $namespace rollout status deployment/graphrag-mcp --timeout=180s

$portForward = Start-Process kubectl -ArgumentList @(
    "-n", $namespace, "port-forward", "service/graphrag-mcp", "18002:8002"
) -PassThru -WindowStyle Hidden
try {
    Start-Sleep -Seconds 3
    $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:18002/health"
    if ($health.StatusCode -ne 200) { throw "MCP health returned HTTP $($health.StatusCode)." }
    Write-Host "Minikube MCP pod smoke test passed: HTTP $($health.StatusCode)."
}
finally {
    Stop-Process -Id $portForward.Id -Force -ErrorAction SilentlyContinue
}
