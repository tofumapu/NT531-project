#!/bin/bash

set -x

# $1 = service name (vote / result / worker)
# $2 = imageRepository
# $3 = tag (Build.BuildId)

SERVICE_NAME=$1
IMAGE_REPO=$2
TAG=$3

# Set the repository URL (thay ACCESS-TOKEN và ORG-NAME của bạn)
REPO_URL="https://AZDO_PAT_REMOVED@dev.azure.com/tofucut3/votingApp/_git/votingApp"

# Clone repo vào thư mục tạm
git clone "$REPO_URL" /tmp/temp_repo

# Di chuyển vào repo
cd /tmp/temp_repo

# Cập nhật image tag trong file deployment yaml
# Ví dụ: image: gabvotingappacr.azurecr.io/votingapp/vote:17  →  :18
sed -i "s|image:.*|image: tofuvotingappacr.azurecr.io/${IMAGE_REPO}/${SERVICE_NAME}:${TAG}|g" \
    k8s-specifications/${SERVICE_NAME}-deployment.yaml

# Stage, commit và push thay đổi
git add .
git commit -m "Update Kubernetes manifest - ${SERVICE_NAME} image to tag ${TAG}"
git push

# Dọn dẹp
rm -rf /tmp/temp_repo