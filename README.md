# NetBox Remote Authentication Backend (TACACS+ / RADIUS)

This project provides a custom **remote authentication backend** for NetBox that authenticates users directly against **TACACS+** or **RADIUS** servers (Cisco ISE, FreeRADIUS, ACS, NPS, etc.).

Unlike NetBox’s built‑in `RemoteUserBackend`, this backend communicates **directly** with your AAA server, receives authorization attributes, and maps them to NetBox user groups automatically — **no reverse proxy**, **no HTTP headers**, **no SSO** required.

---

## Table of Contents

1. Overview & How It Works  
2. Key Features  
3. Requirements  
4. Installation  
   - NetBox Docker Installation  
   - Bare-Metal Installation  
5. Configuration File: `netboxauth_config.py`  
6. NetBox Docker Usage  
7. Bare-Metal NetBox Usage  
8. AAA Server Configuration  
9. Group Mapping Behaviour  
10. Troubleshooting  
11. Advanced Notes  

---

## Overview & How It Works

1. User enters username & password into NetBox login page.  
2. NetBox invokes this backend instead of the default RemoteUser backend.  
3. Credentials are sent to TACACS+ or RADIUS.  
4. AAA validates the credentials and returns attributes/roles.  
5. Backend:
   - Creates/updates NetBox local users,
   - Assigns NetBox groups based on AAA roles,
   - Applies staff/superuser flags,
   - Ensures `is_active = True`,
   - Optionally updates first name, last name, and email.

AAA fully controls whether the user is granted access.

---

## Key Features

- ✔ TACACS+ and RADIUS authentication  
- ✔ Multi-server failover (try servers in order)  
- ✔ Automatic user creation  
- ✔ Automatic group creation based on AAA roles  
- ✔ Optional name/email attribute sync  
- ✔ Works with NetBox Docker and bare-metal  
- ✔ No configuration changes required in `configuration.py`  
- ✔ All settings live in one file: `netboxauth_config.py`  

---

## Requirements

Install required Python packages:

```bash
pip install tacacs-plus pyrad typing_extensions
```

---

# Installation

The backend package must be installed into the Python environment where NetBox runs.

---

# NetBox Docker Installation

NetBox Docker does **not** provide an extensions folder by default.  
To install this backend, follow these steps.

---

## 1. Clone the repository on your **host** system

```bash
git clone https://github.com/aliimani/netbox-remote-auth.git
```

Replace the repository URL with yours.

---

## 2. Copy the package into the NetBox container

```bash
CID=$(sudo docker compose ps -q netbox)
sudo docker cp netbox-remote-auth "$CID":/tmp/netbox-remote-auth
```

This copies your backend to:

```
/tmp/netbox-remote-auth
```

inside the container.

---

## 3. Install the package in the container using UV

```bash
sudo docker exec -it -u root -w /tmp/netbox-remote-auth "$CID" uv pip install .
```

This installs your backend as a Python module in NetBox’s environment.

---

## 4. Restart NetBox services

```bash
sudo docker compose restart netbox netbox-worker
```

---

# Bare-Metal Installation

### 1. Clone the repository

```bash
git clone https://github.com/aliimani/netbox-remote-auth.git
```

---

### 2. Install the package inside NetBox virtual environment

```bash
cd netbox-remote-auth
source /opt/netbox/venv/bin/activate
pip install .
deactivate
```

The backend will be installed automatically at:

```
/opt/netbox/venv/lib/python3.x/site-packages/netboxauth/
```

---

### 3. Restart NetBox

```bash
sudo systemctl restart netbox netbox-rq
```

---

# Configuration File: `netboxauth_config.py`

All backend configuration lives in this file.  
No edits to `configuration.py` are required.

📄 **Example file:**  
👉 [netboxauth_config_example.py](https://github.com/aliimani/netbox-remote-auth/blob/main/config/netboxauth_config_example.py)

Replace this link with your real repo location.

---

# Example `netboxauth_config.py`

```python
# NetBox Remote Auth Configuration (TACACS+ / RADIUS)

REMOTE_AUTH_ENABLED = True
REMOTE_AUTH_BACKEND = "netboxauth.backend.NetBoxRemoteAuthBackend"

REMOTE_AUTH_AUTO_CREATE_USER = True
REMOTE_AUTH_DEFAULT_GROUPS = ["netbox-staff"]
REMOTE_AUTH_GROUP_SYNC_ENABLED = True

REMOTE_AUTH_SUPERUSER_GROUPS = ["netbox-admin"]
REMOTE_AUTH_STAFF_GROUPS = ["netbox-staff"]

NETBOX_REMOTE_AUTH_METHOD = "tacacs"  # or "radius"

# -------------------------------------------------------
# TACACS+ CONFIGURATION (Enable only if using TACACS)
# The backend will try each server in order. If the first fails (connection/timeouts),
# it will try the next one.
# -------------------------------------------------------
#
# NETBOX_REMOTE_AUTH_TACACS = {
#     "SERVERS": [
#         {"HOST": "10.10.10.10", "PORT": 49},
#         {"HOST": "10.10.10.11", "PORT": 49},  # Optional second server, if you only have one TACACS server, remove the second entry.
#     ],
#     "SECRET": "SecretKey",
#     "TIMEOUT": 5,
# }

# -------------------------------------------------------
# RADIUS CONFIGURATION (Enable only if using RADIUS)
# The backend will try each server in order. If the first fails (connection/timeouts),
# it will try the next one.
# -------------------------------------------------------
#
# NETBOX_REMOTE_AUTH_RADIUS = {
#     "SERVERS": [
#         {"HOST": "10.10.20.10", "PORT": 1812},
#         {"HOST": "10.10.20.11", "PORT": 1812}, # Optional second server, if you only have one RADIUS server, remove the second entry.
#     ],
#     "SECRET": "SecretKey",
#     "TIMEOUT": 5,
#     # "NAS_IDENTIFIER": "netbox",   # Optional NAS-Identifier override used in RADIUS requests
# }

# Optional attribute mapping
REMOTE_AUTH_USER_FIRST_NAME = "givenName"
REMOTE_AUTH_USER_LAST_NAME  = "sn"
REMOTE_AUTH_USER_EMAIL      = "mail"
```

---

# NetBox Docker Usage

Place file here:

```
netbox-docker/configuration/netboxauth_config.py
```

This becomes inside the container:

```
/etc/netbox/config/netboxauth_config.py
```

Restart containers:

```bash
sudo docker compose restart netbox netbox-worker
```

---

# Bare-Metal NetBox Usage

Put file here:

```
/opt/netbox/netbox/netbox/netboxauth_config.py
```

Restart:

```bash
sudo systemctl restart netbox netbox-rq
```

---

# TACACS+ role attributes (supports multiple groups)

The backend can receive one or more roles and will map **each role to a NetBox group**.

Supported patterns:
- `role = netbox-admin` (can be returned multiple times)
- `Cisco-AVPair = shell:role="netbox-admin"` (can be returned multiple times)
- `priv-lvl = 15` → maps to the pseudo-role/group `tacacs-priv-15`

✅ Multiple groups example (TACACS+):
- `role = netbox-admin`
- `role = netbox-ipam-admin`
User is assigned to **both** NetBox groups: `netbox-admin`, `netbox-ipam-admin`

---

# RADIUS role attributes (supports multiple groups)

The backend can receive one or more roles and will map **each role to a NetBox group**.

Supported patterns:
- `role = netbox-admin` (can be returned multiple times)
- `Cisco-AVPair = shell:role="netbox-admin"` (can be returned multiple times)
- `Class = netbox-admin` (**recommended**; can be returned multiple times)

✅ Multiple groups example (RADIUS / Cisco ISE):
- `Class = netbox-admin`
- `Class = netbox-staff`
User is assigned to **both** NetBox groups: `netbox-admin`, `netbox-staff`

⚠️ Cisco ISE note:
Cisco ISE may also inject an internal session value like:
- `Class = CACS:<session-id>`

The backend should **ignore** these `CACS:` values to prevent NetBox from creating unwanted groups.

**Each AAA role becomes a NetBox group name.**

---

# Group Mapping Behaviour

1. Add default groups  
2. Add AAA role-based groups  
3. If sync enabled → clear old groups  
4. Apply staff/superuser group mapping  

---

# Troubleshooting

The recommended flow is:

1. **First check what NetBox sees in `django.conf.settings`**  
2. **Then check what the backend reads via `_cfg()`**, which merges `netbox.configuration`, `settings`, and `netboxauth_config.py`.

---

## Docker: Check configuration step by step

### 1. Enter the NetBox container and run `manage.py shell`

```bash
sudo docker exec -it netbox-docker-netbox-1 bash
cd /opt/netbox/netbox
python manage.py shell
```

### 2. First, check the general NetBox settings

```python
from django.conf import settings

print("REMOTE_AUTH_BACKEND:", settings.REMOTE_AUTH_BACKEND)
print("REMOTE_AUTH_ENABLED:", settings.REMOTE_AUTH_ENABLED)
print("REMOTE_AUTH_SUPERUSER_GROUPS:", getattr(settings, "REMOTE_AUTH_SUPERUSER_GROUPS", None))
print("REMOTE_AUTH_STAFF_GROUPS:", getattr(settings, "REMOTE_AUTH_STAFF_GROUPS", None))
```

If these values are not what you expect, the issue is in your NetBox/Docker config (e.g. wrong config file, bad mount).

### 3. Then, check what the backend reads via `_cfg()`

```python
from netboxauth.backend import _cfg

print("NETBOX_REMOTE_AUTH_METHOD:", _cfg("NETBOX_REMOTE_AUTH_METHOD"))
print("TACACS config:", _cfg("NETBOX_REMOTE_AUTH_TACACS"))
print("RADIUS config:", _cfg("NETBOX_REMOTE_AUTH_RADIUS"))
print("REMOTE_AUTH_USER_FIRST_NAME:", _cfg("REMOTE_AUTH_USER_FIRST_NAME"))
print("REMOTE_AUTH_USER_LAST_NAME:", _cfg("REMOTE_AUTH_USER_LAST_NAME"))
print("REMOTE_AUTH_USER_EMAIL:", _cfg("REMOTE_AUTH_USER_EMAIL"))
```

If `_cfg(...)` returns `None` or `{}`:

- Confirm `netboxauth_config.py` exists inside the container in `/etc/netbox/config/`.  
- Check for syntax errors in the file.  
- Ensure you restarted the NetBox containers after creating or editing the file.

---

## Bare-Metal: Check configuration step by step

On the NetBox host:

```bash
cd /opt/netbox/netbox
python manage.py shell
```

### 1. First, inspect `settings`

```python
from django.conf import settings

print("REMOTE_AUTH_BACKEND:", settings.REMOTE_AUTH_BACKEND)
print("REMOTE_AUTH_ENABLED:", settings.REMOTE_AUTH_ENABLED)
print("REMOTE_AUTH_SUPERUSER_GROUPS:", getattr(settings, "REMOTE_AUTH_SUPERUSER_GROUPS", None))
print("REMOTE_AUTH_STAFF_GROUPS:", getattr(settings, "REMOTE_AUTH_STAFF_GROUPS", None))
```

### 2. Then, inspect `_cfg()` values

```python
from netboxauth.backend import _cfg

print("NETBOX_REMOTE_AUTH_METHOD:", _cfg("NETBOX_REMOTE_AUTH_METHOD"))
print("TACACS config:", _cfg("NETBOX_REMOTE_AUTH_TACACS"))
print("RADIUS config:", _cfg("NETBOX_REMOTE_AUTH_RADIUS"))
print("REMOTE_AUTH_USER_FIRST_NAME:", _cfg("REMOTE_AUTH_USER_FIRST_NAME"))
print("REMOTE_AUTH_USER_LAST_NAME:", _cfg("REMOTE_AUTH_USER_LAST_NAME"))
print("REMOTE_AUTH_USER_EMAIL:", _cfg("REMOTE_AUTH_USER_EMAIL"))
```

If `settings` looks correct but `_cfg()` does not:

- Check the location of `netboxauth_config.py`
- Ensure NetBox has been restarted
- Confirm there are no import errors (check NetBox logs)

If `_cfg()` looks correct but users still cannot authenticate:

- Check TACACS+/RADIUS secrets, ports, and reachability  
- Check AAA server policies/logs (Cisco ISE, FreeRADIUS, NPS)  

---

# Advanced Notes

## Multi-server failover

The backend tries all servers listed in `"SERVERS"` in order.  
If the first is down or unreachable, it logs a warning and tries the next.

## Access removal

If you remove a user or revoke their NetBox access in the AAA policy:

- AAA denies authentication  
- Backend returns `None`  
- NetBox login fails even if the local user object still exists  

---

# Summary

- Direct TACACS+/RADIUS login  
- Multi-server failover  
- Automatic user/group management  
- Optional AAA → NetBox attribute sync  
- Works for both NetBox Docker and bare-metal  
- All settings in one file  

Pull requests and feature suggestions welcome!

