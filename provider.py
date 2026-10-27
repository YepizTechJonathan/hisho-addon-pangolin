"""Pangolin integration for Hisho (see addon.yaml). Runs inside Core.

Talks to Pangolin's integration API with an org API key (Bearer). Responses are
``{"data": ..., "success": ..., "message": ...}``. Besides the page (summary/details/act) it offers
helpers other add-ons use (the Web Factory): ``connect_core``, ``ensure_link``, ``retarget``.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

NEWT_IMAGE = "fosrl/newt:1.18.1"


class PangolinError(RuntimeError):
    pass


def api_base(entry: dict[str, Any]) -> str:
    if entry.get("api_url"):
        return str(entry["api_url"]).rstrip("/")
    return str(entry["url"]).rstrip("/") + "/api/v1"


def call(entry: dict[str, Any], ctx: Any, method: str, path: str, body: Any = None, timeout: int = 15) -> Any:
    req = urllib.request.Request(api_base(entry) + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {ctx.secret(entry['api_key'])}",
                                          "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            msg = json.load(exc).get("message")
        except Exception:  # noqa: BLE001
            msg = None
        if exc.code == 401:
            raise PangolinError("Pangolin refused the API key (expired, deleted, or for another server)")
        if exc.code == 403:
            raise PangolinError(f"the API key isn't allowed to do this ({msg or path}); give it more permissions")
        raise PangolinError(f"Pangolin answered {exc.code} on {path}: {msg or exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PangolinError(f"can't reach Pangolin at {api_base(entry)} ({getattr(exc, 'reason', exc)})")
    if isinstance(payload, dict) and payload.get("success") is False:
        raise PangolinError(str(payload.get("message") or "Pangolin said no"))
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


def org_id(entry: dict[str, Any], ctx: Any) -> str:
    if entry.get("org"):
        return str(entry["org"])
    try:
        data = call(entry, ctx, "GET", "/orgs")
    except PangolinError as exc:
        # An organization's own API key can't list organizations (only server-wide keys can).
        raise PangolinError("fill in the Organization ID: it's the part of your Pangolin address after the "
                            "domain, e.g. example-co-llc in pangolin.example.com/example-co-llc/settings "
                            f"({exc})") from None
    orgs = (data or {}).get("orgs") or []
    if len(orgs) != 1:
        raise PangolinError("the key sees several organizations (or none): fill in the Organization ID")
    return str(orgs[0]["orgId"])


def sites(entry: dict[str, Any], ctx: Any) -> list[dict[str, Any]]:
    data = call(entry, ctx, "GET", f"/org/{org_id(entry, ctx)}/sites?pageSize=500")
    return list((data or {}).get("sites") or [])


def resources(entry: dict[str, Any], ctx: Any) -> list[dict[str, Any]]:
    data = call(entry, ctx, "GET", f"/org/{org_id(entry, ctx)}/resources?pageSize=500")
    return list((data or {}).get("resources") or [])


def _targets(res: dict[str, Any], entry: dict[str, Any], ctx: Any) -> list[dict[str, Any]]:
    if res.get("targets") is not None:
        return list(res["targets"])
    data = call(entry, ctx, "GET", f"/resource/{res['resourceId']}/targets")
    return list((data or {}).get("targets") or [])


def _site_states(entry: dict[str, Any], ctx: Any) -> dict[Any, dict[str, Any]]:
    out: dict[Any, dict[str, Any]] = {}
    for s in sites(entry, ctx):
        out[s.get("siteId")] = s
        out[s.get("name")] = s
    return out


def summary(entries: dict[str, dict[str, Any]], ctx: Any) -> dict[str, Any]:
    problems, n_sites, n_online, n_links = [], 0, 0, 0
    for name, entry in entries.items():
        try:
            ss = sites(entry, ctx)
            rs = resources(entry, ctx)
        except Exception as exc:  # noqa: BLE001
            problems.append({"entry": name, "severity": "warn", "title": f"Can't read Pangolin {name}",
                             "detail": str(exc)[:200]})
            continue
        n_sites += len(ss)
        n_online += sum(1 for s in ss if s.get("online") or s.get("type") == "local")
        n_links += len(rs)
        offline = {s.get("name") for s in ss if s.get("type") == "newt" and not s.get("online")}
        for s in sorted(offline):
            used = [r for r in rs if any(t.get("siteName") == s for t in r.get("targets") or [])
                    or any(x.get("siteName") == s for x in r.get("sites") or [])]
            if used:
                problems.append({"entry": name, "severity": "warn",
                                 "title": f"Pangolin site {s} is offline",
                                 "detail": f"{len(used)} public link(s) through it don't work, e.g. "
                                           f"{used[0].get('fullDomain')}. Its Newt isn't connected."})
    return {"state": "warn" if problems else "ok",
            "headline": f"{n_online} of {n_sites} sites online · {n_links} public links",
            "problems": problems}


def details(entries: dict[str, dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    out = []
    for name, entry in entries.items():
        try:
            ss = sites(entry, ctx)
            rs = resources(entry, ctx)
        except Exception as exc:  # noqa: BLE001
            out.append({"title": name, "entry": name, "state": "down", "facts": [str(exc)[:200]], "items": []})
            continue
        # a "local" site is Pangolin's own server (no tunnel); it has no online flag and is up
        online = {s.get("name"): bool(s.get("online")) or s.get("type") == "local" for s in ss}
        items = []
        core = getattr(ctx.fleet, "core_host", "") or "core"
        for s in ss:
            links = [r for r in rs if any(t.get("siteName") == s.get("name") for t in r.get("targets") or [])]
            up = online.get(s.get("name"))
            items.append({"id": f"site-{s.get('siteId')}", "name": f"Site {s.get('name')}",
                          "state": "up" if up else ("off" if s.get("type") != "newt" else "down"),
                          "facts": [f"{s.get('type')} · {'online' if up else 'offline'} · "
                                    f"{len(links)} link(s)"] + [str(r.get("fullDomain")) for r in links[:20]],
                          "actions": []})
        if core not in online:
            items.insert(0, {"id": "connect-core", "name": f"{core} is not a Pangolin site yet",
                             "state": "off", "facts": [f"Connect {core} to publish what Hisho hosts through Pangolin."],
                             "actions": [{"id": "connect", "label": f"connect {core}",
                                          "confirm": f"Create the site '{core}' in Pangolin {name} and run its "
                                                     f"Newt tunnel on {core}?"}]})
        for r in sorted(rs, key=lambda r: str(r.get("fullDomain") or r.get("name"))):
            ts = r.get("targets") or []
            where = ", ".join(f"{t.get('siteName') or '?'} → {t.get('ip')}:{t.get('port')}" for t in ts) or "no target"
            dead = [t for t in ts if t.get("siteName") in online and not online[t.get("siteName")]]
            items.append({"id": f"res-{r.get('resourceId')}", "name": str(r.get("fullDomain") or r.get("name")),
                          "state": "off" if not r.get("enabled", True) else "down" if dead else "up",
                          "facts": [where] + (["its site is offline"] if dead else [])
                                   + ([] if r.get("enabled", True) else ["disabled"]),
                          "actions": []})
        out.append({"title": name, "entry": name, "state": "ok",
                    "facts": [entry["url"], f"organization {org_id(entry, ctx)}"]
                             + ([entry["note"]] if entry.get("note") else []),
                    "items": items})
    return out


def test(entry: dict[str, Any], ctx: Any) -> dict[str, Any]:
    try:
        org = org_id(entry, ctx)
        ss = sites(entry, ctx)
        rs = resources(entry, ctx)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    fill = {} if entry.get("org") else {"org": org}
    return {"ok": True, "fill": fill,
            "message": f"Connected: organization {org}, {len(ss)} sites, {len(rs)} public links"}


# ── helpers other add-ons use ─────────────────────────────────────────────

def newt_env_path(entry_name: str) -> Path:
    return Path(f"~/.config/hisho/pangolin-{entry_name}-newt.env").expanduser()


def connect_core(entry_name: str, entry: dict[str, Any], ctx: Any, site_name: str | None = None) -> dict[str, Any]:
    """Make the Core machine a Pangolin site and run its Newt here (rootless Docker, user unit).
    The Newt ID and secret go straight from Pangolin into a 0600 file; they are never shown."""
    org = org_id(entry, ctx)
    site_name = site_name or ctx.fleet.core_host
    existing = next((s for s in sites(entry, ctx) if s.get("name") == site_name), None)
    env_file = newt_env_path(entry_name)
    if existing and env_file.exists():
        status = "already a site; Newt restarted"
    else:
        if existing:
            raise PangolinError(f"Pangolin already has a site named {site_name} but this machine has no Newt "
                                f"login for it. Delete or rename that site in Pangolin, then connect again.")
        d = call(entry, ctx, "GET", f"/org/{org}/pick-site-defaults")
        body = {"name": site_name, "type": "newt", "newtId": d["newtId"], "secret": d["newtSecret"],
                "subnet": d.get("subnet"), "exitNodeId": d.get("exitNodeId")}
        if d.get("clientAddress"):
            body["address"] = d["clientAddress"]
        call(entry, ctx, "PUT", f"/org/{org}/site", {k: v for k, v in body.items() if v is not None})
        env_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"PANGOLIN_ENDPOINT={str(entry['url']).rstrip('/')}\nNEWT_ID={d['newtId']}\n"
                     f"NEWT_SECRET={d['newtSecret']}\n")
        status = "site created; Newt started"
    unit = f"hisho-newt-{entry_name.lower()}.service"
    unit_path = Path(f"~/.config/systemd/user/{unit}").expanduser()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    image = entry.get("newt_image") or NEWT_IMAGE
    unit_path.write_text(f"""[Unit]
Description=Pangolin tunnel (Newt) to {entry['url']} (Hisho Pangolin integration, entry {entry_name})
After=docker.service
Wants=docker.service

[Service]
Environment=DOCKER_HOST=unix:///run/user/%U/docker.sock
ExecStartPre=-/usr/bin/docker rm -f hisho-newt-{entry_name.lower()}
ExecStart=/usr/bin/docker run --rm --name hisho-newt-{entry_name.lower()} --network host --env-file {env_file} {image}
ExecStop=/usr/bin/docker stop hisho-newt-{entry_name.lower()}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
""")
    env = {**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"}
    for cmd in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", unit],
                ["systemctl", "--user", "restart", unit]):
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        if res.returncode:
            raise PangolinError(f"{' '.join(cmd)} failed: {res.stderr.strip()[-200:]}")
    return {"ok": True, "site": site_name, "unit": unit, "message": f"{site_name}: {status}"}


def find_site(entry: dict[str, Any], ctx: Any, site_name: str) -> dict[str, Any]:
    s = next((s for s in sites(entry, ctx) if s.get("name") == site_name), None)
    if s is None:
        raise PangolinError(f"Pangolin has no site named {site_name}")
    return s


def retarget(entry: dict[str, Any], ctx: Any, resource: dict[str, Any], site_name: str, ip: str, port: int,
             method: str = "http") -> list[dict[str, Any]]:
    """Point every target of a public link at ``site_name`` → ``ip:port``."""
    site = find_site(entry, ctx, site_name)
    done = []
    for t in _targets(resource, entry, ctx):
        call(entry, ctx, "POST", f"/target/{t['targetId']}",
             {"siteId": site["siteId"], "ip": ip, "port": int(port), "method": method})
        done.append({"target": t["targetId"], "now": f"{site_name} → {ip}:{port}"})
    return done


# ── capability public_links (see hisho.addons) ───────────────────────────

def _first(entries: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    if not entries:
        raise PangolinError("no Pangolin server is set up")
    return next(iter(entries.items()))


def _find_resource(entry: dict[str, Any], ctx: Any, hostname: str) -> dict[str, Any] | None:
    return next((r for r in resources(entry, ctx) if str(r.get("fullDomain") or "").lower() == hostname.lower()), None)


def publish_link(entries: dict[str, dict[str, Any]], ctx: Any, hostname: str, address: str, port: int,
                 site: str | None = None) -> dict[str, Any]:
    """Make https://<hostname> reach <address>:<port> through the site ``site`` (default: the Core
    machine's site). Creates the public link, or repoints it if it exists."""
    _name, entry = _first(entries)
    org = org_id(entry, ctx)
    site = site or ctx.fleet.core_host
    res = _find_resource(entry, ctx, hostname)
    if res is not None:
        retarget(entry, ctx, res, site, address, int(port))
        return {"ok": True, "url": f"https://{hostname}", "message": f"{hostname} now goes to {site} → {address}:{port}"}
    domains = (call(entry, ctx, "GET", f"/org/{org}/domains") or {}).get("domains") or []
    dom = max((d for d in domains if hostname.lower() == d["baseDomain"].lower()
               or hostname.lower().endswith("." + d["baseDomain"].lower())),
              key=lambda d: len(d["baseDomain"]), default=None)
    if dom is None:
        raise PangolinError(f"Pangolin has no domain for {hostname}")
    sub = hostname[: -len(dom["baseDomain"])].rstrip(".") or None
    body = {"name": hostname, "subdomain": sub, "domainId": dom["domainId"], "mode": "http"}
    try:
        created = call(entry, ctx, "PUT", f"/org/{org}/resource", body)
    except PangolinError:  # older Pangolin: no "mode", uses http/protocol
        created = call(entry, ctx, "PUT", f"/org/{org}/resource",
                       {k: v for k, v in body.items() if k != "mode"} | {"http": True, "protocol": "tcp"})
    target_site = find_site(entry, ctx, site)
    call(entry, ctx, "PUT", f"/resource/{created['resourceId']}/target",
         {"siteId": target_site["siteId"], "ip": address, "port": int(port), "method": "http", "enabled": True})
    # Pangolin puts its own login (SSO) in front of a new resource; a public link means anyone with
    # the address can open it (e.g. a client reviewing a preview).
    call(entry, ctx, "POST", f"/resource/{created['resourceId']}", {"sso": False})
    return {"ok": True, "url": f"https://{hostname}", "message": f"{hostname} created → {site} → {address}:{port}"}


def unpublish_link(entries: dict[str, dict[str, Any]], ctx: Any, hostname: str) -> dict[str, Any]:
    _name, entry = _first(entries)
    res = _find_resource(entry, ctx, hostname)
    if res is None:
        return {"ok": True, "message": f"{hostname} was not published"}
    call(entry, ctx, "DELETE", f"/resource/{res['resourceId']}")
    return {"ok": True, "message": f"{hostname} removed"}


def link_status(entries: dict[str, dict[str, Any]], ctx: Any, hostname: str) -> dict[str, Any]:
    _name, entry = _first(entries)
    res = _find_resource(entry, ctx, hostname)
    if res is None:
        return {"exists": False, "up": False, "target": None}
    online = {s.get("name"): bool(s.get("online")) or s.get("type") == "local" for s in sites(entry, ctx)}
    ts = res.get("targets") or []
    return {"exists": True, "up": bool(ts) and all(online.get(t.get("siteName"), False) for t in ts),
            "target": ", ".join(f"{t.get('siteName')} → {t.get('ip')}:{t.get('port')}" for t in ts) or None}


def act(entries: dict[str, dict[str, Any]], ctx: Any, entry: str, item: str, action: str) -> dict[str, Any]:
    if item == "connect-core" and action == "connect":
        return connect_core(entry, entries[entry], ctx)
    return {"ok": False, "error": f"unknown action {action}"}
