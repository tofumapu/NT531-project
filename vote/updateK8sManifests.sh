#!/bin/bash

set -x
set -e

SERVICE_NAME=$1
IMAGE_REPO=$2
TAG=$3

git config --global user.email "pipeline@devops.com"
git config --global user.name "Azure Pipeline"

REPO_URL="https://AZDO_PAT_REMOVED@dev.azure.com/tofucut3/votingApp/_git/votingApp"

# Chỉ định đúng branch
git clone -b dependabot/npm_and_yarn/result/express-4.19.2 "$REPO_URL" /tmp/temp_repo

cd /tmp/temp_repo

sed -i "s|image:.*|image: tofuvotingappacr.azurecr.io/${IMAGE_REPO}/${SERVICE_NAME}:${TAG}|g" \
    k8s-specifications/${SERVICE_NAME}-deployment.yaml

echo "=== Kiểm tra sau sed ==="
cat k8s-specifications/${SERVICE_NAME}-deployment.yaml | grep image

git add k8s-specifications/${SERVICE_NAME}-deployment.yaml
git diff --staged --quiet && echo "Không có thay đổi" || git commit -m "Update ${SERVICE_NAME} image to tag ${TAG}"
git push

rm -rf /tmp/temp_repo