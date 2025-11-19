# NetBox Remote Authentication Backend (TACACS+ / RADIUS)

This project provides a custom authentication backend for NetBox that allows you to authenticate users directly against TACACS+ or RADIUS servers (such as Cisco ISE, FreeRADIUS, ACS, or any standard AAA platform).

Unlike NetBox’s built-in “RemoteUser” header-based authentication, this backend communicates directly with your TACACS+/RADIUS servers and maps the user’s AAA-assigned *role* to *NetBox groups* automatically.

The goal of this project is to make remote authentication work cleanly without needing reverse proxies, SSO, or custom HTTP headers, just talk to your TACACS or RADIUS server, receive the user’s role, and assign them to the correct NetBox groups.

---

## Table of Contents

1. How It Works  
2. Features  
3. Requirements  
4. Installing The Backend  
5. NetBox Configuration  
6. AAA Server Configuration (ISE / RADIUS / TACACS)  
7. Group Mapping Explained  
8. Troubleshooting  
9. Advanced Options  
10. Reference

---

## How It Works

1. A user enters their username and password on the NetBox login page.  
2. NetBox calls this backend, which tries TACACS+ or RADIUS (depending on your config).  
3. Your AAA server validates the credentials.  
4. The server sends back attributes (e.g. `role=netbox-admin`).  
5. The backend:  
   - creates the user in NetBox (if allowed),  
   - assigns NetBox groups based on the received roles,  
   - optionally sets `is_staff` and `is_superuser`.

This makes it possible to centrally manage NetBox permissions using your TACACS+/RADIUS server.

---

## Features

✔ Supports TACACS+
✔ Supports RADIUS
✔ Multi-server failover support  
✔ Automatic group creation based on AAA “role” attribute  
✔ Optional default groups  
✔ Optional superuser / staff mapping  
✔ No headers, SSO, or reverse proxy required  
✔ Works with NetBox Docker or bare-metal  

---

## Requirements

Add the following Python packages to your NetBox environment:

```
tacacs-plus
pyrad
```

---

## Installing The Backend

Place your backend in:

```
netbox/netbox/netboxauth/
    ├── __init__.py
    └── backend.py
```

For netbox-docker, you can mount this folder or include it in your custom container image.

---

## NetBox Configuration

Open `configuration.py` (or `configuration/extra.py` if you use netbox-docker) and add the following.

---

### 1. Enable Remote Authentication (Mandatory)

```
REMOTE_AUTH_ENABLED = True
REMOTE_AUTH_BACKEND = "netboxauth.backend.NetBoxRemoteAuthBackend"
NETBOX_REMOTE_AUTH_METHOD = "tacacs"    # or "radius"
```

---

### 2. TACACS+ Server Configuration (Mandatory if TACACS is used)

```
NETBOX_REMOTE_AUTH_TACACS = {
    "SERVERS": [
        {"HOST": "10.10.10.10", "PORT": 49},
        {"HOST": "10.10.10.11", "PORT": 49},
    ],
    "SECRET": "SuperSecretKey",
    "TIMEOUT": 5,
}
```

Supports multiple servers for redundancy.

---

### 3. RADIUS Server Configuration (Mandatory if RADIUS is used)

```
NETBOX_REMOTE_AUTH_RADIUS = {
    "SERVERS": [
        {"HOST": "10.10.10.10", "PORT": 1812},
        {"HOST": "10.10.10.11", "PORT": 1812},
    ],
    "SECRET": "SuperSecretKey",
    "TIMEOUT": 5,
}
```

---

### 4. User & Group Handling

These options define how NetBox handles users and groups created by remote authentication.

#### Mandatory

```
REMOTE_AUTH_AUTO_CREATE_USER = True
```

NetBox will automatically create local users after successful TACACS+/RADIUS authentication.

---

### Optional (Common and Recommended)

```
REMOTE_AUTH_GROUP_SYNC_ENABLED = True
```

If enabled, the user’s NetBox groups will exactly match what TACACS+/RADIUS sends.

---

### Default Group (Optional)

```
REMOTE_AUTH_DEFAULT_GROUPS = ['netbox-staff']
```

This group will be added to *every* remote user.

---

### Superuser/Staff Group Mapping (Optional)

```
REMOTE_AUTH_SUPERUSER_GROUPS = ['netbox-admin']
REMOTE_AUTH_STAFF_GROUPS = ['netbox-staff']
```
A mapping of permissions to assign a new user account when created using remote authentication.

Meaning:

- If a user is in group `netbox-admin` → they become `is_superuser = True` in NetBox
- If a user is in group `netbox-staff` → they become `is_staff = True` in NetBox

---

### Default Permissions (Optional)

```
REMOTE_AUTH_DEFAULT_PERMISSIONS = {}
```

---

## AAA Server Configuration  
(Applies to Cisco ISE TACACS+, Cisco ISE RADIUS, FreeRADIUS, Windows NPS, etc.)

Your AAA server must send a **role attribute** that identifies the user’s NetBox role.

Examples:

```
role=netbox-admin
```

Or Cisco AVPair:

```
Cisco-AVPair = shell:role="netbox-admin"
```

Or RADIUS Class:

```
Class = netbox-admin
```

Each role name becomes a **NetBox group name**.

If the user receives multiple roles, they will be added to multiple NetBox groups.

---

### TACACS+ Example (Cisco ISE)

Under:

**Device Administration → TACACS Profiles → Custom Attributes**

Add:

```
shell:role="netbox-admin"
```

Then assign this profile in the Authorization Policy.

---

### RADIUS Example (Cisco ISE)

Under:

**Policy → Authorization Profiles → RADIUS**

Add either:

```
Class = netbox-admin
```

Or:

```
Cisco-AVPair = "shell:role=netbox-admin"
```

---

### FreeRADIUS Example

```
Class := "netbox-admin"
```

---

## Group Mapping Explained

This backend is built around **simple, direct mapping**:

> The value of the TACACS+/RADIUS “role” attribute = the NetBox group name.

Example:

AAA sends:

```
role=netbox-readonly
```

NetBox will:

- create group `netbox-readonly` if missing  
- assign user to that group  

No extra mapping file is needed unless you want aliases.

---

## Troubleshooting

### View logs inside NetBox Docker

```
docker logs netbox-docker-netbox-1 | grep remote_roles
```

Example output:

```
TACACS: attributes for user1: {'role': 'netbox-admin'}; remote_roles=['netbox-admin']
```

If no roles appear:

- Verify your ISE authorization profile  
- Make sure your device is added to the TACACS+/RADIUS device list  
- Ensure that your attribute names match what the backend supports:
  - `role`
  - `Class`
  - `Cisco-AVPair`

---

## Advanced Options

NetBox supports many remote-auth related options.

Official documentation:

https://netboxlabs.com/docs/netbox/configuration/remote-authentication/

You can optionally configure:

- custom permission grants  
- syncing HTTP headers  
- external SSO integration  
- mixed-mode authentication  

For this backend, only the options documented above are required.

---

## Reference

- TACACS+ Python library  
- pyrad RADIUS library  
- Cisco ISE documentation  
- FreeRADIUS documentation  
