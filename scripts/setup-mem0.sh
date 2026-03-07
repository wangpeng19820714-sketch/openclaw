#!/bin/bash
set -e

TARGET_DIR="extensions-custom/mem0-upstream"
REPO_URL="https://github.com/mem0ai/mem0.git"

echo "Checking Mem0 upstream..."

if [ -d "$TARGET_DIR" ]; then
  echo "Directory $TARGET_DIR already exists. Pulling latest changes..."
  git -C "$TARGET_DIR" pull
else
  echo "Cloning Mem0 from $REPO_URL..."
  git clone "$REPO_URL" "$TARGET_DIR"
fi

# Cleanup examples to save space
if [ -d "$TARGET_DIR/example" ]; then
  echo "Removing example directory to save space..."
  rm -rf "$TARGET_DIR/example"
fi

echo "Mem0 setup complete."
