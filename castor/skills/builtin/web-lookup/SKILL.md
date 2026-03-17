---
name: web-lookup
description: >
  Use when the user asks about something the robot doesn't know from its local
  knowledge — facts, current events, how-to questions, product specs, news,
  definitions, or anything requiring up-to-date information from the internet.
  Also use when the user asks to search, look up, find information, or research a topic.
version: "1.0"
requires: []
consent: none
tools:
  - web_search
max_iterations: 3
---

# Web Lookup Skill

Use this skill to answer questions that require current or external information.

## Steps

1. Call `web_search(query)` with a concise, targeted search query derived from the user's question
2. Review the returned results (title + snippet)
3. Synthesise a clear, grounded answer citing the most relevant result
4. If results are poor quality, try one follow-up search with a refined query

## Guidelines

- Keep queries short and specific — avoid full sentences
- Prefer authoritative sources (official docs, Wikipedia, reputable news)
- If no useful results found: say so honestly, don't fabricate
- Do NOT use this skill for questions about the robot's own sensors or status — use `get_telemetry` for those
- Cite your source: "According to [title]..."

## Example

User: "What is a Feetech STS3215 servo?"
→ `web_search("Feetech STS3215 servo specifications")`
→ Summarise: torque, voltage, protocol, use cases
