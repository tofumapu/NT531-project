#!/bin/bash

set -x

# arguments: $1 = service name, $2 = image repo, $3 = tag
SERVICE=$1
IMAGE_REPO=$2
TAG=$3

# Set URL repo
REPO_URL="https://AZDO_PAT_REMOVED@dev.azure.com/tofucut3/votingApp/_git/votingApp"

# Clone repo vào /tmp
git clone "$REPO_URL" /tmp/temp_repo

# Di chuyển vào thư mục đã clone
cd /tmp/temp_repo

# Cập nhật tag image trong file deployment YAML
# Tìm dòng chứa "image:" và cập nhật tag mới
sed -i "s|image:.*${SERVICE}.*|image: <ACR-REGISTRY-NAME>/${IMAGE_REPO}/${SERVICE}:${TAG}|g" \
  k8s-specifications/${SERVICE}-deployment.yaml

# Stage và commit thay đổi
git add .
git commit -m "Update Kubernetes manifest - ${SERVICE} image tag to ${TAG}"

# Push lên Azure Repo
git push

# Dọn dẹp
rm -rf /tmp/temp_repo