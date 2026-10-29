# Changelog

## 0.2.0

- The API key's help link opens Pangolin's new-key page for your organization (`{url}/{org}/settings/api-keys/create`, filled in from what you typed), and the form asks for the Organization ID before the key. Pangolin makes keys only on its dashboard (its integration API needs a key to start with), so there is no sign-in-once.

## 0.1.2

- Every field on its setup form says in plain words what it is, and links to where to get it (addon.yaml `help:`, shown by Hisho's dashboard).

## 0.1.1

- README says what you need (addon.yaml you_need) and how to use it; the README check requires both.
- Neutral sample names in tests and comments.

## 0.1.0

- Moved out of Hisho into its own repository (hisho-addon-pangolin); installed from the add-on store.

