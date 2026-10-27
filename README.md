# Pangolin (Hisho add-on)

## What it does

Shows your Pangolin servers (self-hosted tunnelled reverse proxy): their sites, online or not, and
every public link (resource) with where it points. It can connect the Core machine as a Pangolin
site of its own (runs Newt there), so anything Hisho hosts can be published. It provides the
`public_links` capability: other add-ons (the Web Factory) publish links through it without
naming Pangolin.

## What it needs

- You need:
  - A Pangolin server on a VPS outside your home network (Pangolin itself is not installed by this add-on)
  - A Pangolin API key (Sites, Resources, Targets, Domains)
- Runs on: the Core machine, inside Hisho Core
- One entry per Pangolin server, each with:
  - `url` (url, required)
  - `api_url` (url, optional)
  - `api_key` (password, kept as a local secret, required)
  - `org` (str, optional)
  - `newt_image` (str, optional)
  - `note` (str, optional)
- Provides: `public_links`

## How to install

On the dashboard: **Add-ons > Store > Pangolin > Install**. Or from a terminal:

```bash
hisho addon install pangolin
```

Hisho takes the newest version from this repository and turns it on inside Hisho Core. Updates show as **Update
available** on its Store card; **Roll back** returns to the version before.

## How to use

Its card shows each Pangolin server's sites and public links, online or not. On its page (**Add-ons > Pangolin**), **connect this machine** runs Newt, Pangolin's small tunnel client, on the Core machine, so what Hisho hosts can get a public link; only Newt runs at home. Other add-ons publish links through it (it provides `public_links`).

## Settings

One entry per Pangolin server: its dashboard address, its API address (often
`https://pangolin-api.<your domain>/v1`), an API key and the organization's ID. Make the key in
Pangolin: Organization > Settings > API Keys, with the Sites, Resources, Targets and Domains
permissions. Entries are kept in `~/Hisho/System/Config/Addons/pangolin.yaml`. To rotate: Pangolin >
Settings > API Keys (make a new key, delete the old).

## How to remove

**Add-ons > Store > Pangolin > Remove**, or `hisho addon remove pangolin`. Its card and page go away; your settings stay in `~/Hisho/System/Config/Addons/pangolin.yaml`, so installing it again picks up where you left off.

## License

Source-available under the [PolyForm Shield License 1.0.0](LICENSE.md): free to use, change and share,
for any purpose except offering a product that competes with Hisho (reselling it, for example).
