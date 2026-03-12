#
# EXAMPLE: netboxauth_config.py
# Copy this file, modify values, and place it in the correct directory:
#
# Docker:     netbox-docker/configuration/netboxauth_config.py
# Bare-metal: /opt/netbox/netbox/netbox/netboxauth_config.py
#

# Enable authentication backend
REMOTE_AUTH_ENABLED = True

# Use custom backend
REMOTE_AUTH_BACKEND = "netboxauth.backend.NetBoxRemoteAuthBackend"

# Auto-create accounts
REMOTE_AUTH_AUTO_CREATE_USER = True

# Replace groups based on TACACS+/RADIUS roles
REMOTE_AUTH_GROUP_SYNC_ENABLED = True

# Default groups for all remote users
REMOTE_AUTH_DEFAULT_GROUPS = ["netbox-staff"]   #update the group name if it is required

# Admin group mappings
REMOTE_AUTH_SUPERUSER_GROUPS = ["netbox-admin"] #update the group name if it is required
REMOTE_AUTH_STAFF_GROUPS = ["netbox-staff"]     # NetBox >=4.5: ignored because User has no is_staff

# OPTIONAL: map AAA attributes → NetBox profile fields
REMOTE_AUTH_USER_FIRST_NAME = "givenName"
REMOTE_AUTH_USER_LAST_NAME  = "sn"
REMOTE_AUTH_USER_EMAIL      = "mail"


# Select authentication method
NETBOX_REMOTE_AUTH_METHOD = "tacacs"   # or "radius"



# ----------------------------------------------------------------------
# TACACS+ CONFIGURATION (Enable only if using TACACS+)
# ----------------------------------------------------------------------
# Uncomment the following block if NETBOX_REMOTE_AUTH_METHOD = "tacacs"
# You can configure one or more TACACS servers.
# The backend will try each server in order. If the first fails (connection/timeouts),
# it will try the next one.

# NETBOX_REMOTE_AUTH_TACACS = {
#     "SERVERS": [
#         {"HOST": "10.10.10.10", "PORT": 49},
#         {"HOST": "10.10.10.11", "PORT": 49},  # Optional second server, if you only have one TACACS server, remove the second entry.
#     ],
#     "SECRET": "SecretKey",
#     "TIMEOUT": 5,
# }



# ----------------------------------------------------------------------
# RADIUS CONFIGURATION (Enable only if using RADIUS)
# ----------------------------------------------------------------------
# Uncomment the following block if NETBOX_REMOTE_AUTH_METHOD = "radius"
# You can configure one or more RADIUS servers.
# The backend will try each server in order. If the first fails (connection/timeouts),
# it will try the next one.

# NETBOX_REMOTE_AUTH_RADIUS = {
#     "SERVERS": [
#         {"HOST": "10.10.20.10", "PORT": 1812},
#         {"HOST": "10.10.20.11", "PORT": 1812},  # Optional second server, if you only have one RADIUS server, remove the second entry.
#     ],
#     "SECRET": "SecretKey",
#     "TIMEOUT": 5,
#     # "NAS_IDENTIFIER": "netbox",  # Optional NAS-Identifier override used in RADIUS requests
# }


