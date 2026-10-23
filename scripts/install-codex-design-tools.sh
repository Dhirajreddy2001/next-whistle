#!/usr/bin/env bash
set -euo pipefail

# Installs the requested Codex design plugins/skills in the current user's
# Codex configuration. Run outside a restricted Codex sandbox.

printf 'Installing daisyUI Codex plugin...\n'
codex plugin marketplace add saadeghi/daisyui --sparse .agents/plugins
codex plugin add daisyui@daisyui

printf 'Installing MagicPath Codex plugin...\n'
codex plugin marketplace add MagicPathAI/agent-skills
codex plugin add magicpath@magicpath

printf 'Installing OpenDesign/Taste skills...\n'
npx --yes skills add https://github.com/Leonxlnx/taste-skill --skill design-taste-frontend
npx --yes skills add https://github.com/Leonxlnx/taste-skill --skill minimalist-ui

printf 'Installing Better Design skill and MCP server...\n'
npx --yes skills add marvkr/better-design --skill better-design
npx --yes better-design

printf '\nInstalled Codex marketplace plugins:\n'
codex plugin list
printf '\nRestart Codex so newly installed plugins, skills, and MCP tools are loaded.\n'
