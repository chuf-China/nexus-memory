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
- Email to: chuf@localhost
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

## Threat Detection

Nexus Memory includes built-in threat detection:

- **Prompt Injection**: 15+ patterns detected
- **Data Exfiltration**: 10+ patterns detected
- **Anti-Forensics**: 5+ patterns detected

Total: 30+ threat patterns with 3-tier scope control (all/context/strict)

## Credits

We appreciate responsible disclosure and will credit reporters (with permission).
