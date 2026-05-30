#!/bin/bash
# Run this to add topics to your GitHub repo
# Requires: gh CLI authenticated

gh repo edit chuf-China/nexus-memory \
  --add-topic ai-agent \
  --add-topic memory \
  --add-topic persistent-memory \
  --add-topic llm \
  --add-topic mcp \
  --add-topic knowledge-graph \
  --add-topic sqlite \
  --add-topic python \
  --add-topic vector-search \
  --add-topic full-text-search \
  --add-topic ai-memory \
  --add-topic agent-framework

echo "Topics added!"
