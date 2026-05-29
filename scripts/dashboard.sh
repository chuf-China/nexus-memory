#!/bin/bash
# nexus_dashboard.sh — 启动Nexus Dashboard

cd ~/.hermes/plugins/nexus-dashboard
python3 server.py "${1:-8080}"
