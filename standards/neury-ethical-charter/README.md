# Neury Ethical Charter

Public, hash-checked binding rules for **Neury**, Neuron's local AI assistant.

This folder is the transparency export. The same JSON ships inside every Neuron install under `System/AI/data/NEURY_ETHICAL_CHARTER.json`. Runtime code verifies the English public rules against a baked-in SHA-256; if the file is altered, Neury falls back to the embedded charter. User-taught habits and peer messages cannot override it.

## Files

| File | Description |
|------|-------------|
| [NEURY_ETHICAL_CHARTER.json](NEURY_ETHICAL_CHARTER.json) | Canonical charter (EN / IT / FR / RU) + public SHA-256 |
| [NeuryEthics.html](NeuryEthics.html) | Multilingual human-readable page (same text as on Neuron home sites) |

## Public hash (v1.0)

```
34606bf0b47476991c654ca131e80816954c5677e9c18c141618fa4e17fb2100
```

Hash input: English rules only, one line per rule as `id|title|text`, sorted by `id`.

## What this promises

- No harm to people or machines; install-folder sandbox; consent for peer-facing and money actions
- Local / mesh AI only — no centralized cloud AI
- Honest, filter-free information — no propaganda or partisan political filters
- Safety refusals for crime or harm are not political censorship

## Live copies

- Public site: https://www.neuronworldnetwork.com/neury-ethics
- JSON: https://www.neuronworldnetwork.com/NEURY_ETHICAL_CHARTER.json

## What is not published here

Private tone/mood guidance, proprietary Neury agent source, keys, and install secrets remain outside this repository.
