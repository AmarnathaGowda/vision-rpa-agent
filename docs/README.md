# Vision RPA Agent — Project Overview

## What This Is

A dynamic, vision-driven RPA agent that observes the screen, reasons about the current state, and decides the next action — rather than following a fixed predefined script.

Targets: browser applications, Windows desktop apps, RDP/RemoteApp sessions, File Explorer, and shared network drives.

## Why This Approach

Traditional fixed-pipeline automation breaks on any unexpected UI change.
This agent adapts — it sees what is on screen, understands it via a Vision Language Model, and decides what to do next. If it cannot decide with sufficient confidence, it routes to a human reviewer.

## Project Structure

```
vision-rpa-agent/
├── docs/                        ← you are here
│   ├── README.md                ← project overview
│   ├── tech-stack.md            ← technology decisions
│   ├── vm-setup.md              ← VM requirements and configuration
│   ├── architecture.md          ← system design and modules
│   ├── roadmap.md               ← development phases and timeline
│   ├── todo.md                  ← current task tracking
│   └── dependencies.md          ← packages, tools, installation
├── agent/                       ← core agent loop
├── executors/                   ← browser, desktop, RDP, file executors
├── memory/                      ← working, session, long-term memory
├── hitl/                        ← human-in-the-loop queue and UI
├── config/                      ← settings, task definitions
├── tests/                       ← unit and integration tests
└── run_agent.py                 ← entry point
```

## Quick Reference

| Topic | Document |
|-------|----------|
| What technologies to use | [tech-stack.md](tech-stack.md) |
| Setting up development VM | [vm-setup.md](vm-setup.md) |
| How the system is designed | [architecture.md](architecture.md) |
| What to build and when | [roadmap.md](roadmap.md) |
| What to work on right now | [todo.md](todo.md) |
| How to install everything | [dependencies.md](dependencies.md) |
| Feasibility analysis + POC case review | [feasibility-analysis.md](feasibility-analysis.md) |

## Access Model

```
Development:   Agent runs on local Windows laptop
               ↓
               Playwright → RD Web browser (HTTPS)
               mstsc.exe  → RDP / RemoteApp session
               pywinauto  → desktop windows inside RDP

Testing/Demo:  Agent runs inside Agent VM
               → accesses LD and IIM via RDP session into App VM

Production:    Same as Testing/Demo, scaled to N VMs
               (or Model A: direct network access, if IT permits)
```

## MVP Scope

- 3 agents running simultaneously and independently
- Handles browser + RDP + desktop + file operations
- Dynamic decision making — no hardcoded step sequences
- Human approval gates on all write/submit actions
- Full audit log per task
