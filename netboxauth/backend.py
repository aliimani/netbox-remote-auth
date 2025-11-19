# netboxauth/backend.py

from __future__ import annotations

from typing import Optional, Dict, Any, List, Tuple
import socket
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

from tacacs_plus.client import TACACSClient

logger = logging.getLogger(__name__)

User = get_user_model()
# Use the actual Group model attached to User.groups (NetBox's custom group model)
GroupModel = User._meta.get_field("groups").remote_field.model


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def get_server_list(cfg: dict, default_port: int) -> List[Tuple[str, int]]:
    """
    Get a list of (host, port) tuples from config dict.

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
    Parse TACACS AVPairs list into a dict.

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
# Main backend
# ----------------------------------------------------------------------


class NetBoxRemoteAuthBackend(BaseBackend):
    """
    NetBox authentication backend using TACACS+ or RADIUS (Cisco ISE).

    Behaviour:

      - Selects method by NETBOX_REMOTE_AUTH_METHOD: "tacacs" or "radius"

      - NETBOX_REMOTE_AUTH_TACACS / NETBOX_REMOTE_AUTH_RADIUS settings:
          * SERVERS / HOST / PORT
          * SECRET
          * TIMEOUT

      - Global NetBox REMOTE_AUTH_* settings:
          * REMOTE_AUTH_AUTO_CREATE_USER (bool)
          * REMOTE_AUTH_DEFAULT_GROUPS (list of group names)
          * REMOTE_AUTH_GROUP_SYNC_ENABLED (bool)
          * REMOTE_AUTH_SUPERUSER_GROUPS (list of group names)
          * REMOTE_AUTH_STAFF_GROUPS (list of group names)

      - Group model:

          * Every remote "role" is treated as a NetBox group name.
            Example: ISE sends role=netbox-admin -> group "netbox-admin"

          * REMOTE_AUTH_DEFAULT_GROUPS are always added
          * No NETBOX_REMOTE_AUTH_GROUP_MAP is used in this version
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

        method = getattr(settings, "NETBOX_REMOTE_AUTH_METHOD", None)
        if method not in {"tacacs", "radius"}:
            return None

        if method == "tacacs":
            result = self._authenticate_tacacs(username, password)
        else:
            result = self._authenticate_radius(username, password)

        if not result.get("success"):
            return None

        return self._get_or_create_user(username, password, result, method)

    def get_user(self, user_id: int) -> Optional[User]:
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    # ------------------------------------------------------------------
    # TACACS+ auth
    # ------------------------------------------------------------------

    def _authenticate_tacacs(self, username: str, password: str) -> Dict[str, Any]:
        cfg = getattr(settings, "NETBOX_REMOTE_AUTH_TACACS", {}) or {}

        servers = get_server_list(cfg, 49)
        secret = cfg.get("SECRET")
        timeout = int(cfg.get("TIMEOUT", 5))

        if not servers or not secret:
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
                    continue

                # Authorization to get AVPairs (role, priv-lvl, etc.)
                try:
                    author = client.authorize(
                        username=username,
                        arguments=[b"service=netbox"],
                        rem_addr="netbox",
                        port="https",
                    )
                    attributes = parse_kv_arguments(getattr(author, "arguments", []))
                except Exception:
                    attributes = {}

                remote_roles = self._extract_remote_roles_tacacs(attributes)

                logger.debug(
                    "TACACS: attributes for %s: %r; remote_roles=%r",
                    username,
                    attributes,
                    remote_roles,
                )

                return {
                    "success": True,
                    "attributes": attributes,
                    "remote_roles": remote_roles,
                }

            except Exception:
                continue

        return {"success": False, "attributes": {}, "remote_roles": []}

    @staticmethod
    def _extract_remote_roles_tacacs(attrs: Dict[str, Any]) -> List[str]:
        """Return a list of role strings from TACACS attributes."""
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
                    roles.append(s.split("=", 1)[1].replace('"', ""))

        # 3) priv-lvl as pseudo-role
        if "priv-lvl" in attrs:
            roles.append(f"tacacs-priv-{attrs['priv-lvl']}")

        # dedupe
        out: List[str] = []
        seen = set()
        for r in roles:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out

    # ------------------------------------------------------------------
    # RADIUS auth
    # ------------------------------------------------------------------

    def _authenticate_radius(self, username: str, password: str) -> Dict[str, Any]:
        cfg = getattr(settings, "NETBOX_REMOTE_AUTH_RADIUS", {}) or {}
        servers = get_server_list(cfg, 1812)
        secret = cfg.get("SECRET")
        timeout = int(cfg.get("TIMEOUT", 5))
        nas_id = cfg.get("NAS_IDENTIFIER", "netbox")

        if not servers or not secret:
            return {"success": False, "attributes": {}, "remote_roles": []}

        try:
            from pyrad.client import Client
            import pyrad.packet as packet
        except Exception:
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
                    continue

                attributes: Dict[str, Any] = {}
                for key in reply.keys():
                    val = reply.get(key)
                    attributes[key] = val[0] if len(val) == 1 else val

                remote_roles = self._extract_remote_roles_radius(attributes)

                logger.debug(
                    "RADIUS: attributes for %s: %r; remote_roles=%r",
                    username,
                    attributes,
                    remote_roles,
                )

                return {
                    "success": True,
                    "attributes": attributes,
                    "remote_roles": remote_roles,
                }

            except Exception:
                continue

        return {"success": False, "attributes": {}, "remote_roles": []}

    @staticmethod
    def _extract_remote_roles_radius(attrs: Dict[str, Any]) -> List[str]:
        """Return a list of role strings from RADIUS attributes."""
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
                    roles.append(s.split("=", 1)[1].replace('"', ""))

        # 3) RADIUS Class as generic "role"
        clazz = attrs.get("Class")
        if clazz:
            if isinstance(clazz, list):
                roles.extend(str(v) for v in clazz if v)
            else:
                roles.append(str(clazz))

        # dedupe
        out: List[str] = []
        seen = set()
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
        auto_create = getattr(settings, "REMOTE_AUTH_AUTO_CREATE_USER", False)

        try:
            user = User.objects.get(username=username)
            created = False
        except User.DoesNotExist:
            if not auto_create:
                return None
            user = User(username=username)
            # IMPORTANT: save before M2M operations
            user.save()
            created = True

        remote_roles = result.get("remote_roles") or []

        # Apply group handling
        self._apply_group_mapping(user, remote_roles)

        # Superuser / staff flags from group membership
        super_groups = set(
            getattr(settings, "REMOTE_AUTH_SUPERUSER_GROUPS", []) or []
        )
        staff_groups = set(
            getattr(settings, "REMOTE_AUTH_STAFF_GROUPS", []) or []
        )
        names = set(user.groups.values_list("name", flat=True))

        if names & super_groups:
            user.is_superuser = True
            user.is_staff = True

        if names & staff_groups:
            user.is_staff = True

        user.save()
        return user

    def _apply_group_mapping(self, user: User, remote_roles: List[str]) -> None:
        """
        Final group logic:

          - Start with REMOTE_AUTH_DEFAULT_GROUPS
          - For each role in remote_roles, add a group with the same name
          - If REMOTE_AUTH_GROUP_SYNC_ENABLED is True:
                clear user's groups and set exactly that set
            Else:
                just ensure those groups exist in addition to what's already there
        """
        # Start from default groups
        target_names = set(
            getattr(settings, "REMOTE_AUTH_DEFAULT_GROUPS", []) or []
        )

        # Direct role -> same-name group
        for r in remote_roles:
            if r:
                target_names.add(str(r))

        sync = bool(getattr(settings, "REMOTE_AUTH_GROUP_SYNC_ENABLED", False))

        if sync:
            user.groups.clear()

        for name in target_names:
            if not name:
                continue
            try:
                grp, _ = GroupModel.objects.get_or_create(name=name)
                user.groups.add(grp)
            except Exception as e:
                logger.warning(
                    "Failed adding user %s to group %s: %s",
                    user.username,
                    name,
                    e,
                )
