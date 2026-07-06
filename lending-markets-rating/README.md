# Lending Markets Rating CLI

CLI for extracting evidence from Morpho, Aave, Spark and Maple markets and, optionally, sending that evidence to an LLM for scoring.

## Installation

```bash
yarn install
```

## Usage

The main commands are `morpho`, `aave`, `spark` and `maple`.

```bash
yarn morpho <chain-id> <market-id>
```

Example:

```bash
yarn morpho 1 0x7e585a933ffe8443c371b4f8cfeb4430f5f6a14c2f32a898c26662c67a1cb8b8
```

This prints a market evidence report.

### Aave V3

```bash
yarn aave <chain-id> <address>
```

`<address>` can be:
- the underlying asset address
- the aToken address
- the market/pool address

Example:

```bash
yarn aave 1 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
```

This prints an Aave V3 market evidence report using the public subgraph.

### SparkLend

```bash
yarn spark <chain-id> <address>
```

### Maple

```bash
yarn maple <chain-id> <pool-address>
```

Example:

```bash
yarn maple 1 0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b
```

## Options

- `--output <text|json|graph>`: output format.
  - default: `text`
- `--score`: after printing the evidence, send the report to an LLM for scoring.
  - currently available for Morpho, Aave and Spark reports
- `--llm <claude|openai>`: LLM provider for scoring and graph dependency expansion.
  - default: `openai`

Example with scoring:

```bash
ANTHROPIC_API_KEY=... yarn morpho --score --llm claude 1 0x7e585a933ffe8443c371b4f8cfeb4430f5f6a14c2f32a898c26662c67a1cb8b8
```

Example using OpenAI:

```bash
OPENAI_API_KEY=... yarn morpho --score --llm openai 1 0x7e585a933ffe8443c371b4f8cfeb4430f5f6a14c2f32a898c26662c67a1cb8b8
```

JSON output:

```bash
yarn morpho --output json 1 0x7e585a933ffe8443c371b4f8cfeb4430f5f6a14c2f32a898c26662c67a1cb8b8
```

Dependency graph output:

```bash
yarn morpho --output graph --llm openai 1 0x7e585a933ffe8443c371b4f8cfeb4430f5f6a14c2f32a898c26662c67a1cb8b8
```

The graph output prints a tree view in the terminal. It starts with the market/pool as the root node, connects it to the loan asset, protocol, and current collaterals, and uses the selected LLM to recursively expand non-leaf token dependencies.

## What It Prints

By default the CLI prints:

1. Basic market identity
2. Anchors evidence
   - market solvency
   - withdrawal liquidity
3. Control modifiers evidence
   - collateral dependency robustness
   - collateral and liquidity diversification

If `--score` is enabled, it also prints:

- score for each anchor
- PD for each anchor
- conclusion for each anchor
- multiplier for each control modifier
- conclusion for each control modifier

The score is independent per block. The CLI does not combine those values into a single final score.

## Supported Chains

The `chain-id` must be numeric. The default supported chains are:

- `1` Ethereum
- `10` OP Mainnet
- `130` Unichain
- `137` Polygon
- `42161` Arbitrum One
- `8453` Base

## Environment Variables

The CLI loads `.env` automatically from the project root.

### Scoring with Claude

```bash
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-20250514
```

### Aave V3 subgraph

```bash
THE_GRAPH_API_KEY=...
```

### Scoring with OpenAI

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.5
```

## Scripts

```bash
yarn lint
yarn typecheck
yarn build
yarn morpho --help
yarn aave --help
yarn spark --help
yarn maple --help
```

## Notes

- The CLI does not use a database or local file storage.
- The output is meant as input evidence for later evaluation.
- The `--score` mode is optional.
- Aave V3 uses The Graph subgraphs.
- Maple currently uses on-chain pool accounting plus the public Maple GraphQL API for Syrup pool discovery and withdrawal queue data.
