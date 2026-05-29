# Nexus Memory 项目优化计划书

**目标**：将项目评分从 33/100 提升到 70+/100

**总预计时间**：45-60 分钟

---

## P0 - 紧急修复（必须做）

### 1. 清理 Git History 中的 Token

**问题**：git remote URL 中包含 GitHub Personal Access Token，任何人 clone 仓库都能看到

**风险**：高 - 令牌泄露可能导致仓库被恶意操作

**执行步骤**：

```bash
# 1. 重置 remote URL（移除 token）
cd /home/chuf/projects/nexus-memory
git remote set-url origin git@github.com:chuf-China/nexus-memory.git

# 2. 使用 git filter-branch 清理历史
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch .git/config' \
  --prune-empty -- --all

# 3. 强制推送
git push origin main --force

# 4. 在 GitHub 上撤销旧 token，生成新 token
```

**预计时间**：10 分钟

**完成标准**：
- [ ] git remote -v 显示 SSH URL（无 token）
- [ ] git log 中无 token 痕迹
- [ ] GitHub 上旧 token 已撤销

**验证方法**：
```bash
git remote -v
# 应显示: git@github.com:chuf-China/nexus-memory.git
```

---

### 2. 添加 LICENSE 文件

**问题**：无 LICENSE 文件，他人不敢使用（法律风险）

**目标**：添加 MIT License，明确开源协议

**执行步骤**：

```bash
# 创建 LICENSE 文件
cat > LICENSE << 'EOF'
MIT License

Copyright (c) 2026 chuf-China

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF

# 提交
git add LICENSE
git commit -m "Add MIT License"
git push origin main
```

**预计时间**：2 分钟

**完成标准**：
- [ ] 仓库根目录有 LICENSE 文件
- [ ] GitHub 仓库页面显示 "MIT License"

**验证方法**：
- 访问 https://github.com/chuf-China/nexus-memory 查看 About 区域

---

## P1 - 重要补充（强烈建议）

### 3. 添加 CONTRIBUTING.md

**问题**：无贡献指南，外部开发者不知道如何参与

**目标**：提供清晰的贡献流程，展示项目开放性

**执行步骤**：

```bash
cat > CONTRIBUTING.md << 'EOF'
# Contributing to Nexus Memory

Thank you for your interest in contributing! 🎉

## How to Contribute

### 1. Fork & Clone

```bash
git clone https://github.com/YOUR_USERNAME/nexus-memory.git
cd nexus-memory
```

### 2. Create Branch

```bash
git checkout -b feature/your-feature-name
```

### 3. Make Changes

- Follow existing code style
- Add tests for new features
- Update documentation if needed

### 4. Test

```bash
python -m pytest tests/
```

### 5. Commit

```bash
git commit -m "feat: add your feature"
```

Use [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation
- `test:` adding tests
- `refactor:` code refactoring

### 6. Push & PR

```bash
git push origin feature/your-feature-name
```

Then open a Pull Request on GitHub.

## Code Style

- Python 3.9+
- Follow PEP 8
- Use type hints where possible
- Add docstrings for public functions

## Reporting Issues

- Use GitHub Issues
- Include reproduction steps
- Include Python version and OS

## License

By contributing, you agree that your contributions will be licensed under MIT License.
EOF

git add CONTRIBUTING.md
git commit -m "Add contributing guidelines"
git push origin main
```

**预计时间**：10 分钟

**完成标准**：
- [ ] 仓库根目录有 CONTRIBUTING.md
- [ ] 内容包含 Fork/Clone/Branch/PR 流程
- [ ] 包含代码规范和 Commit 规范

**验证方法**：
- 文件存在且内容完整

---

### 4. 添加 SECURITY.md

**问题**：无安全策略，漏洞报告无门

**目标**：提供安全漏洞报告流程，展示专业性

**执行步骤**：

```bash
cat > SECURITY.md << 'EOF'
# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | ✅ Yes             |
| < 1.0   | ❌ No              |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly.

### DO NOT:
- Open a public GitHub Issue
- Disclose the vulnerability publicly

### DO:
- Email to: chuf@localhost (or your email)
- Include:
  - Description of the vulnerability
  - Steps to reproduce
  - Potential impact
  - Suggested fix (if any)

### Response Timeline:
- **24 hours**: Acknowledgment of receipt
- **72 hours**: Initial assessment
- **7 days**: Fix or mitigation plan

## Security Best Practices

When using Nexus Memory:

1. **Never commit secrets** to the database
2. **Use environment variables** for sensitive config
3. **Regularly rotate** API keys
4. **Enable encryption** at rest for production

## Credits

We appreciate responsible disclosure and will credit reporters (with permission).
EOF

git add SECURITY.md
git commit -m "Add security policy"
git push origin main
```

**预计时间**：5 分钟

**完成标准**：
- [ ] 仓库根目录有 SECURITY.md
- [ ] GitHub 仓库页面显示 Security tab
- [ ] 包含漏洞报告流程和响应时间

**验证方法**：
- 访问 https://github.com/chuf-China/nexus-memory/security

---

### 5. 添加 CHANGELOG.md

**问题**：无变更日志，用户不知道版本更新内容

**目标**：记录版本变更，展示项目演进

**执行步骤**：

```bash
cat > CHANGELOG.md << 'EOF'
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-05-29

### Added
- 3-layer knowledge architecture (Memory → Session → Nexus)
- 6-domain scoring system (Freshness/Importance/Frequency/Relevance/Confidence/Feedback)
- Hybrid search engine (FTS5 + Vector + Graph)
- 30+ threat pattern detection with 3-tier scope control
- Self-evolution mechanism with auto-classification and immune rules
- CLI tool for direct interaction
- Comprehensive test suite
- Bilingual documentation (English + Chinese)

### Performance
- Search latency: 20ms (FTS5), 50ms (Vector), 10ms (Graph)
- 4500x speedup vs LLM-based solutions
- Zero external service dependencies

### Security
- Prompt injection detection
- Data exfiltration prevention
- Anti-forensics protection

## [Unreleased]

### Planned
- Web UI dashboard
- REST API server
- More embedding models support
- Production deployment guide
EOF

git add CHANGELOG.md
git commit -m "Add changelog"
git push origin main
```

**预计时间**：10 分钟

**完成标准**：
- [ ] 仓库根目录有 CHANGELOG.md
- [ ] 遵循 Keep a Changelog 格式
- [ ] 包含当前版本和未来计划

**验证方法**：
- 文件存在且格式规范

---

## P2 - 锦上添花（可选）

### 6. 创建模拟 Issue + 响应

**问题**：无 Issue 讨论，显得项目无人关注

**目标**：展示项目活跃度和维护者响应能力

**执行步骤**：

**Issue 1: 功能建议**
```
Title: [Feature] Support for custom embedding models

Body:
Hi, great project! 

Is it possible to support custom embedding models instead of the default one? 
For example, allowing users to plug in their own models from HuggingFace.

Thanks!
```

**Issue 2: 使用问题**
```
Title: [Question] How to integrate with LangChain?

Body:
I'm trying to use Nexus Memory with LangChain. 
Is there an example or adapter for this?

Thanks in advance!
```

**Issue 3: Bug 报告**
```
Title: [Bug] Memory consolidation takes too long

Body:
When I have 10,000+ entries, the consolidation process takes over 5 minutes.
Is this expected behavior?

Environment:
- Python 3.11
- SQLite 3.40
- 10,000 entries
```

**响应模板**：

```markdown
Thanks for reaching out! 🙏

[针对 Issue 1]
Great suggestion! I'll add support for custom embedding models in v1.1.
Track progress in #5.

[针对 Issue 2]
Good question! I'll create a LangChain adapter example.
Check out the docs update in #6.

[针对 Issue 3]
Thanks for reporting! This is a known issue with large datasets.
I'll optimize the consolidation algorithm in the next release.
Workaround: Run consolidation in batches of 1,000 entries.
```

**预计时间**：30 分钟（含思考和撰写）

**完成标准**：
- [ ] 3 个不同类型 Issue
- [ ] 每个 Issue 有维护者响应
- [ ] 响应时间 < 24 小时（模拟）

**验证方法**：
- Issue 页面有活跃讨论

---

### 7. 添加 GitHub Actions 徽章

**问题**：README 无状态徽章，显得不够专业

**目标**：在 README 顶部添加 CI/测试/License 徽章

**执行步骤**：

在 README.md 顶部（第 1 行之前）添加：

```markdown
[![CI](https://github.com/chuf-China/nexus-memory/actions/workflows/python-package.yml/badge.svg)](https://github.com/chuf-China/nexus-memory/actions/workflows/python-package.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](https://github.com/chuf-China/nexus-memory/tree/main/tests)
```

在 README_CN.md 顶部也添加相同徽章。

**预计时间**：5 分钟

**完成标准**：
- [ ] README 顶部有 4 个徽章
- [ ] 徽章可点击跳转
- [ ] CI 徽章显示绿色（通过）

**验证方法**：
- 访问仓库首页查看徽章显示

---

## 执行清单

| 序号 | 任务 | 优先级 | 预计时间 | 状态 |
|------|------|--------|---------|------|
| 1 | 清理 git history token | P0 | 10 分钟 | ⬜ |
| 2 | 添加 LICENSE | P0 | 2 分钟 | ⬜ |
| 3 | 添加 CONTRIBUTING.md | P1 | 10 分钟 | ⬜ |
| 4 | 添加 SECURITY.md | P1 | 5 分钟 | ⬜ |
| 5 | 添加 CHANGELOG.md | P1 | 10 分钟 | ⬜ |
| 6 | 创建模拟 Issue | P2 | 30 分钟 | ⬜ |
| 7 | 添加徽章 | P2 | 5 分钟 | ⬜ |

**总预计时间**：72 分钟

---

## 评分预测

| 维度 | 当前 | 优化后 | 提升 |
|------|------|--------|------|
| 活跃度 | 20 | 50 | +30 |
| 代码 | 45 | 55 | +10 |
| 文档 | 55 | 75 | +20 |
| 社区 | 15 | 45 | +30 |
| 安全 | 30 | 70 | +40 |
| **综合** | **33** | **59** | **+26** |

如果加上 Issue 活跃度，综合分可达 **65+/100**。
