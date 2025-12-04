# NetBox Remote Authentication Backend (TACACS+ / RADIUS)

This project provides a custom **remote authentication backend** for NetBox that allows you to authenticate users directly against **TACACS+** or **RADIUS** servers (Cisco ISE, FreeRADIUS, ACS, NPS, etc.).

Unlike NetBox’s built-in `RemoteUserBackend`, this backend talks directly to your AAA server, receives attributes and roles, and maps them to NetBox groups automatically.

No reverse proxy, no HTTP headers, no SSO – just direct TACACS+/RADIUS.

---

## Table of Contents

1. [Overview & How It Works](#overview--how-it-works)  
2. [Key Features](#key-features)  
3. [Requirements](#requirements)  
4. [Installation](#installation)  
5. [Configuration File: `netboxauth_config.py`](#configuration-file-netboxauth_configpy)  
6. [NetBox Docker Usage](#netbox-docker-usage)  
7. [Bare-Metal NetBox Usage](#bare-metal-netbox-usage)  
8. [AAA Server Configuration](#aaa-server-configuration)  
9. [Group Mapping Behaviour](#group-mapping-behaviour)  
10. [Troubleshooting](#troubleshooting)  
11. [Advanced Notes](#advanced-notes)  

---

## Overview & How It Works

1. A user enters their username & password on the NetBox login page.  
2. NetBox invokes this backend instead of the default one.  
3. The backend sends the credentials to your TACACS+ or RADIUS server.  
4. AAA validates the credentials and returns authorization attributes (e.g. roles).  
5. The backend:
   - Creates the NetBox user (if enabled),
   - Assigns NetBox groups based on AAA roles,
   - Sets `is_staff` / `is_superuser` based on group membership,
   - Ensures the account is active (`is_active = True`),
   - Optionally updates first name, last name, and email from AAA attributes.

The decision **“can this user log in?”** is made entirely by your TACACS+/RADIUS server.

---

## Key Features

- ✅ TACACS+ support  
- ✅ RADIUS support  
- ✅ Optional multi-server configuration (failover):
  - You can configure multiple TACACS+/RADIUS servers.
  - If the first server is unreachable or errors out, the backend will try the next.
  - If you only have one server, simply remove the second entry from the config.
- ✅ Direct AAA integration (no reverse proxy, no headers, no SSO needed)  
- ✅ Automatic NetBox group creation from AAA roles  
- ✅ Optional default groups for all remote users  
- ✅ Optional superuser / staff mapping based on groups  
- ✅ Works with **NetBox Docker** and **bare-metal NetBox installations**  
- ✅ All configuration contained in a single file: `netboxauth_config.py`  
- ✅ You **do not need to modify** `configuration.py`  

---

## Requirements

Install the following Python packages in the same environment as NetBox:

```bash
pip install tacacs-plus pyrad typing_extensions
```

For NetBox Docker, install these into your custom NetBox image or use a `requirements.txt` override.

---

## Installation

Place the backend code into:

```text
netbox/netbox/netboxauth/
    ├── __init__.py
    └── backend.py
```

The `backend.py` in this repository is written to work with:

- netbox-docker (using `/etc/netbox/config/`), and  
- bare-metal NetBox installs.

---

## Configuration File: `netboxauth_config.py`

All configuration for this backend is stored in **one file**: `netboxauth_config.py`.

This repository includes a sample `netboxauth_config.py`.  
You should copy it, adjust values to your environment, and place it in the appropriate directory (see Docker vs bare-metal sections below).

> **Note**: Because all required remote-auth settings are in `netboxauth_config.py`, you do *not* need to make changes in `configuration.py`.

### Example `netboxauth_config.py`

```python
#
# NetBox Remote Auth Configuration (TACACS+ / RADIUS)
#

# Enable remote authentication and point NetBox to this backend
REMOTE_AUTH_ENABLED = True
REMOTE_AUTH_BACKEND = "netboxauth.backend.NetBoxRemoteAuthBackend"

# Automatically create local NetBox users after successful remote auth
REMOTE_AUTH_AUTO_CREATE_USER = True

# Default groups assigned to every remote user (optional)
REMOTE_AUTH_DEFAULT_GROUPS = ["netbox-staff"]

# If enabled, groups in NetBox will be kept in sync with roles from AAA
REMOTE_AUTH_GROUP_SYNC_ENABLED = True

# Groups that grant superuser / staff flags
REMOTE_AUTH_SUPERUSER_GROUPS = ["netbox-admin"]
REMOTE_AUTH_STAFF_GROUPS = ["netbox-staff"]

# Choose netboxauth remote method: "tacacs" or "radius"
NETBOX_REMOTE_AUTH_METHOD = "tacacs"  # or "radius"

# TACACS+ server configuration (only if using TACACS)
NETBOX_REMOTE_AUTH_TACACS = {
    # You can configure one or more TACACS servers.
    # The backend will try each server in order. If the first fails (connection/timeouts),
    # it will try the next one.
    "SERVERS": [
        {"HOST": "10.10.10.10", "PORT": 49},
        {"HOST": "10.10.10.11", "PORT": 49},
    ],
    # If you only have one TACACS server, remove the second entry.
    "SECRET": "SuperSecretKey",
    "TIMEOUT": 5,
}

# RADIUS server configuration (only if using RADIUS)
NETBOX_REMOTE_AUTH_RADIUS = {
    # You can configure one or more RADIUS servers.
    # The backend will try each server in order. If the first fails (connection/timeouts),
    # it will try the next one.
    "SERVERS": [
        {"HOST": "10.10.20.10", "PORT": 1812},
        {"HOST": "10.10.20.11", "PORT": 1812},
    ],
    # If you only have one RADIUS server, remove the second entry.
    "SECRET": "SuperSecretKey",
    "TIMEOUT": 5,
    # Optional NAS-Identifier override used in RADIUS requests
    # "NAS_IDENTIFIER": "netbox",
}

# Optional: map AAA attributes to user profile fields
# These are attribute names as they appear in TACACS+/RADIUS replies.
NETBOX_REMOTE_AUTH_FIRST_NAME_ATTR = None  # e.g. "givenName"
NETBOX_REMOTE_AUTH_LAST_NAME_ATTR = None   # e.g. "sn"
NETBOX_REMOTE_AUTH_EMAIL_ATTR = None       # e.g. "mail"
```

You can add additional remote-auth-related options based on the official NetBox documentation if needed.

---

## NetBox Docker Usage

### 1. Place `netboxauth_config.py` in the `configuration/` directory

In your `netbox-docker` repository structure:

```text
netbox-docker/
    configuration/
        netboxauth_config.py   ← add this file here
```

The `configuration/` directory is mounted into the NetBox container as:

```text
/etc/netbox/config/
```

NetBox automatically loads all `.py` files from that directory, including `netboxauth_config.py`.

### 2. Restart the NetBox containers after adding or changing the file

To apply your configuration:

```bash
cd /path/to/netbox-docker
sudo docker compose restart netbox netbox-worker
```

In many cases, NetBox will pick up the config when the container is recreated, but restarting is the safe way to ensure changes are loaded (especially after adding a new file).

---

## Bare-Metal NetBox Usage

For a local NetBox installation (no Docker), you typically have:

```text
/opt/netbox/netbox/netbox/configuration.py
```

Place your `netboxauth_config.py` alongside `configuration.py`, for example:

```text
/opt/netbox/netbox/netbox/configuration.py
/opt/netbox/netbox/netbox/netboxauth_config.py   ← add this file
```

As long as this directory is on `PYTHONPATH` (standard install), the backend’s `_cfg()` helper will see these values, and no changes to `configuration.py` are required.

After creating or editing the file, restart NetBox (e.g. `systemctl restart netbox` or your service manager) so the code is reloaded.

---

## AAA Server Configuration

Your AAA server (Cisco ISE, FreeRADIUS, etc.) must send a **role-like attribute** that the backend will use as the source of NetBox group names.

Supported patterns:

### TACACS+

- `role = netbox-admin`
- `Cisco-AVPair = shell:role="netbox-admin"`
- `priv-lvl = 15` (converted to pseudo-role `tacacs-priv-15`)

### RADIUS

- `role = netbox-admin`
- `Cisco-AVPair = "shell:role=netbox-admin"`
- `Class = netbox-admin`

Each role value becomes a **NetBox group name**.  
If multiple roles are returned, the user will be added to multiple NetBox groups.

#### Example (Cisco ISE TACACS+)

In Cisco ISE, under a TACACS Profile’s **Custom Attributes**:

```text
shell:role="netbox-admin"
```

Assign this profile in your TACACS Authorization Policy rule for NetBox administrators.

#### Example (Cisco ISE RADIUS)

In a RADIUS Authorization Profile:

```text
Class = netbox-admin
```

or

```text
Cisco-AVPair = "shell:role=netbox-admin"
```

#### Example (FreeRADIUS)

```text
Class := "netbox-admin"
```

---

## Group Mapping Behaviour

The backend’s group logic is intentionally simple and predictable:

1. Start with `REMOTE_AUTH_DEFAULT_GROUPS` from `netboxauth_config.py`.
2. For each remote role returned by TACACS+/RADIUS:
   - Ensure there is a NetBox group with the same name,
   - Add the user to that group.
3. If `REMOTE_AUTH_GROUP_SYNC_ENABLED = True`:
   - Existing groups for the user are cleared first,
   - Then the default + remote-based groups are applied.

Superuser / staff flags are controlled by:

```python
REMOTE_AUTH_SUPERUSER_GROUPS = ["netbox-admin"]
REMOTE_AUTH_STAFF_GROUPS = ["netbox-staff"]
```

- If user belongs to any group in `REMOTE_AUTH_SUPERUSER_GROUPS`:
  - `is_superuser = True`, `is_staff = True`.
- If user belongs to any group in `REMOTE_AUTH_STAFF_GROUPS`:
  - `is_staff = True`.

This allows you to drive fine-grained permissions from the AAA side.

---

## Troubleshooting

### 1. Check configuration inside NetBox Docker

Enter the NetBox container:

```bash
sudo docker exec -it netbox-docker-netbox-1 bash
```

Then run:

```bash
cd /opt/netbox/netbox
python manage.py shell
```

In the Python shell:

```python
from django.conf import settings
print(settings.REMOTE_AUTH_BACKEND)
print(settings.REMOTE_AUTH_ENABLED)
print(settings.REMOTE_AUTH_SUPERUSER_GROUPS)

from netboxauth.backend import _cfg
print("NETBOX_REMOTE_AUTH_METHOD via _cfg:", _cfg("NETBOX_REMOTE_AUTH_METHOD"))
print("TACACS config via _cfg:", _cfg("NETBOX_REMOTE_AUTH_TACACS"))
print("RADIUS config via _cfg:", _cfg("NETBOX_REMOTE_AUTH_RADIUS"))
```

You should see your configured values printed. If `None` appears where you expect data, check:

- Is `netboxauth_config.py` in the correct directory?
- Did you restart the NetBox container after adding the file?
- Are there any syntax errors in `netboxauth_config.py`?

---

### 2. Check configuration on bare-metal NetBox

On the NetBox host:

```bash
cd /opt/netbox/netbox
python manage.py shell
```

Then:

```python
from django.conf import settings
print(settings.REMOTE_AUTH_BACKEND)
print(settings.REMOTE_AUTH_ENABLED)
print(settings.REMOTE_AUTH_SUPERUSER_GROUPS)

from netboxauth.backend import _cfg
print("NETBOX_REMOTE_AUTH_METHOD via _cfg:", _cfg("NETBOX_REMOTE_AUTH_METHOD"))
print("TACACS config via _cfg:", _cfg("NETBOX_REMOTE_AUTH_TACACS"))
print("RADIUS config via _cfg:", _cfg("NETBOX_REMOTE_AUTH_RADIUS"))
```

Again, you should see your configured values.

---

### 3. Multi-server behaviour

If you configured multiple servers in `SERVERS`:

```python
"SERVERS": [
    {"HOST": "10.10.10.10", "PORT": 49},
    {"HOST": "10.10.10.11", "PORT": 49},
],
```

The backend will:

1. Try the first server.
2. If the connection fails or a network error occurs, it logs a warning and tries the next server.
3. If all servers fail, authentication fails.

> If you only have a single TACACS+/RADIUS server, simply remove the second dictionary from the list.

---

## Advanced Notes

### Profile sync from AAA attributes

If your TACACS+/RADIUS server sends user information such as first name, last name, or email, you can map those attributes into NetBox:

```python
NETBOX_REMOTE_AUTH_FIRST_NAME_ATTR = "givenName"
NETBOX_REMOTE_AUTH_LAST_NAME_ATTR = "sn"
NETBOX_REMOTE_AUTH_EMAIL_ATTR = "mail"
```

On every successful login, the backend will:

- Set `user.first_name` from `givenName` (if present),
- Set `user.last_name` from `sn`,
- Set `user.email` from `mail`.

If any of these are not set in AAA or the config, they are simply left unchanged.

### Access removal

If you remove a user from the TACACS+/RADIUS policy that allows NetBox, the AAA server will reject their login attempts. The backend will then also reject the login and the user will not be able to access NetBox. The local NetBox account remains in the database, but it cannot be used to log in without a successful TACACS+/RADIUS authentication.

---

## Summary

This backend provides a clean, consistent way to:

- Authenticate NetBox users against TACACS+ or RADIUS,
- Map AAA roles to NetBox groups,
- Optionally sync user profile information,
- Use one configuration file (`netboxauth_config.py`) for both Docker and bare-metal deployments,
- Optionally use multiple TACACS+/RADIUS servers with failover.

Contributions, bug reports, and feature requests are welcome!
