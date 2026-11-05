# Content checklist (everyone)

Every team member publishes **1 article + 1 LinkedIn post**. The team publishes **1 video**.
The content guide has prompts for each; run them in Claude Code or Codex at the repo root.

> Any title, body or hashtag that mentions the word "hackathon" is disqualified. Talk about what we built.

## Article (each person, 800-1,500 words)

Pick your own angle so the 7 articles don't overlap:

| Person | Suggested angle |
| --- | --- |
| P1 Jay | Building an agent loop that trusts memory over the stack trace |
| P2 | Designing what an on-call agent should remember (retain/recall/reflect) |
| P3 | Generating 30 days of realistic pipeline failures |
| P4 | Log fingerprinting: making two different logs mean the same failure |
| P5 | Designing a diagnosis card engineers trust at 3 AM |
| P6 | Showing an agent's learning curve |
| P7 | Wiring an AI agent into Airflow's on_failure_callback |

- [ ] Title is about the idea or result, under 10 words, mentions Hindsight
- [ ] Opens with something specific (the 3 AM page), not a generic intro
- [ ] At least one real code snippet from this repo
- [ ] At least one before/after example (memory OFF vs ON)
- [ ] At least one honest lesson or dead end
- [ ] Screenshots: UI, terminal retain/recall, architecture diagram
- [ ] Links: [Hindsight GitHub](https://github.com/vectorize-io/hindsight), [Hindsight docs](https://hindsight.vectorize.io/), [agent memory](https://vectorize.io/what-is-agent-memory)
- [ ] Published publicly (Medium, Dev.to, Hashnode, Substack or LinkedIn Articles)
- [ ] Shared as a link post on r/llmdevs, r/sideproject, r/aiagents or r/aimemory

## LinkedIn post (each person)

- [ ] Posted after your article is live, with the article link
- [ ] No "hackathon" anywhere, including hashtags

## Video (team, 2-5 min, P7)

- [ ] Follows the demo script in the project plan: hook → memory OFF → memory ON → live replay → correction → Health Brief
- [ ] Shows the Hindsight UI (:9999) with the memory bank at least once
