---
name: find-skills
description: Helps users discover and install agent skills when they ask questions like "how do I do X", "find a skill for X", "is there a skill that can...", or express interest in extending capabilities. This skill should be used when the user is looking for functionality that might exist as an installable skill. Also use this when the user asks about trading, risk management, WebSocket debugging, or multi-exchange crypto trading capabilities.
---

# Find Skills

This skill helps you discover and install skills from the open agent skills ecosystem.

## Project Context (grid-trading-console)

This project has **27 active skills** in `.agents/skills/`. Before searching externally, check if an existing local skill already covers the need:

### Currently Installed Skills
| Category | Skills |
|---|---|
| **Trading & Strategy** | `strategy-development`, `order-execution-debug`, `position-lifecycle`, `risk-audit`, `config-tuning-guide` |
| **Infrastructure & Exchange** | `websocket-debug`, `api-diagnostics`, `db-migration`, `stability-check`, `traffic-audit` |
| **Operations & Troubleshooting** | `deployment-check`, `production-troubleshooting`, `log-analysis`, `reconciler-debug`, `ssh-tunnel` |
| **Development & Quality** | `dashboard-enhancement`, `code-quality`, `test-generation`, `version-audit`, `release-gate` |
| **UI/UX Reference** | `ui-ux-pro-max` |
| **Agent Meta & Utilities** | `ponytail`, `ponytail-debt`, `ponytail-help`, `skill-creator`, `find-skills`, `graphify` |

### When External Skills May Help
- **Document processing**: generating reports as PDF/DOCX/XLSX (e.g., `anthropics/skills@pdf`, `anthropics/skills@xlsx`)
- **Frontend/UI**: advanced visualization or animation libraries
- **Testing**: end-to-end testing frameworks (e.g., Playwright)
- **DevOps/CI**: Docker orchestration, advanced cloud deployment monitoring

## When to Use This Skill

Use this skill when the user:

- Asks "how do I do X" where X might be a common task with an existing skill
- Says "find a skill for X" or "is there a skill for X"
- Asks "can you do X" where X is a specialized capability
- Expresses interest in extending agent capabilities
- Wants to search for tools, templates, or workflows

## What is the Skills CLI?

The Skills CLI (`npx skills`) is the package manager for the open agent skills ecosystem. Skills are modular packages that extend agent capabilities with specialized knowledge, workflows, and tools.

**Key commands:**

- `npx skills find [query]` - Search for skills interactively or by keyword
- `npx skills add <package>` - Install a skill from GitHub or other sources
- `npx skills check` - Check for skill updates
- `npx skills update` - Update all installed skills

**Browse skills at:** https://skills.sh/
