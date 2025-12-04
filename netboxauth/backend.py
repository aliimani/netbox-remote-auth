from __future__ import annotations

from typing import Optional, Dict, Any, List, Tuple
import logging
import socket

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

logger = logging.getLogger(__name__)

User = get_user_model()
# Use the actual Group model attached to User.groups (NetBox's custom group model)
GroupModel = User._meta.get_field("groups").remote_field.model


# ----------------------------------------------------------------------
# Configuration loading (Docker + bare-metal)
# ----------------------------------------------------------------------

# 1) netbox-docker & modern NetBox:
#    /etc/netbox/config/*.py are aggregated into netbox.configuration
try:
    from netbox import configuration as netbox_config  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - bare-metal or old versions
    netbox_config = None

# 2) Optional standalone config module for bare-metal installs:
#    netboxauth_config.py next to configuration.py (must be on PYTHONPATH)
try:
    import netboxauth_config as netboxauth_cfg  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - module not found
    netboxauth_cfg = None


def _cfg(name: str, default: Any = None) -> Any:
    """
    Read a setting for the backend.

    Priority:
      1. netbox.configuration  (docker-style aggregated config)
      2. django.conf.settings  (bare-metal / older setups)
      3. netboxauth_config     (local module for bare-metal)
      4. default
    """
    # 1) netbox.configuration aggregator (docker)
    if netbox_config is not None:
        try:
            return getattr(netbox_config, name)
        except AttributeError:
            pass

    # 2) Direct Django settings
    if hasattr(settings, name):
        return getattr(settings, name)

    # 3) Standalone netboxauth_config.py (bare-metal)
    if netboxauth_cfg is not None and hasattr(netboxauth_cfg, name):
        return getattr(netboxauth_cfg, name)

    # 4) Fallback
    return default


# ----------------------------------------------------------------------
# Utility helpers
# ----------------------------------------------------------------------


def get_server_list(cfg: Dict[str, Any], default_port: int) -> List[Tuple[str, int]]:
    """
    Return list of (host, port) tuples for failover.

    Supports:
      cfg["SERVERS"] = [{"HOST": "...", "PORT": 49}, ...]
    Or legacy:
      cfg["HOST"] = "..."
      cfg["PORT"] = 49
    """
    if not cfg:
        return []

    servers = cfg.get("SERVERS")
    if servers:
        out: List[Tuple[str, int]] = []
        for s in servers:
            host = s.get("HOST")
            if not host:
                continue
            out.append((host, int(s.get("PORT", default_port))))
        return out

    host = cfg.get("HOST")
    if host:
        return [(host, int(cfg.get("PORT", default_port)))]

    return []


def parse_kv_arguments(args: List[Any]) -> Dict[str, Any]:
    """
    Turn TACACS AVPairs list into a dict.

    Example:
      [b'role=netbox-admin', b'priv-lvl=15']
    becomes:
      {'role': 'netbox-admin', 'priv-lvl': '15'}
    """
    result: Dict[str, Any] = {}
    for item in args or []:
        if isinstance(item, bytes):
            s = item.decode(errors="ignore")
        else:
            s = str(item)

        if "=" not in s:
            continue

        key, val = s.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue

        existing = result.get(key)
        if existing is None:
            result[key] = val
        else:
            if isinstance(existing, list):
                existing.append(val)
            else:
                result[key] = [existing, val]

    return result


# ----------------------------------------------------------------------
# Main Backend
# ----------------------------------------------------------------------


class NetBoxRemoteAuthBackend(BaseBackend):
    """
    NetBox authentication backend using TACACS+ or RADIUS (e.g. Cisco ISE).

    Works in both netbox-docker and bare-metal NetBox installs.

    Behaviour:
      - Authenticates against TACACS+ / RADIUS.
      - Supports multiple servers for failover.
      - Creates/updates users & groups.
      - Sets is_staff / is_superuser based on groups.
      - On success, ensures user.is_active = True.
      - Optionally syncs first_name, last_name, email from AAA attributes
        using the standard NetBox config keys:
          * REMOTE_AUTH_USER_FIRST_NAME
          * REMOTE_AUTH_USER_LAST_NAME
          * REMOTE_AUTH_USER_EMAIL
    """

    # ------------------------------------------------------------------
    # Django entry points
    # ------------------------------------------------------------------

    def authenticate(
        self,
        request,
        username: Optional[str] = None,
        password: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[User]:
        if not username or not password:
            return None

        # Check that remote auth is globally enabled
        if not bool(_cfg("REMOTE_AUTH_ENABLED", False)):
            return None

        method = _cfg("NETBOX_REMOTE_AUTH_METHOD", None)
        if not method:
            logger.debug("NETBOX_REMOTE_AUTH_METHOD not set; backend inactive.")
            return None

        method = str(method).lower()
        if method not in {"tacacs", "radius"}:
            logger.warning("Unsupported NETBOX_REMOTE_AUTH_METHOD: %r", method)
            return None

        if method == "tacacs":
            result = self._authenticate_tacacs(username, password)
        else:
            result = self._authenticate_radius(username, password)

        if not result.get("success"):
            # AAA rejected (wrong password, no policy, etc.)
            return None

        return self._get_or_create_user(username, password, result, method)

    def get_user(self, user_id: int) -> Optional[User]:
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    # ------------------------------------------------------------------
    # TACACS+ Authentication
    # ------------------------------------------------------------------

    def _authenticate_tacacs(self, username: str, password: str) -> Dict[str, Any]:
        cfg = _cfg("NETBOX_REMOTE_AUTH_TACACS", {}) or {}

        servers = get_server_list(cfg, 49)
        secret = cfg.get("SECRET")
        timeout = int(cfg.get("TIMEOUT", 5))

        if not servers or not secret:
            logger.warning("TACACS config incomplete or missing servers/secret.")
            return {"success": False, "attributes": {}, "remote_roles": []}

        try:
            from tacacs_plus.client import TACACSClient
        except ImportError:  # pragma: no cover - dependency missing
            logger.error(
                "tacacs-plus package is not installed; "
                "TACACS+ authentication is unavailable."
            )
            return {"success": False, "attributes": {}, "remote_roles": []}

        for host, port in servers:
            try:
                client = TACACSClient(
                    host=host,
                    port=port,
                    secret=secret,
                    timeout=timeout,
                    family=socket.AF_INET,
                )

                auth_result = client.authenticate(
                    username=username,
                    password=password,
                    rem_addr="netbox",
                    port="https",
                )

                if not getattr(auth_result, "valid", False):
                    # Authentication failed on this server; try next
                    continue

                # Authorization to fetch AVPairs (role, priv-lvl, etc.)
                try:
                    author = client.authorize(
                        username=username,
                        arguments=[b"service=netbox"],
                        rem_addr="netbox",
                        port="https",
                    )
                    attributes = parse_kv_arguments(
                        getattr(author, "arguments", [])
                    )
                except Exception as exc:  # pragma: no cover - very defensive
                    logger.warning(
                        "TACACS authorization failed for %s on %s:%s: %s",
                        username,
                        host,
                        port,
                        exc,
                    )
                    attributes = {}

                remote_roles = self._extract_remote_roles_tacacs(attributes)

                logger.debug(
                    "TACACS: attributes for %s via %s:%s: %r; remote_roles=%r",
                    username,
                    host,
                    port,
                    attributes,
                    remote_roles,
                )

                return {
                    "success": True,
                    "attributes": attributes,
                    "remote_roles": remote_roles,
                }

            except Exception as exc:  # pragma: no cover - network / TACACS errors
                logger.warning(
                    "Error talking to TACACS server %s:%s for user %s: %s",
                    host,
                    port,
                    username,
                    exc,
                )
                continue

        return {"success": False, "attributes": {}, "remote_roles": []}

    @staticmethod
    def _extract_remote_roles_tacacs(attrs: Dict[str, Any]) -> List[str]:
        """
        Extract roles from TACACS attributes (Cisco ISE, etc.).

        Supported patterns:
          - role=netbox-admin
          - Cisco-AVPair: shell:role="netbox-admin"
          - priv-lvl -> pseudo-role tacacs-priv-<N>
        """
        roles: List[str] = []
        if not attrs:
            return roles

        # 1) Direct "role" attribute
        val = attrs.get("role")
        if isinstance(val, list):
            roles.extend(str(v) for v in val if v)
        elif val:
            roles.append(str(val))

        # 2) Cisco AVPair: shell:role="xyz"
        for key in ("Cisco-AVPair", "cisco-av-pair"):
            cav = attrs.get(key)
            if not cav:
                continue
            values = cav if isinstance(cav, list) else [cav]
            for item in values:
                s = str(item)
                if "shell:role=" in s:
                    roles.append(
                        s.split("=", 1)[1].replace('"', "").strip()
                    )

        # 3) priv-lvl as pseudo-role
        if "priv-lvl" in attrs:
            roles.append(f"tacacs-priv-{attrs['priv-lvl']}")

        # dedupe
        seen = set()
        out: List[str] = []
        for r in roles:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out

    # ------------------------------------------------------------------
    # RADIUS Authentication
    # ------------------------------------------------------------------

    def _authenticate_radius(self, username: str, password: str) -> Dict[str, Any]:
        cfg = _cfg("NETBOX_REMOTE_AUTH_RADIUS", {}) or {}

        servers = get_server_list(cfg, 1812)
        secret = cfg.get("SECRET")
        timeout = int(cfg.get("TIMEOUT", 5))
        nas_id = cfg.get("NAS_IDENTIFIER", "netbox")

        if not servers or not secret:
            logger.warning("RADIUS config incomplete or missing servers/secret.")
            return {"success": False, "attributes": {}, "remote_roles": []}

        try:
            from pyrad.client import Client
            import pyrad.packet as packet
        except ImportError:  # pragma: no cover - dependency missing
            logger.error(
                "pyrad package is not installed; "
                "RADIUS authentication is unavailable."
            )
            return {"success": False, "attributes": {}, "remote_roles": []}

        for host, port in servers:
            try:
                client = Client(server=host, secret=secret.encode())
                client.timeout = timeout
                client.authport = port

                req = client.CreateAuthPacket(code=packet.AccessRequest)
                req["User-Name"] = username
                req["User-Password"] = req.PwCrypt(password)
                req["NAS-Identifier"] = nas_id

                reply = client.SendPacket(req)
                if reply.code != packet.AccessAccept:
                    # Authentication failed on this server; try the next
                    continue

                attributes: Dict[str, Any] = {}
                for key in reply.keys():
                    val = reply.get(key)
                    attributes[key] = val[0] if len(val) == 1 else val

                remote_roles = self._extract_remote_roles_radius(attributes)

                logger.debug(
                    "RADIUS: attributes for %s via %s:%s: %r; remote_roles=%r",
                    username,
                    host,
                    port,
                    attributes,
                    remote_roles,
                )

                return {
                    "success": True,
                    "attributes": attributes,
                    "remote_roles": remote_roles,
                }

            except Exception as exc:  # pragma: no cover - network / RADIUS errors
                logger.warning(
                    "Error talking to RADIUS server %s:%s for user %s: %s",
                    host,
                    port,
                    username,
                    exc,
                )
                continue

        return {"success": False, "attributes": {}, "remote_roles": []}

    @staticmethod
    def _extract_remote_roles_radius(attrs: Dict[str, Any]) -> List[str]:
        """
        Extract roles from RADIUS attributes (Cisco ISE, FreeRADIUS, etc.).

        Supported patterns:
          - role = netbox-admin
          - Cisco-AVPair: shell:role="netbox-admin"
          - Class = netbox-admin
        """
        roles: List[str] = []
        if not attrs:
            return roles

        # 1) Direct "role"
        val = attrs.get("role")
        if isinstance(val, list):
            roles.extend(str(v) for v in val if v)
        elif val:
            roles.append(str(val))

        # 2) Cisco AVPair shell:role="xyz"
        for key in ("Cisco-AVPair", "cisco-av-pair"):
            cav = attrs.get(key)
            if not cav:
                continue
            values = cav if isinstance(cav, list) else [cav]
            for item in values:
                s = str(item)
                if "shell:role=" in s:
                    roles.append(
                        s.split("=", 1)[1].replace('"', "").strip()
                    )

        # 3) Class as generic "role"
        clazz = attrs.get("Class")
        if clazz:
            if isinstance(clazz, list):
                roles.extend(str(v) for v in clazz if v)
            else:
                roles.append(str(clazz))

        # dedupe
        seen = set()
        out: List[str] = []
        for r in roles:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out

    # ------------------------------------------------------------------
    # USER + GROUP HANDLING
    # ------------------------------------------------------------------

    def _get_or_create_user(
        self,
        username: str,
        password: str,
        result: Dict[str, Any],
        method: str,
    ) -> Optional[User]:
        """
        Create or update a NetBox user based on remote authentication result.
        """
        auto_create = bool(_cfg("REMOTE_AUTH_AUTO_CREATE_USER", False))

        try:
            user = User.objects.get(username=username)
            created = False
        except User.DoesNotExist:
            if not auto_create:
                return None
            user = User(username=username)
            # MUST save before touching M2M relations like groups
            user.save()
            created = True

        attrs = result.get("attributes") or {}
        remote_roles = result.get("remote_roles") or []

        # Apply groups based on roles
        self._apply_group_mapping(user, remote_roles)

        # Superuser / staff flags based on group membership
        super_groups = set(_cfg("REMOTE_AUTH_SUPERUSER_GROUPS", []) or [])
        staff_groups = set(_cfg("REMOTE_AUTH_STAFF_GROUPS", []) or [])

        names = set(user.groups.values_list("name", flat=True))

        # Reset flags then re-apply based on groups
        user.is_superuser = False
        # keep existing staff if some other mechanism sets it, then OR with mapping
        staff_flag = user.is_staff

        if names & super_groups:
            user.is_superuser = True
            staff_flag = True

        if names & staff_groups:
            staff_flag = True

        user.is_staff = staff_flag

        # On successful remote auth, ensure the account is active
        user.is_active = True

        # Sync profile info (name, email) from attributes if configured
        self._apply_profile_attributes(user, attrs)

        user.save()
        return user

    def _apply_group_mapping(self, user: User, remote_roles: List[str]) -> None:
        """
        Group logic:

          - Start with REMOTE_AUTH_DEFAULT_GROUPS
          - For each remote role, add a group with the same name
          - If REMOTE_AUTH_GROUP_SYNC_ENABLED is True:
                clear existing groups first

        We intentionally do NOT use an extra mapping dict here: ISE roles
        and NetBox group names are expected to be the same.
        """
        # Start from default groups
        default_groups = _cfg("REMOTE_AUTH_DEFAULT_GROUPS", []) or []
        target_names = set(default_groups)

        # Each role becomes a group name
        for r in remote_roles:
            if r:
                target_names.add(str(r))

        sync = bool(_cfg("REMOTE_AUTH_GROUP_SYNC_ENABLED", False))

        if sync:
            user.groups.clear()

        for name in target_names:
            if not name:
                continue
            try:
                grp, _ = GroupModel.objects.get_or_create(name=name)
                user.groups.add(grp)
            except Exception as exc:  # pragma: no cover - extremely defensive
                logger.warning(
                    "Failed adding user %s to group %s: %s",
                    user.username,
                    name,
                    exc,
                )

    # ------------------------------------------------------------------
    # PROFILE ATTRIBUTE SYNC
    # ------------------------------------------------------------------

    def _apply_profile_attributes(self, user: User, attrs: Dict[str, Any]) -> None:
        """
        Optionally set first_name, last_name, and email from TACACS+/RADIUS attrs.

        Uses the standard NetBox remote-auth config keys, but instead of
        HTTP headers we interpret them as AAA attribute names:

          REMOTE_AUTH_USER_FIRST_NAME  -> attribute name for first name
          REMOTE_AUTH_USER_LAST_NAME   -> attribute name for last name
          REMOTE_AUTH_USER_EMAIL       -> attribute name for email
        """
        if not attrs:
            return

        def _get_attr(name: Optional[str]) -> Optional[str]:
            if not name:
                return None
            value = attrs.get(name)
            if value is None:
                return None
            if isinstance(value, list) and value:
                value = value[0]
            return str(value).strip()

        first_attr = _cfg("REMOTE_AUTH_USER_FIRST_NAME", None)
        last_attr = _cfg("REMOTE_AUTH_USER_LAST_NAME", None)
        email_attr = _cfg("REMOTE_AUTH_USER_EMAIL", None)

        first = _get_attr(first_attr)
        last = _get_attr(last_attr)
        mail = _get_attr(email_attr)

        if first is not None:
            user.first_name = first
        if last is not None:
            user.last_name = last
        if mail is not None:
            user.email = mail
