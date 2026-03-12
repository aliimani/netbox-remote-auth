from __future__ import annotations

from typing import Optional, Dict, Any, List, Tuple
import logging
import socket
import tempfile
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

logger = logging.getLogger(__name__)

User = get_user_model()
# Use the actual Group model attached to User.groups (NetBox's custom group model)
GroupModel = User._meta.get_field("groups").remote_field.model

def _has_user_field(field_name: str) -> bool:
    """Return True if the active User model exposes a concrete field."""
    try:
        User._meta.get_field(field_name)
        return True
    except Exception:
        return False


USER_HAS_IS_SUPERUSER = _has_user_field("is_superuser")
USER_HAS_IS_STAFF = _has_user_field("is_staff")
USER_HAS_IS_ACTIVE = _has_user_field("is_active")

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
      cfg["SERVERS"] = [{"HOST": "...", "PORT": 49/1812}, ...]
    Or legacy:
      cfg["HOST"] = "..."
      cfg["PORT"] = 49/1812
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


def _normalize_text(v: Any) -> str:
    """
    Normalize RADIUS/TACACS attribute values into a clean string.
    Handles bytes properly (critical for RADIUS Class values).
    """
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode(errors="ignore").strip()
        except Exception:
            return ""
    return str(v).strip()


def _split_multi(v: Any) -> List[str]:
    """
    Normalize and split multi-valued role strings.
    Handles bytes, str, comma-separated, semicolon-separated values.
    """
    s = _normalize_text(v)
    if not s:
        return []

    # Some systems might send "a,b" or "a; b" as a single attribute.
    if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]
    if ";" in s:
        return [p.strip() for p in s.split(";") if p.strip()]
    return [s]


# ----------------------------------------------------------------------
# Main Backend
# ----------------------------------------------------------------------


class NetBoxRemoteAuthBackend(BaseBackend):
    """
    NetBox authentication backend using TACACS+ or RADIUS (e.g. Cisco ISE).

    Behaviour:
      - Authenticates against TACACS+ / RADIUS.
      - Supports multiple servers for failover.
      - Creates/updates users & groups.
      - Sets is_superuser based on groups.
      - Sets is_staff when the active User model exposes that field.
      - On success, ensures user.is_active = True (when field exists).
      - Optionally syncs first_name, last_name, email from AAA attributes.
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
        except ImportError:  # pragma: no cover
            logger.error(
                "tacacs-plus package is not installed; TACACS+ authentication is unavailable."
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
                    continue

                # Authorization to fetch AVPairs (role, priv-lvl, etc.)
                try:
                    author = client.authorize(
                        username=username,
                        arguments=[b"service=netbox"],
                        rem_addr="netbox",
                        port="https",
                    )
                    attributes = parse_kv_arguments(getattr(author, "arguments", []))
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "TACACS authorization failed for %s on %s:%s: %s",
                        username,
                        host,
                        port,
                        exc,
                        exc_info=True,
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

            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Error talking to TACACS server %s:%s for user %s: %s",
                    host,
                    port,
                    username,
                    exc,
                    exc_info=True,
                )
                continue

        return {"success": False, "attributes": {}, "remote_roles": []}

    @staticmethod
    def _extract_remote_roles_tacacs(attrs: Dict[str, Any]) -> List[str]:
        """
        Extract roles from TACACS attributes.

        Supports:
          - role=netbox-admin (may appear multiple times)
          - Cisco-AVPair: shell:role="netbox-admin" (may appear multiple times)
          - priv-lvl -> pseudo-role tacacs-priv-<N>
        """
        roles: List[str] = []
        if not attrs:
            return roles

        # 1) Direct "role" attribute
        val = attrs.get("role")
        values = val if isinstance(val, list) else ([val] if val else [])
        for v in values:
            roles.extend(_split_multi(v))

        # 2) Cisco AVPair: shell:role="xyz"
        for key in ("Cisco-AVPair", "cisco-av-pair"):
            cav = attrs.get(key)
            if not cav:
                continue
            cav_values = cav if isinstance(cav, list) else [cav]
            for item in cav_values:
                s = _normalize_text(item)
                if "shell:role=" in s:
                    extracted = s.split("=", 1)[1].replace('"', "").strip()
                    roles.extend(_split_multi(extracted))

        # 3) priv-lvl as pseudo-role
        if "priv-lvl" in attrs:
            roles.append(f"tacacs-priv-{_normalize_text(attrs['priv-lvl'])}")

        # dedupe
        seen = set()
        out: List[str] = []
        for r in roles:
            r = _normalize_text(r)
            if r and r not in seen:
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
            from pyrad.dictionary import Dictionary
            import pyrad.packet as packet
        except ImportError:  # pragma: no cover
            logger.error("pyrad package is not installed; RADIUS authentication is unavailable.")
            return {"success": False, "attributes": {}, "remote_roles": []}

        # Minimal dictionary embedded in code (no additional project file needed).
        MIN_RADIUS_DICT = b"""
ATTRIBUTE User-Name 1 string
ATTRIBUTE User-Password 2 string
ATTRIBUTE NAS-IP-Address 4 ipaddr
ATTRIBUTE NAS-Port 5 integer
ATTRIBUTE Service-Type 6 integer
ATTRIBUTE Reply-Message 18 string
ATTRIBUTE State 24 string
ATTRIBUTE Class 25 string
ATTRIBUTE NAS-Identifier 32 string

VENDOR Cisco 9
BEGIN-VENDOR Cisco
ATTRIBUTE Cisco-AVPair 1 string
END-VENDOR Cisco
"""

        dict_path: Optional[str] = None
        try:
            fd, dict_path = tempfile.mkstemp(prefix="netbox-radius-", suffix=".dict")
            os.write(fd, MIN_RADIUS_DICT)
            os.close(fd)
            radius_dict = Dictionary(dict_path)
        except Exception as exc:
            logger.error(
                "Failed to create/load embedded RADIUS dictionary: %s",
                exc,
                exc_info=True,
            )
            if dict_path:
                try:
                    os.unlink(dict_path)
                except Exception:
                    pass
            return {"success": False, "attributes": {}, "remote_roles": []}

        try:
            logger.info("RADIUS auth attempt for %s using %d server(s)", username, len(servers))

            for host, port in servers:
                try:
                    client = Client(server=host, secret=secret.encode(), dict=radius_dict)
                    client.timeout = timeout
                    client.authport = port

                    req = client.CreateAuthPacket(code=packet.AccessRequest)
                    req["User-Name"] = username

                    # IMPORTANT for Cisco ISE (and valid generally): encrypted User-Password
                    req["User-Password"] = req.PwCrypt(password)

                    req["NAS-Identifier"] = nas_id

                    reply = client.SendPacket(req)

                    if reply.code != packet.AccessAccept:
                        logger.info(
                            "RADIUS reject from %s:%s for user %s (code=%s)",
                            host,
                            port,
                            username,
                            reply.code,
                        )
                        continue

                    attributes: Dict[str, Any] = {}
                    for k, v in reply.items():
                        # Normalize list-of-one -> scalar, keep list if multi-valued
                        if isinstance(v, list) and len(v) == 1:
                            attributes[k] = v[0]  # keep original key type
                        else:
                            attributes[k] = v      # keep original key type

                    remote_roles = self._extract_remote_roles_radius(attributes)

                    logger.info("RADIUS accept for user %s via %s:%s", username, host, port)
                    logger.debug(
                        "RADIUS: attributes keys=%r",
                        list(attributes.keys()),
                    )
                    logger.debug(
                        "RADIUS: remote_roles=%r",
                        remote_roles,
                    )

                    return {
                        "success": True,
                        "attributes": attributes,
                        "remote_roles": remote_roles,
                    }

                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "Error talking to RADIUS server %s:%s for user %s: %s",
                        host,
                        port,
                        username,
                        exc,
                        exc_info=True,
                    )
                    continue

            return {"success": False, "attributes": {}, "remote_roles": []}

        finally:
            if dict_path:
                try:
                    os.unlink(dict_path)
                except Exception:
                    pass

    @staticmethod
    def _extract_remote_roles_radius(attrs: Dict[Any, Any]) -> List[str]:
        """
        Extract roles from RADIUS attributes.

        Supported patterns:
          - role = netbox-admin (single or multi)
          - Cisco-AVPair: shell:role="netbox-admin" (single or multi)
          - Class = netbox-admin (single or multi)

        IMPORTANT:
          Some pyrad/dictionary combinations may expose "Class" as:
            - "Class" (string key)
            - 25 (numeric key)
            - a pyrad attribute object
          So we look up by name AND by the standard ID (25).
        """
        roles: List[str] = []
        if not attrs:
            return roles

        def _get_any(keys: List[Any]) -> Any:
            for k in keys:
                if k in attrs:
                    return attrs.get(k)
            # Also try stringified key matching as a fallback
            for k_attr in attrs.keys():
                if _normalize_text(k_attr) in [str(x) for x in keys]:
                    return attrs.get(k_attr)
            return None

        # 1) Direct "role"
        val = _get_any(["role"])
        values = val if isinstance(val, list) else ([val] if val else [])
        for v in values:
            roles.extend(_split_multi(v))

        # 2) Cisco AVPair shell:role="xyz"
        cav = _get_any(["Cisco-AVPair", "cisco-av-pair"])
        cav_values = cav if isinstance(cav, list) else ([cav] if cav else [])
        for item in cav_values:
            s = _normalize_text(item)
            if "shell:role=" in s:
                extracted = s.split("=", 1)[1].replace('"', "").strip()
                roles.extend(_split_multi(extracted))

        # 3) Class (by name or by ID 25)
        clazz = _get_any(["Class", 25])
        clazz_values = clazz if isinstance(clazz, list) else ([clazz] if clazz else [])
        for v in clazz_values:
            roles.extend(_split_multi(v))

        # dedupe
        seen = set()
        out: List[str] = []
        for r in roles:
            r = _normalize_text(r)
            if r and r not in seen:
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
        except User.DoesNotExist:
            if not auto_create:
                return None
            user = User(username=username)
            user.save()  # save before touching M2M

        attrs = result.get("attributes") or {}
        remote_roles = result.get("remote_roles") or []

        # Apply groups based on roles
        self._apply_group_mapping(user, remote_roles)

        # Superuser / staff flags based on group membership.
        # NOTE: NetBox 4.x user model no longer provides `is_staff`.
        super_groups = set(_cfg("REMOTE_AUTH_SUPERUSER_GROUPS", []) or [])
        staff_groups = set(_cfg("REMOTE_AUTH_STAFF_GROUPS", []) or [])

        names = set(user.groups.values_list("name", flat=True))

        if USER_HAS_IS_SUPERUSER:
            user.is_superuser = False

        # Preserve existing staff where available, then OR with mapping.
        staff_flag = bool(getattr(user, "is_staff", False))

        if names & super_groups:
             if USER_HAS_IS_SUPERUSER:
                user.is_superuser = True

        if names & staff_groups:
            staff_flag = True

        if USER_HAS_IS_STAFF:
            user.is_staff = staff_flag
        elif staff_groups:
            logger.debug(
                "REMOTE_AUTH_STAFF_GROUPS configured but ignored because User model has no is_staff field."
            )

        if USER_HAS_IS_ACTIVE:
            user.is_active = True

        self._apply_profile_attributes(user, attrs)

        user.save()
        return user

    def _apply_group_mapping(self, user: User, remote_roles: List[str]) -> None:
        """
        Group logic:

        - Start with REMOTE_AUTH_DEFAULT_GROUPS
        - For each remote role, add a group with the same name (or mapped name)
        - If REMOTE_AUTH_GROUP_SYNC_ENABLED is True:
                clear existing groups first

        Optional mapping table:
        NETBOX_REMOTE_AUTH_GROUP_MAP = {
            "ise-role-a": ["netbox-staff", "netbox-readonly"],
            "ise-role-b": "netbox-admin",
        }

        Optional allow-list (recommended to prevent junk groups):
        NETBOX_REMOTE_AUTH_ALLOWED_GROUPS = ["netbox-admin", "netbox-staff", "netbox-ipam-admin"]

        Optional deny prefixes:
        NETBOX_REMOTE_AUTH_DENY_GROUP_PREFIXES = ["CACS:"]
        """

        def _norm(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, bytes):
                try:
                    return v.decode(errors="ignore").strip()
                except Exception:
                    return ""
            return str(v).strip()

        # Start from default groups
        default_groups = _cfg("REMOTE_AUTH_DEFAULT_GROUPS", []) or []
        target_names = set(_norm(g) for g in default_groups if _norm(g))

        # Optional mapping table (keeps your existing behavior if not set)
        group_map = _cfg("NETBOX_REMOTE_AUTH_GROUP_MAP", {}) or {}

        # Optional allow-list (if not set, it's ignored)
        allowed_groups = set(_cfg("NETBOX_REMOTE_AUTH_ALLOWED_GROUPS", []) or [])

        # Deny prefixes: always block Cisco ISE internal session Class (CACS:...)
        deny_prefixes = list(_cfg("NETBOX_REMOTE_AUTH_DENY_GROUP_PREFIXES", []) or [])
        if "CACS:" not in deny_prefixes:
            deny_prefixes.append("CACS:")

        for r in remote_roles or []:
            role = _norm(r)
            if not role:
                continue

            # Block Cisco ISE internal/session identifiers (commonly sent as Class)
            # Example: CACS:<opaque>:<ise-node>/<session>/<counter>
            if any(role.startswith(pfx) for pfx in deny_prefixes if pfx):
                continue

            # Apply mapping if configured
            mapped = group_map.get(role)
            if mapped:
                if isinstance(mapped, (list, tuple, set)):
                    candidates = [_norm(x) for x in mapped]
                else:
                    candidates = [_norm(mapped)]
            else:
                candidates = [role]

            # Add candidates, optionally constrained by allow-list
            for name in candidates:
                name = _norm(name)
                if not name:
                    continue
                if allowed_groups and name not in allowed_groups:
                    continue
                target_names.add(name)

        sync = bool(_cfg("REMOTE_AUTH_GROUP_SYNC_ENABLED", False))
        if sync:
            user.groups.clear()

        for name in target_names:
            name = _norm(name)
            if not name:
                continue
            try:
                grp, _ = GroupModel.objects.get_or_create(name=name)
                user.groups.add(grp)
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Failed adding user %s to group %s: %s",
                    user.username,
                    name,
                    exc,
                    exc_info=True,
                )


    # ------------------------------------------------------------------
    # PROFILE ATTRIBUTE SYNC
    # ------------------------------------------------------------------

    def _apply_profile_attributes(self, user: User, attrs: Dict[str, Any]) -> None:
        """
        Optionally set first_name, last_name, and email from TACACS+/RADIUS attrs.

        Uses these config keys as AAA attribute names:
          REMOTE_AUTH_USER_FIRST_NAME
          REMOTE_AUTH_USER_LAST_NAME
          REMOTE_AUTH_USER_EMAIL
        """
        if not attrs:
            return

        def _get_attr(name: Optional[str]) -> Optional[str]:
            if not name:
                return None

            # attrs keys might be non-string; attempt direct and fallback match
            value = None
            if name in attrs:
                value = attrs.get(name)
            else:
                for k in attrs.keys():
                    if _normalize_text(k) == name:
                        value = attrs.get(k)
                        break

            if value is None:
                return None
            if isinstance(value, list) and value:
                value = value[0]
            text = _normalize_text(value)
            return text or None

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
