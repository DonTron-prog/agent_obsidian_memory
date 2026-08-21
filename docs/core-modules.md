# Core module architecture

The project has two access paths to one shared Markdown vault: agents use the managed memory service, while the user reads and edits through Obsidian.

```mermaid
flowchart LR
    user["User"]
    obsidian["Obsidian"]
    agents["Pi and Hermes<br/>agents and adapters"]
    memory["Memory service<br/>search · validation · transactions"]
    operations["Operations<br/>audit · checkpoints · Git history"]
    serverVault["Shared Markdown vault<br/>concepts · indexes · sessions"]
    localVault["Local Obsidian vault"]

    user --> obsidian
    user --> agents

    agents -->|"search and open files"| memory
    agents -->|"create and edit files"| memory
    memory <-->|"managed read and write"| serverVault
    memory --> operations
    operations --> serverVault

    obsidian <-->|"read and direct edit"| localVault
    localVault <-->|"Syncthing"| serverVault

    user -.->|"reconcile direct edits"| memory
```

Agent changes pass through the memory service for validation, auditing, and Git history. Obsidian edits synchronize directly and enter managed history when the user runs `memory reconcile`.
