#!/bin/bash

REPO_NAME="paper-trading"
GITHUB_USER="namana843-bit"
PROJECT_PATH="/c/Users/Naman/paper-trading"

echo "================================================"
echo "  Uploading paper-trading to GitHub (private)"
echo "================================================"
echo ""

cd "$PROJECT_PATH" || { echo "ERROR: Cannot find project path"; exit 1; }

# Stage and commit
echo "[1/4] Staging all files..."
git add .

echo "[2/4] Committing..."
git commit -m "Initial commit - Paper Trading Bot" 2>/dev/null || echo "  (nothing new to commit, continuing...)"

# Get GitHub token
echo ""
echo "[3/4] Creating private GitHub repo..."
echo ""
echo "  Enter your GitHub Personal Access Token (classic)"
echo "  Get one here: https://github.com/settings/tokens/new"
echo "  (Select 'repo' scope, then generate and paste below)"
echo ""
read -s -p "  Token: " TOKEN
echo ""

# Create the repo via API
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/user/repos \
  -d "{\"name\":\"$REPO_NAME\",\"private\":true,\"description\":\"Paper Trading Bot\"}")

if [ "$RESPONSE" = "201" ]; then
  echo "  ✅ Repo created: https://github.com/$GITHUB_USER/$REPO_NAME"
elif [ "$RESPONSE" = "422" ]; then
  echo "  ⚠️  Repo already exists, proceeding to push..."
else
  echo "  ❌ Failed to create repo (HTTP $RESPONSE). Check your token and try again."
  exit 1
fi

# Set remote
echo ""
echo "[4/4] Pushing to GitHub..."
git remote remove origin 2>/dev/null
git remote add origin "https://$GITHUB_USER:$TOKEN@github.com/$GITHUB_USER/$REPO_NAME.git"
git branch -M main
git push -u origin main

echo ""
echo "================================================"
echo "  ✅ DONE! View your repo at:"
echo "  https://github.com/$GITHUB_USER/$REPO_NAME"
echo "================================================"
