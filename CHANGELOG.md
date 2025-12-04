# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.2.0] - 2025-12-04

### Added
- Support for mapping **first name**, **last name**, and **email** from TACACS+/RADIUS attributes into NetBox user profiles.
  - Uses standard NetBox remote-auth variables interpreted as AAA attribute names:
    - `REMOTE_AUTH_USER_FIRST_NAME`
    - `REMOTE_AUTH_USER_LAST_NAME`
    - `REMOTE_AUTH_USER_EMAIL`
- Example `netboxauth_config.py` showing:
  - TACACS+ and RADIUS configuration blocks
  - Multi-server failover with `SERVERS` lists
  - Profile attribute mapping configuration

### Changed
- Refined configuration loading logic to:
  - Prefer `netbox.configuration` (NetBox Docker aggregated config)
  - Fall back to `django.conf.settings`
  - Fall back to `netboxauth_config` (bare-metal support)
- Updated README with:
  - Clear Docker vs bare-metal setup instructions
  - Multi-server failover explanation
  - Troubleshooting examples using `manage.py shell`

### Fixed
- Ensured consistent behavior between NetBox Docker and bare-metal installs when reading `netboxauth_config.py`.

---

## [1.1.0] - 2025-11-19

### Added
- Initial support for both **TACACS+** and **RADIUS** authentication.
- Multi-server failover for TACACS+ and RADIUS:
  - `NETBOX_REMOTE_AUTH_TACACS["SERVERS"]`
  - `NETBOX_REMOTE_AUTH_RADIUS["SERVERS"]`
- Group mapping logic:
  - AAA roles → NetBox group names
  - Optional `REMOTE_AUTH_DEFAULT_GROUPS`
  - Optional `REMOTE_AUTH_SUPERUSER_GROUPS` and `REMOTE_AUTH_STAFF_GROUPS`
- Compatibility with:
  - NetBox Docker (`/etc/netbox/config/`)
  - Bare-metal NetBox installs.

---

## [1.0.0] - 2025-11-12

### Added
- First public release of the NetBox remote authentication backend:
  - TACACS+/RADIUS login support
  - Basic remote-auth configuration
