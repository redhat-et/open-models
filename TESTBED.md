# TESTBED.md — manual test checklist

How to run this as a "real user": create a fresh OS user (or use any new
teammate), give them the repo script, work the list.

```bash
sudo useradd -m omtest
sudo cp -f scripts/setup-user.sh /home/omtest/
sudo chown omtest:omtest /home/omtest/setup-user.sh
sudo su - omtest
./setup-user.sh                     # pastes key when prompted
```

## Setup script
- [ ] Cold run as fresh user: smoke test passes, opencode installs,
      `~/.config/octo/key` exists with mode 600, config written
- [ ] Bad key (`--key sk-bogus`) aborts BEFORE touching any dotfiles
- [ ] Re-run is idempotent (no duplicate .bashrc block, config backed up once)
- [ ] `./setup-user.sh --check` on a good setup: "setup looks good"

## opencode through the gateway
- [ ] `/models` shows *Qwen 3.8 Flash Next* + *Qwen 3.8 27B (fast)*
- [ ] A real coding task completes on flash (default model)
- [ ] `/model` switch to fast works (needs hot aisle on, or it runs on the 5090)
- [ ] Reboot the machine → `opencode` works with no env exports (key is read
      from file)

## Portal (private browser window → GitHub login)
- [ ] Login round-trip works; avatar shows
- [ ] Create key → shown once → list + delete work
- [ ] Spend chip ticks up after requests (needs ≥1 priced call)
- [ ] Model cards show copyable snippets; `listed` cards (spark2) direct-URL

## Gateway behavior
- [ ] Kill/stop a fast endpoint → requests still succeed via fallback
      (fast group has the 5090 + hot aisle; never stop both)
- [ ] Delete your key → immediate 401 from the gateway
- [ ] Hammer >600 rpm briefly → expect 429s, then recovery
- [ ] Prometheus: gateway + all `running` deployment targets green on the
      prom route; down target (parked box) shows without breaking anything

## Observability
- [ ] Each call shows up in `/key/info` spend (portal chip, or admin API)
- [ ] Grafana: traffic visible per model; panels owned by Matt for deep dives

## Notes
- Key minted for the testbed user is tagged `github_user=<user>`; it will NOT
  appear in your own portal list — check spend via litellm admin API instead.
- Hot aisle power: `./scripts/hotaisle.sh serve|wait|status|off`
- Lab (no hardware needed): `make lab` then `make lab-test`
