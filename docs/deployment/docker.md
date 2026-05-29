# Docker 部署

## 前置条件

- Docker 20.10+
- Docker Compose 2.0+

## 快速启动

```bash
cd ~/.hermes/nexus
docker-compose up -d
```

启动后服务：
| 服务 | 端口 | 说明 |
|------|------|------|
| nexus | 8080 | MCP Server + REST API |
| prometheus | 9090 | 指标采集 |
| grafana | 3000 | 监控面板 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| NEXUS_DB_PATH | /root/.hermes/data/nexus/nexus.db | 数据库路径 |
| NEXUS_LOG_LEVEL | INFO | 日志级别 |

## 数据持久化

数据通过 Docker volume 持久化：

```bash
# 查看 volumes
docker volume ls | grep nexus

# 备份数据
docker run --rm -v nexus-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/nexus-backup.tar.gz -C /data .
```

## 健康检查

```bash
curl http://localhost:8080/health
```

## 监控

```bash
# Prometheus 指标
curl http://localhost:8080/metrics?format=prometheus

# Grafana 面板
# 浏览器打开 http://localhost:3000 (admin/admin)
```

## 备份与恢复

```bash
# 备份
docker exec nexus-memory python3 -c "
from nexus.backup import NexusBackup
nb = NexusBackup('/root/.hermes/data/nexus/nexus.db')
nb.snapshot(label='manual')
"

# 恢复
docker cp backup.db nexus-memory:/root/.hermes/data/nexus/nexus.db
docker restart nexus-memory
```

## 升级

```bash
docker-compose pull
docker-compose up -d
```
