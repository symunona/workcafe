ALWAYS read AGENTS.md in every folder.

Use `just <recipe>` for all build, deploy, start, and data operations — never invoke npm/pnpm/go/python scripts directly. Run `just --list` to see available recipes.

After modifying any frontend UI code: run `just build` to build the frontend, then use the `agent-browser` skill to open the built app and verify the changed features are present.
