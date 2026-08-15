# Local Kubernetes validation

The repository already contains hardened Kubernetes manifests for the API,
MCP gateway, workers, service account, network policy, probes, resource limits,
read-only filesystems, non-root containers, HPA, and PodDisruptionBudgets.

Validate the rendered objects locally with Docker Desktop Kubernetes, kind, or
k3d. The commands below do not claim a production deployment:

```powershell
kubectl kustomize deploy/kubernetes > artifacts/kubernetes-rendered.yaml
kubectl apply --dry-run=client -k deploy/kubernetes
kubectl auth can-i --list --as=system:serviceaccount:graphrag:graphrag-workload -n graphrag
```

With a local cluster and locally tagged images, deploy and exercise rollout,
readiness, restart, and rollback:

```powershell
kubectl apply -k deploy/kubernetes
kubectl rollout status deployment/graphrag-api -n graphrag
kubectl rollout status deployment/graphrag-mcp -n graphrag
kubectl rollout restart deployment/graphrag-mcp -n graphrag
kubectl rollout undo deployment/graphrag-mcp -n graphrag
kubectl get networkpolicy,pdb,hpa -n graphrag
```

Record the cluster version, node capacity, image digests, pod counts, rollout
time, restart recovery, and probe failures in the evidence report. Kubernetes
manifests prove deployability and intended controls; they do not prove uptime,
autoscaling effectiveness, or customer-scale capacity.
