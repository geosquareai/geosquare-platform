# Security policy

## Report a vulnerability

Do not open a public issue for a security problem. Report it in private:

**https://github.com/geosquareai/geosquare-platform/security/advisories/new**

Private vulnerability reporting is turned on for this repository. Only the maintainers can read the report.

Include these details if you can:

- what the problem is and what an attacker can do with it;
- the steps to reproduce it, or a minimal request that shows it;
- the commit or version you tested (`geosquare-platform` commit, or the `geosquare-platform` and `geosquare-engine` versions);
- whether real data is affected.

Do not send real personal data in a report. Use synthetic data to show the problem.

## Scope

In scope: the code in this repository. That includes the HTTP API, the upload checks, the export, the internal route gate, and the browser map page.

Other GeoSquare packages have their own repositories. Report a problem in `geosquare-engine` or `geosquare-v2` in the repository where the code lives.

## Known limits

These limits are documented. They are not secret, but a report that shows real harm is still welcome:

- `GSQ_INTERNAL_ROUTES=1` is an environment switch. It is not authentication.
- The public routes `/datasets/{id}/cells` and `/cells/{gid}/profile` return values per cell and do not check `share_policy`. See [Privacy and internal routes](docs/privacy-and-internal-routes.md).
