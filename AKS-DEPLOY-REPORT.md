# AKS Deployment Report

Date: 2026-04-11
Target branch: `dependabot/npm_and_yarn/result/express-4.19.2`

## Completed

- Added application metrics to `vote`, `result`, and `worker`
- Added Prometheus scraping resources for the voting app
- Added AKS deployment pipeline file: `azure-pipelines-aks.yml`
- Configured the pipeline to trigger from branch `dependabot/npm_and_yarn/result/express-4.19.2`
- Configured Docker build and push for:
  - `tofuvotingappacr.azurecr.io/votingapp/vote`
  - `tofuvotingappacr.azurecr.io/votingapp/result`
  - `tofuvotingappacr.azurecr.io/votingapp/worker`
- Configured AKS deployment through Azure DevOps using a `Kubernetes Service Connection`
- Configured manifest rendering so Kubernetes deployments use the current `$(Build.BuildId)` image tag
- Removed legacy `imagePullSecrets` from rendered manifests during deployment because AKS should pull from ACR after `attach-acr`
- Added rollout verification for `vote`, `result`, and `worker`
- Fixed a worker runtime issue where the PostgreSQL keep-alive command could point to a stale connection after reconnect

## Required before first pipeline run

- Replace `REPLACE_WITH_KUBERNETES_SERVICE_CONNECTION` in `azure-pipelines-aks.yml` with the real Kubernetes service connection name from Azure DevOps
- Ensure AKS is attached to ACR:
  - `az aks update --name votingApp-k8s --resource-group AzureStudentLab --attach-acr tofuvotingappacr`
- Ensure the Docker Registry service connection name is exactly `tofuVotingAppACR`

## Verification steps

1. Open Azure DevOps Repos and confirm this branch contains:
   - `azure-pipelines-aks.yml`
   - `AKS-DEPLOY-REPORT.md`
2. Open Azure DevOps Pipelines and create a pipeline from `azure-pipelines-aks.yml`
3. Verify the variable `kubernetesServiceConnection` matches your actual Kubernetes service connection name
4. Run the pipeline on branch `dependabot/npm_and_yarn/result/express-4.19.2`
5. Confirm rollout in AKS:
   - `kubectl get pods -n default`
   - `kubectl get svc -n default`
6. Confirm metrics endpoints are reachable from the running workloads
