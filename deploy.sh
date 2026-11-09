#!/bin/bash
# SMARTBIN Deployment Script
# Use this to trigger a manual deployment to Render

set -e

echo "=========================================="
echo "SMARTBIN Deployment Script"
echo "=========================================="

# Check if git is available
if ! command -v git &> /dev/null; then
    echo "Error: git is not installed"
    exit 1
fi

# Check if we're in a git repo
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "Error: Not a git repository"
    exit 1
fi

# Show current status
echo ""
echo "Current branch: $(git branch --show-current)"
echo "Latest commit: $(git log -1 --pretty=format:'%h - %s (%ar)')"
echo ""

# Check for uncommitted changes
if ! git diff-index --quiet HEAD; then
    echo "Warning: You have uncommitted changes"
    echo ""
    read -p "Commit and push changes? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Staging all changes..."
        git add .
        
        # Read commit message or use default
        read -p "Enter commit message (default: 'Deploy to Render'): " commit_msg
        commit_msg=${commit_msg:-"Deploy to Render"}
        
        echo "Committing changes..."
        git commit -m "$commit_msg"
    else
        echo "Aborting deployment"
        exit 0
    fi
fi

# Push to trigger auto-deploy
echo ""
echo "Pushing to repository..."
git push

echo ""
echo "=========================================="
echo "Deployment triggered!"
echo "=========================================="
echo ""
echo "Your changes are being deployed to Render."
echo "Monitor the deployment at:"
echo "https://dashboard.render.com"
echo ""
echo "You can check logs at:"
echo "https://dashboard.render.com/widgets/services"
echo ""
