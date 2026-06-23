ALWAYS read AGENTS.md in every folder.

Use `just <recipe>` for all build, deploy, start, and data operations — never invoke npm/pnpm/go/python scripts directly. Run `just --list` to see available recipes.

To start / stop / inspect scrapers and pipeline services, ALWAYS use `just scraper-start`, `just scraper-stop`, `just scraper-status` — never launch scrapers, the tagger, or pipeline daemons by hand (nohup/tmux/python). `scraper-status` is the single source of truth for what is running; check it before starting anything and after stopping. `scraper-start` does a clean restart (kills strays first).

After modifying any frontend UI code: run `just build` to build the frontend, then use the `agent-browser` skill to open the built app and verify the changed features are present.
