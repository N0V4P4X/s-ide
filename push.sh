#!/bin/bash
set -e

# Initialize repo if not already initialized
git init

# Add your GitHub remote (replace with your actual URL)
git remote add origin https://github.com/USERNAME/REPO.git

# Stage everything
git add .

# Commit with a message
git commit -m "Initial commit"

# Rename default branch to main (GitHub standard)
git branch -M main

# Push to GitHub
git push -u origin main
