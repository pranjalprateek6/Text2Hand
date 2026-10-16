# Working on Text2Hand

## Shipping a change

Every change goes out the same way, without waiting to be asked:

1. Branch off `main`, commit, push, open a PR.
2. Wait for CI (lint, types, suite on 3.10/3.12/3.13, Docker build and container smoke test) to pass, then merge it.
3. Deploy what was merged, from an up-to-date `main`:

   ```
   git checkout main && git pull
   fly deploy --ha=false
   ```

4. Check the live site, and say it is live only after this passes:

   ```
   python tests/smoke.py https://text2hand.fly.dev
   ```

If the deploy or the smoke test fails, report what broke instead of calling it done. Deploy only from `main`, never from a feature branch.

### Deploy rules

- **Always `--ha=false`, and never scale past one machine.** Jobs and their progress live in the one server process's memory (see `wsgi.py`), so a second machine would answer polls for jobs it never started.
- The app is `text2hand` on Fly.io, region `sin`; its config is `fly.toml`. Fly no longer accepts new machines in Mumbai (`bom`).
- `flyctl` is at `%USERPROFILE%\.fly\bin`. If a command says no access token is available, run `fly auth login` in the background and give the owner the approval URL it prints.
- The machine stops when idle, so the first request after a quiet spell takes a few seconds; that is expected, not a failure.

## Testing

- Run `python tests/regression.py` before every commit. Visual changes to the handwriting are expected to fail the reference-picture check; look at the new output, then run `python tests/golden_check.py --update`.
- When testing in a browser, run the app on port 5077 (`T2H_PORT=5077 python app.py`), never 5000: another project uses that port on this machine.

## Commits

- Do not add a `Co-Authored-By: Claude` trailer.
