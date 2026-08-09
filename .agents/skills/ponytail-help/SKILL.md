---
name: ponytail-help
description: >
  Quick-reference card for all ponytail modes, skills, and commands.
  One-shot display, not a persistent mode. Trigger: "ponytail help",
  "what ponytail commands", "how do I use ponytail", "ponytail 指令",
  "ponytail 怎麼用".
---

# Ponytail Help

Display this reference card when invoked. One-shot, do NOT change mode,
write flag files, or persist anything.

## Levels

| Level | Trigger | What changes |
|-------|---------|-------------|
| **Lite** | say "ponytail lite" | Build what's asked, name the lazier alternative in one line. |
| **Full** | say "ponytail" | The ladder enforced: YAGNI → stdlib → native → one line → minimum. Default. |
| **Ultra** | say "ponytail ultra" | YAGNI extremist. Deletion before addition. Challenges requirements before building. |

Level sticks until changed or session end.

## Skills

| Skill | Trigger | What it does |
|-------|---------|--------------
| **ponytail** | "ponytail" / "lazy mode" / "極簡模式" | Lazy mode itself. Simplest solution that works. |
| **code-quality** (over-engineering review) | "review for over-engineering" / "ponytail review" | One-line-per-finding review: `L42: yagni: factory, one product. Inline.` |
| **code-quality** (repo-wide audit) | "audit for bloat" / "ponytail audit" | Full repo scan for what to delete, simplify, or replace with stdlib. |
| **ponytail-debt** | "ponytail debt" / "技術債" | Harvest `ponytail:` comments into a tracked debt ledger. |
| **ponytail-help** | "ponytail help" / "ponytail 指令" | This card. |

## Always-On Rule

`rule-07-ponytail` in `.agents/rules/README.md` is always active. The YAGNI
ladder and `ponytail:` comment convention apply to every response automatically.
No need to trigger the ponytail skill unless you want a specific intensity level.

## Deactivate

Say "stop ponytail" or "normal mode". Resume anytime with "ponytail".

## More

The ponytail philosophy: the shortest path to done is the right path.
Original project: https://github.com/DietrichGebert/ponytail
