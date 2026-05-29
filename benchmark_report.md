# Nexus Benchmark Report

## 环境
- 测试时间: 2026-05-30 03:20:14
- 查询总数: 50 (15 exact + 15 semantic + 10 relational + 10 temporal)
- 种子数据: 50 条记忆

## 结果汇总

| 模式 | 准确率 | 命中/总数 | 平均延迟 | P95 延迟 |
|------|--------|-----------|----------|----------|
| exact | 66.7% | 10/15 | 575.9ms | 6919.7ms |
| semantic | 6.7% | 1/15 | 120.8ms | 130.3ms |
| relational | 0.0% | 0/10 | 130.3ms | 147.5ms |
| temporal | 20.0% | 2/10 | 153.1ms | 459.1ms |
| **整体** | **26.0%** | **13/50** | - | - |

## 各模式详细结果

### exact (66.7%)

1. ✅ `PostgreSQL 版本是多少` → 期望: `PostgreSQL 版本是 16`
2. ✅ `Redis 默认端口是什么` → 期望: `Redis 默认端口是 6379`
3. ✅ `Docker 使用什么容器运行时` → 期望: `Docker 使用 containerd`
4. ✅ `Python 最新稳定版` → 期望: `Python 最新稳定版是 3.12`
5. ✅ `Git 默认分支名` → 期望: `Git 的默认分支名是 main`
6. ✅ `SQLite 支持什么模式提高并发` → 期望: `SQLite 支持 WAL 模式`
7. ✅ `React 18 引入了什么特性` → 期望: `React 18 引入了并发渲染`
8. ✅ `TypeScript 5.0 添加了什么` → 期望: `TypeScript 5.0 添加了装饰器支持`
9. ✅ `Node.js 当前 LTS 版本` → 期望: `Node.js 20 是当前 LTS 版本`
10. ✅ `AWS 最常用区域` → 期望: `AWS us-east-1 是最常用的区域`
11. ❌ `Prometheus 用什么查询语言` → 期望: `Prometheus 使用 PromQL`
12. ❌ `Nginx 配置文件路径` → 期望: `Nginx 的配置文件路径是 /etc/nginx/nginx.conf`
13. ❌ `Kubernetes 默认命名空间` → 期望: `Kubernetes 的默认命名空间是 default`
14. ❌ `Linux 内核 6.x 新特性` → 期望: `Linux 内核版本 6.x 引入了新的调度器`
15. ❌ `GitHub Actions 用什么格式` → 期望: `GitHub Actions 使用 YAML 配置工作流`

### semantic (6.7%)

1. ✅ `数据库相关的配置` → 期望: `PostgreSQL`
2. ❌ `容器编排工具` → 期望: `Kubernetes`
3. ❌ `前端框架的版本更新` → 期望: `React 18`
4. ❌ `用户的编码习惯` → 期望: `类型标注`
5. ❌ `部署相关的决策` → 期望: `AWS us-east-1`
6. ❌ `安全相关的规则` → 期望: `密钥不能硬编码`
7. ❌ `测试框架的偏好` → 期望: `pytest`
8. ❌ `编辑器的选择` → 期望: `VS Code`
9. ❌ `监控系统的配置` → 期望: `Prometheus`
10. ❌ `版本控制的规范` → 期望: `Conventional Commits`
11. ❌ `API 设计的要求` → 期望: `健康检查`
12. ❌ `数据库迁移策略` → 期望: `支持回滚`
13. ❌ `缓存技术的使用` → 期望: `Redis`
14. ❌ `Web 框架的特点` → 期望: `FastAPI`
15. ❌ `操作系统相关` → 期望: `Linux 内核`

### relational (0.0%)

1. ❌ `Nexus 依赖了哪些技术` → 期望: `Python 3.10+`
2. ❌ `Hermes 和 Nexus 的关系` → 期望: `Hermes 集成了 Nexus`
3. ❌ `Django 支持哪些数据库` → 期望: `PostgreSQL、MySQL、SQLite`
4. ❌ `Kubernetes 运行在什么上面` → 期望: `Docker 容器`
5. ❌ `Prometheus 从哪里抓取指标` → 期望: `Nexus 的 /metrics`
6. ❌ `SQLite 的全文搜索依赖什么` → 期望: `FTS5 和 jieba`
7. ❌ `Redis 有几种持久化方式` → 期望: `RDB 和 AOF`
8. ❌ `Django ORM 映射什么` → 期望: `数据库表`
9. ❌ `FastAPI 基于什么` → 期望: `Starlette 和 Pydantic`
10. ❌ `Nginx 反向代理用什么指令` → 期望: `proxy_pass`

### temporal (20.0%)

1. ✅ `项目什么时候启动的` → 期望: `2026-01-15`
2. ✅ `上次部署是什么时候` → 期望: `2026-05-28`
3. ❌ `价格什么时候调整的` → 期望: `2026-04-15`
4. ❌ `数据库迁移 v2 什么时候完成` → 期望: `2026-05-20`
5. ❌ `周会是什么时候` → 期望: `每周三下午 3 点`
6. ❌ `React 18 什么时候发布` → 期望: `React 18`
7. ❌ `Python 3.12 什么时候发布` → 期望: `2023-10-02`
8. ❌ `最新的数据库迁移` → 期望: `v2 完成于 2026-05-20`
9. ❌ `最近的价格变化` → 期望: `从 $500 调整为 $450`
10. ❌ `项目经历了哪些部署` → 期望: `上次部署是 2026-05-28`

## 结论

- 融合检索整体准确率: 26.0%
- 精确查询: FTS5 贡献最大
- 语义查询: 向量搜索贡献最大
- 关系查询: 图谱遍历贡献最大
- 时序查询: 时间过滤 + FTS5 贡献最大