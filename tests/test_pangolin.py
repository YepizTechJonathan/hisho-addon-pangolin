"""Pangolin add-on tests. Run with Hisho's venv at the repository's top:
~/code/hisho/.venv/bin/python -m pytest -q"""
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from hisho import addon_store, addons
from hisho.addon_testing import AUTH, TOKEN, Recorder

HERE = Path(__file__).resolve().parents[1]


def ctx_for(addon):
    return addons.Context(addon, resolve=lambda name: f"value-of-{name}")


def test_manifest_is_valid_and_readme_matches(addon):
    assert addons.validate_manifest(addon.manifest) == []
    assert addon_store.readme_problems(HERE) == []


FACTORY = {
    "name": "site-builder", "title": "Site Builder", "host": "kihon",
    "options": {"preview_domain": "example.dev"},
    "schema": {"preview_domain": "str", "worker_token": "secret", "builder": "list(claude|hermes)",
               "worker_port": "port?"},
    "requires": {"machines": ["kihon"]},
    "services": [{"unit": "site-builder-worker.service", "label": "Factory worker"}],
    "checks": [{"name": "router", "http": "http://127.0.0.1:8084/", "header": "Host: probe.{preview_domain}",
                "from": "host"}],
    "actions": {"build": {"label": "Build a dev site", "params": ["site"], "run": "bin/build {site}"},
                "tidy": {"run": "bin/tidy", "background": True}},
}


def configure(**values):
    addons.write_config("site-builder", {"worker_token": "HISHO_WEBFACTORY_TOKEN", "builder": "claude", **values})


def _fake_pangolin(monkeypatch, state):
    import io, json as _json
    import urllib.error

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=0):
        path = req.full_url.split("/v1", 1)[1]
        state["calls"].append((req.get_method(), path, _json.loads(req.data) if req.data else None))
        assert req.headers["Authorization"] == "Bearer the-key"
        if path == "/orgs":
            data = {"orgs": [{"orgId": "acme"}]}
        elif path.startswith("/org/acme/sites"):
            data = {"sites": state["sites"]}
        elif path.startswith("/org/acme/resources"):
            data = {"resources": state["resources"]}
        elif path == "/org/acme/pick-site-defaults":
            data = {"newtId": "nid", "newtSecret": "nsecret", "subnet": "100.64.0.28/29", "exitNodeId": 1}
        elif path == "/org/acme/site":
            state["sites"].append({"siteId": 9, "name": "kihon", "type": "newt", "online": False})
            data = {"siteId": 9}
        elif path.startswith("/target/"):
            data = {}
        elif path == "/org/acme/domains":
            data = {"domains": [{"domainId": "d1", "baseDomain": "example.dev"}, {"domainId": "d2", "baseDomain": "example.com"}]}
        elif path == "/org/acme/resource":
            state["resources"].append({"resourceId": 50, "fullDomain": "new.example.dev", "enabled": True, "targets": []})
            data = {"resourceId": 50}
        elif path.startswith("/resource/50/target"):
            state["resources"][-1]["targets"].append({"targetId": 70, "ip": "127.0.0.1", "port": 8090, "siteName": "kihon"})
            data = {"targetId": 70}
        elif path.startswith("/resource/") and req.get_method() in ("DELETE", "POST"):
            data = {}
        else:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return Resp(_json.dumps({"data": data, "success": True}).encode())
    monkeypatch.setattr("urllib.request.urlopen", urlopen)


def test_the_pangolin_integration_reads_sites_links_and_connects_core(fleet, tmp_path, monkeypatch):
    pg = addons.get("pangolin")
    addons.set_entry(pg, "offsite", {"url": "https://pangolin.example.com", "api_url": "https://api.example.com/v1",
                                   "api_key": "HISHO_PG_KEY"})
    state = {"calls": [], "sites": [{"siteId": 6, "name": "Deployarr", "type": "newt", "online": False},
                                    {"siteId": 7, "name": "media", "type": "newt", "online": True}],
             "resources": [{"resourceId": 1, "fullDomain": "a.example.dev", "enabled": True,
                            "targets": [{"targetId": 11, "ip": "198.51.100.7", "port": 8088, "siteName": "Deployarr"}]},
                           {"resourceId": 2, "fullDomain": "tv.example.com", "enabled": True,
                            "targets": [{"targetId": 12, "ip": "10.0.0.2", "port": 8096, "siteName": "media"}]}]}
    _fake_pangolin(monkeypatch, state)
    ctx = addons.Context(pg, resolve=lambda n: "the-key")
    s = addons.summarize(pg, ctx)
    assert s["headline"] == "1 of 2 sites online · 2 public links"
    assert [p["title"] for p in s["problems"]] == ["Pangolin site Deployarr is offline"]
    [sec] = addons.details(pg, ctx)
    names = [i["name"] for i in sec["items"]]
    assert names[0] == "kihon is not a Pangolin site yet" and "a.example.dev" in names
    assert next(i for i in sec["items"] if i["name"] == "a.example.dev")["state"] == "down"
    assert addons.test_entry(pg, "offsite", {}, ctx)["fill"] == {"org": "acme"}
    # connect the Core machine: site created, Newt login written 0600, unit installed and started
    monkeypatch.setenv("HOME", str(tmp_path))
    ran = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    res = addons.act(pg, "offsite", "connect-core", "connect", ctx)
    assert res["ok"] and res["site"] == "kihon", res
    env = tmp_path / ".config/hisho/pangolin-offsite-newt.env"
    assert oct(env.stat().st_mode & 0o777) == "0o600" and "NEWT_SECRET=nsecret" in env.read_text()
    assert ("PUT", "/org/acme/site", {"name": "kihon", "type": "newt", "newtId": "nid", "secret": "nsecret",
                                      "subnet": "100.64.0.28/29", "exitNodeId": 1}) in state["calls"]
    assert ran[-1] == ["systemctl", "--user", "restart", "hisho-newt-offsite.service"]
    unit = (tmp_path / ".config/systemd/user/hisho-newt-offsite.service").read_text()
    assert "nsecret" not in unit and "--env-file" in unit
    # move a link to the new site
    mod = addons.provider(pg)
    done = mod.retarget(addons.entries(pg)["offsite"], ctx, state["resources"][0], "kihon", "127.0.0.1", 8084)
    assert done == [{"target": 11, "now": "kihon → 127.0.0.1:8084"}]
    assert state["calls"][-1] == ("POST", "/target/11", {"siteId": 9, "ip": "127.0.0.1", "port": 8084, "method": "http"})


def test_public_links_are_a_capability_pangolin_provides(fleet, tmp_path, monkeypatch, stand_in):
    m = dict(FACTORY, requires={"machines": ["kihon"], "capabilities": ["public_links"]})
    stand_in(**m)
    configure()
    assert "needs an add-on that provides public links (e.g. Pangolin), turned on" in addons.problems(addons.get("site-builder"))
    with pytest.raises(addons.AddonError, match="no add-on that provides public links"):
        addons.capability("public_links")
    pg = addons.get("pangolin")
    addons.set_entry(pg, "offsite", {"url": "https://pangolin.example.com", "api_url": "https://api.example.com/v1",
                                   "api_key": "HISHO_PG_KEY", "org": "acme"})
    addons.enable("pangolin")
    assert addons.problems(addons.get("site-builder")) == []
    addon, mod, ents = addons.capability("public_links")
    assert addon.name == "pangolin" and "offsite" in ents
    state = {"calls": [], "sites": [{"siteId": 9, "name": "kihon", "type": "newt", "online": True}],
             "resources": [{"resourceId": 1, "fullDomain": "old.example.dev", "enabled": True,
                            "targets": [{"targetId": 11, "ip": "198.51.100.7", "port": 8084, "siteName": "Deployarr"}]}]}
    _fake_pangolin(monkeypatch, state)
    ctx = addons.Context(addon, resolve=lambda n: "the-key")
    r = mod.publish_link(ents, ctx, "new.example.dev", "127.0.0.1", 8090)
    assert r["ok"] and r["url"] == "https://new.example.dev"
    put = next(c for c in state["calls"] if c[1] == "/org/acme/resource")
    assert put[2] == {"name": "new.example.dev", "subdomain": "new", "domainId": "d1", "mode": "http"}
    assert ("PUT", "/resource/50/target", {"siteId": 9, "ip": "127.0.0.1", "port": 8090, "method": "http", "enabled": True}) in state["calls"]
    assert ("POST", "/resource/50", {"sso": False}) in state["calls"]   # public: no Pangolin login in front
    assert mod.link_status(ents, ctx, "new.example.dev") == {"exists": True, "up": True, "target": "kihon → 127.0.0.1:8090"}
    r = mod.publish_link(ents, ctx, "old.example.dev", "127.0.0.1", 8084)   # existing link: repointed
    assert "now goes to kihon" in r["message"]
    assert mod.unpublish_link(ents, ctx, "nope.example.dev")["message"] == "nope.example.dev was not published"



def test_the_key_help_links_straight_to_pangolins_new_key_page(addon):
    h = addons.field_help(addon)
    assert h["api_key"]["get"] == "{url}/{org}/settings/api-keys/create"
    form = list(addon.manifest["entry_form"])
    assert form.index("org") < form.index("api_key")          # typed first, so the link can be filled in
    assert "connect" not in addon.manifest and "discover" not in addon.manifest
    assert addons.validate_manifest(addon.manifest) == []
