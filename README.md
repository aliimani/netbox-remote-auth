# NetBox Remote Authentication Backend (TACACS+ / RADIUS)

This project provides a custom **remote authentication backend** for NetBox, allowing authentication directly against **TACACS+** or **RADIUS** servers such as Cisco ISE, FreeRADIUS, ACS, or any standard AAA platform.

The backend retrieves AAA attributes (roles, optional first/last name, email) and maps them into NetBox users and groups.

The backend works for both:

- **NetBox Docker**
- **Bare‑metal NetBox installations**

---

## Table of Contents

1. [How It Works](#how-it-works)  
2. [Features](#features)  
3. [Requirements](#requirements)  
4. [Installation](#installation)  
5. [netboxauth_config.py – Configuration](#netboxauth_configpy--configuration)  
6. [NetBox Docker Instructions](#netbox-docker-instructions)  
7. [Bare-Metal NetBox Instructions](#bare-metal-netbox-instructions)  
8. [AAA Server Configuration](#aaa-server-configuration)  
9. [Group Mapping Rules](#group-mapping-rules)  
10. [Troubleshooting](#troubleshooting)  
11. [Advanced Notes](#advanced-notes)

---

## How It Works

1. User enters username/password on the NetBox login page.  
2. Backend sends credentials to TACACS+ or RADIUS.  
3. AAA returns authentication status + optional attributes.  
4. Backend:
   - Creates NetBox user (if enabled)
   - Assigns groups based on AAA roles
   - Ensures user account is active
   - Sets staff/superuser based on group membership
   - Optionally assigns first name, last name, and email from AAA attributes

---

## Features

- ✔ TACACS+ support  
- ✔ RADIUS support  
- ✔ **Multi-server failover**  
  - If first server fails, second one is tried  
  - If only one server is used, remove extra entry  
- ✔ Automatic user creation  
- ✔ AAA role → NetBox group mapping  
- ✔ Optional default groups  
- ✔ Optional staff & superuser mapping  
- ✔ Works in **Docker** and **bare-metal**  
- ✔ All configuration in **one file**  
- ✔ No need to modify `configuration.py`  

---

## Requirements

Install Python dependencies:

```bash
pip install tacacs-plus pyrad typing_extensions
```

---

## Installation

Place backend files in:

```
netbox/netbox/netboxauth/
    ├── __init__.py
    └── backend.py
```

---

## `netboxauth_config.py` – Configuration

Place it in:

- **Docker:** `netbox-docker/configuration/netboxauth_config.py`  
- **Bare-metal:** `/opt/netbox/netbox/netbox/netboxauth_config.py`

A full example file is included in this repository:  
`example_netboxauth_config.py`

---

## NetBox Docker Instructions

### 1. Add config file:

```
netbox-docker/
    configuration/
        netboxauth_config.py
```

Mapped automatically to:

```
/etc/netbox/config/
```

### 2. Restart containers:

```bash
sudo docker compose restart netbox netbox-worker
```

Restart is required when adding or modifying config files.

---

## Bare-Metal NetBox Instructions

Place config file here:

```
/opt/netbox/netbox/netbox/netboxauth_config.py
```

Restart NetBox service:

```
sudo systemctl restart netbox
```

---

## AAA Server Configuration

Backend supports several role formats:

### TACACS+
- `role = netbox-admin`
- `Cisco-AVPair = shell:role="netbox-admin"`
- `priv-lvl = 15` → becomes group `tacacs-priv-15`

### RADIUS
- `role = netbox-admin`
- `Cisco-AVPair = "shell:role=netbox-admin"`
- `Class = netbox-admin`

### Optional: First/Last Name & Email Mapping

Backend uses standard NetBox remote-auth parameters:

```
REMOTE_AUTH_USER_FIRST_NAME = "givenName"
REMOTE_AUTH_USER_LAST_NAME  = "sn"
REMOTE_AUTH_USER_EMAIL      = "mail"
```

---

## Group Mapping Rules

1. Start with:

```
REMOTE_AUTH_DEFAULT_GROUPS
```

2. Add groups corresponding to AAA roles.  
3. If:

```
REMOTE_AUTH_GROUP_SYNC_ENABLED = True
```

→ replace existing groups entirely.  
4. Privilege groups:

```
REMOTE_AUTH_SUPERUSER_GROUPS
REMOTE_AUTH_STAFF_GROUPS
```

Control `is_superuser` and `is_staff`.

---

## Troubleshooting

### Enter Docker NetBox shell:

```bash
sudo docker exec -it netbox-docker-netbox-1 bash
python manage.py shell
```

### Check NetBox settings:

```python
from django.conf import settings
print(settings.REMOTE_AUTH_BACKEND)
print(settings.REMOTE_AUTH_ENABLED)
print(settings.REMOTE_AUTH_SUPERUSER_GROUPS)
```

### Check backend config:

```python
from netboxauth.backend import _cfg
print("Method:", _cfg("NETBOX_REMOTE_AUTH_METHOD"))
print("TACACS:", _cfg("NETBOX_REMOTE_AUTH_TACACS"))
print("RADIUS:", _cfg("NETBOX_REMOTE_AUTH_RADIUS"))
```

If any are `None`, config is not being loaded.

---

## Advanced Notes

### Multi-server failover

Backend tries servers in order:

```python
"SERVERS": [
    {"HOST": "10.10.10.10", "PORT": 49},
    {"HOST": "10.10.10.11", "PORT": 49},
]
```

Remove second entry if only one server exists.

### Account Deactivation

Backend does **not** disable NetBox users on failed login  
→ prevents disabling users who mistype passwords.

AAA controls access:  
If AAA rejects login → NetBox login fails.

---

## Summary

This backend provides:

- Direct TACACS+/RADIUS login  
- Reliable group + privilege mapping  
- Optional name/email syncing  
- Multi-server redundancy  
- Works seamlessly in Docker & bare-metal  
- Simple configuration in one file  

Pull requests and feature suggestions are welcome!
