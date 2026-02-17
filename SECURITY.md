# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2026.x  | Yes       |
| < 2026  | No        |

## Reporting a Vulnerability

If you discover a security vulnerability in OpenCastor, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

### How to Report

1. **Email**: Send details to **security@opencastor.com**
2. **GitHub**: Use the [Security Advisories](https://github.com/craigm26/OpenCastor/security/advisories/new) feature (private)

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact (e.g., motor safety, credential exposure, unauthorized access)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Assessment**: Within 7 days
- **Fix or mitigation**: Within 30 days for critical issues

### Scope

The following are in scope for security reports:

- **Motor safety**: Bypassing speed limits, safety clamps, or emergency stop
- **Credential exposure**: API keys, tokens, or secrets leaked via logs, config export, or network
- **Unauthorized access**: Bypassing API authentication or RBAC controls
- **Injection**: Command injection via config files, channel messages, or API inputs
- **Privilege escalation**: Gaining elevated permissions in the virtual filesystem or RCAN RBAC

### Out of Scope

- Issues requiring physical access to the robot hardware
- Denial of service against locally-running instances
- Social engineering attacks against project maintainers

## Security Architecture

OpenCastor implements multiple defense layers:

- **API Authentication**: Optional bearer token (`OPENCASTOR_API_TOKEN`) and JWT-based RCAN auth
- **RBAC**: Role-based access control (Guest/User/Operator/Admin/Creator) for the virtual filesystem
- **Safety Clamping**: Driver layer enforces physical limits regardless of AI output
- **Emergency Stop**: Hardware-level stop accessible via API, dashboard, CLI, and messaging channels
- **Approval Gate**: Dangerous motor commands can require human approval before execution
- **Geofence**: Configurable operating radius limit with automatic stop
- **Watchdog**: Auto-stops motors if the AI brain becomes unresponsive
- **Audit Log**: Append-only record of all motor commands, approvals, and config changes
- **Privacy Policy**: Default-deny for camera streaming, audio recording, and location sharing
- **Secrets Management**: API keys stored in `.env` (gitignored), never in config files
