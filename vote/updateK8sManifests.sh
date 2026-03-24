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
cd /tmp/temp_repo

# Cập nhật image tag trong file deployment
sed -i "s|image:.*${SERVICE}.*|image: tofuvotingappacr.azurecr.io/${IMAGE_REPO}/${SERVICE}:${TAG}|g" \
    k8s-specifications/${SERVICE}-deployment.yaml

git config user.email "pipeline@azure.com"
git config user.name "Azure Pipeline"
git add .
git commit -m "Update ${SERVICE} image tag to ${TAG}"
git push

rm -rf /tmp/temp_repo