[CmdletBinding()]
param(
    [string]$Profile = "graphrag-local",
    [switch]$DeleteProfile
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command minikube -ErrorAction SilentlyContinue)) {
    throw "Minikube is required. Install it before running this validation."
}
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    throw "kubectl is required. Install it before running this validation."
}

if ($DeleteProfile) {
    minikube delete --profile $Profile
    exit $LASTEXITCODE
}

minikube status --profile $Profile *> $null
if ($LASTEXITCODE -ne 0) {
    minikube start --profile $Profile --driver=docker --cpus=4 --memory=6144
    if ($LASTEXITCODE -ne 0) { throw "Minikube failed to start profile '$Profile'." }
}

minikube profile $Profile | Out-Null
kubectl cluster-info
kubectl kustomize deploy/kubernetes | Out-File -Encoding utf8 artifacts/kubernetes-rendered.yaml
if ($LASTEXITCODE -ne 0) { throw "Kustomize rendering failed." }

# Server-side dry-run validates namespaced objects against the real API server,
# but it cannot dry-run creation of a namespace and its namespaced dependants in
# one transaction. Create only the disposable validation namespace first.
kubectl apply -f deploy/kubernetes/namespace.yaml | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to create the validation namespace." }
kubectl apply --dry-run=server -k deploy/kubernetes | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Kubernetes server-side validation failed." }

Write-Host "Minikube Kubernetes validation passed for profile '$Profile'."
Write-Host "Rendered manifest: artifacts/kubernetes-rendered.yaml"
