# OpenCastor MAC / seccomp deployment artifacts

This directory contains deployable Linux runtime hardening profiles:

- `apparmor/opencastor-gateway` - gateway confinement profile.
- `apparmor/opencastor-driver` - stricter isolated driver confinement profile.
- `seccomp/gateway-seccomp.json` - gateway syscall allow-list policy.
- `seccomp/driver-strict-seccomp.json` - isolated driver syscall allow-list.
- `install_profiles.sh` - installer/activator for `/etc/opencastor/security`.

Install them on a target host with:

```bash
bash deploy/security/install_profiles.sh
```
