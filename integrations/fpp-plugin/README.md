# FacadeKit Projectors (FPP plugin)

FPP 10.4+ plugin that switches PJLink class 1 projectors on and off from FPP Commands,
playlists and the scheduler, shows their state, and lists the shows FacadeKit published.
PHP and shell only; installs nothing from the network.

- Design, security, install and what is verified: [docs/20-fpp-plugin.md](../../docs/20-fpp-plugin.md)
- User guide: [docs/user/fpp-plugin.md](../../docs/user/fpp-plugin.md)
- Install on a sandbox Pi from this checkout: `python3 dev/serve_dev_repo.py`
- Fake projectors for testing: `cd studio/backend && uv run facadekit fpp fake-pjlink --host 0.0.0.0`
