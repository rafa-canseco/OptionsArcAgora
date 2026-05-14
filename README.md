# Arc/Circle Developer Environment

This repo tracks the local Arc/Circle setup for the Agora Agents hackathon work.

## Verified Environment

Verified on 2026-05-12:

- `uv` is installed: `uv 0.4.17`
- Arc Canteen CLI installed with:

```sh
uv tool install git+https://github.com/the-canteen-dev/ARC-cli
```

- Installed executable: `arc-canteen`
- Arc Canteen context bundle synced to `~/.arc-canteen/context`:

```sh
arc-canteen context sync
arc-canteen context --paths
```

- Public Arc Testnet RPC responds locally:

```sh
curl -s https://rpc.testnet.arc.network \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
```

Expected result:

```json
{"jsonrpc":"2.0","id":1,"result":"0x4cef52"}
```

`0x4cef52` is decimal `5042002`.
- Canteen keyed RPC profile/login completed and saved locally to `~/.arc-canteen/env`.
- Canteen keyed RPC verified locally:

```sh
source ~/.arc-canteen/env
cast chain-id --rpc-url "$RPC"
cast block-number --rpc-url "$RPC"
arc-canteen status
```

Expected `cast chain-id` result:

```text
5042002
```

- Circle CLI installed and authenticated on testnet.
- Arc Testnet Circle agent wallet verified:

```text
0xc2b1fdbc40c6d0d1fc6fd25a2a07ae05fdc15ce2
```

- Circle faucet funded wallet with 20 native USDC on Arc Testnet.
- Basic Arc Testnet transaction submitted through Circle Wallets:

```text
Transaction hash: 0x64b4f0835a7045c7cb055a9366ff833d122562b578d84a88a6cfa67766e8f7f9
Block: 41912835
State: COMPLETE
Amount: 0.01 native USDC
Network fee: 0.0071343735 USDC
```

Explorer:

```text
https://testnet.arcscan.app/tx/0x64b4f0835a7045c7cb055a9366ff833d122562b578d84a88a6cfa67766e8f7f9
```

## Network

| Field | Value |
| --- | --- |
| Network | Arc Testnet |
| Chain ID | `5042002` |
| Native gas token | USDC |
| Public RPC | `https://rpc.testnet.arc.network` |
| Canteen keyed RPC | `https://rpc.testnet.arc-node.thecanteenapp.com/v1/<key>` |
| Explorer | `https://testnet.arcscan.app` |

## Setup

1. Install `uv` if needed.
2. Install the Arc Canteen CLI:

```sh
uv tool install git+https://github.com/the-canteen-dev/ARC-cli
```

3. Login to Canteen to mint a personal RPC key:

```sh
arc-canteen login
```

4. Export the keyed Canteen RPC URL:

```sh
eval "$(arc-canteen rpc-url --export)"
```

For every new shell, add this to `~/.zshrc` or `~/.bashrc`:

```sh
[ -f ~/.arc-canteen/env ] && . ~/.arc-canteen/env
```

5. Copy `.env.example` to `.env` and fill in wallet/Circle credentials.
6. Verify RPC access:

```sh
arc-canteen rpc eth_chainId
arc-canteen rpc eth_blockNumber
```

If Canteen login is not available, verify the public endpoint:

```sh
curl -s "$ARC_PUBLIC_RPC_URL" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
```

## Basic Transaction Check

This requires a funded Arc Testnet wallet. Arc uses USDC as the native gas token, so fund the sender through Circle's testnet faucet or a Circle Wallet/Gas Station flow before sending.

```sh
cast send \
  --rpc-url "$ARC_RPC_URL" \
  --private-key "$ARC_PRIVATE_KEY" \
  "$ARC_RECIPIENT_ADDRESS" \
  --value 0
```

Alternative with Circle Wallets/CLI once configured:

```sh
circle wallet transfer \
  "$ARC_RECIPIENT_ADDRESS" \
  --amount 0.01 \
  --address "$CIRCLE_WALLET_ADDRESS" \
  --chain ARC-TESTNET
```

Record the resulting transaction hash and inspect it on `https://testnet.arcscan.app`.

To create and fund an Arc Testnet agent wallet with Circle CLI:

```sh
npm install -g @circle-fin/cli
circle wallet login you@example.com --testnet
circle wallet list --type agent --chain ARC-TESTNET
circle wallet fund --address "$CIRCLE_WALLET_ADDRESS" --chain ARC-TESTNET
circle wallet balance --address "$CIRCLE_WALLET_ADDRESS" --chain ARC-TESTNET
```

## Patient Wheel Agent

B1N-324 lives outside the production backend as a standalone hackathon/demo
service in this workspace.

```text
agent/metavault/     scoring, models, adapters, runner
scripts/run_agent.py one-shot worker entrypoint
decisions/           local JSON decision records, ignored by git
tests/               unit tests for scoring and state transitions
```

The agent loop:

1. Reads `capital_movement_intents` with `status=waiting_to_be_deployed`.
2. Reads b1nary opportunities from backend `/prices?asset=...` or Supabase.
3. Scores every opportunity using explicit components:
   premium APR, expiry fit, distance to strike, assignment risk proxy,
   capacity/liquidity, chain readiness, and current exposure.
4. Selects the best eligible CSP/CC quote or emits `wait`.
5. Writes a structured decision JSON plus reasoning trace under `decisions/`.
6. With `--execute`, patches the selected capital intent to
   `deployment_in_flight` through the configured API/source.

Default run mode uses the backend API:

```sh
export BACKEND_API_URL=https://your-backend-staging.example.com
export ARC_AGENT_SOURCE=backend
export ARC_AGENT_ASSETS=eth,btc,sol,tslax
python3 scripts/run_agent.py
```

Use `--execute` only when you want the demo agent to advance intent state:

```sh
python3 scripts/run_agent.py --execute
```

Supabase direct read mode is available for demo/backfill work:

```sh
export ARC_AGENT_SOURCE=supabase
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=...
python3 scripts/run_agent.py
```

Fixture mode is available for local demos without staging credentials. It
compares ETH, BTC, SOL, and TeslaX opportunities:

```sh
ARC_AGENT_SOURCE=fixture python3 scripts/run_agent.py --decisions-dir decisions/fixture
```

Execution v1:

- Base selections become `prepared_base_execution`; with `--execute` the linked
  intent is advanced to `deployment_in_flight`.
- Solana selections become `pending_execution` unless
  `ARC_AGENT_SOLANA_EXECUTION_READY=true`.
- The standalone agent does not rewrite the bridge relayer or execute unsigned
  contract flows directly.

Run tests:

```sh
python3 -m unittest discover -s tests -v
```

## Selected Circle Tools

- Arc docs: https://docs.arc.network/
- Connect to Arc: https://docs.arc.network/arc/references/connect-to-arc
- Deploy on Arc: https://docs.arc.network/arc/tutorials/deploy-on-arc
- Arc App Kit: https://docs.arc.network/app-kit
- App Kit Send: https://docs.arc.network/app-kit/send
- App Kit Swap: https://docs.arc.network/app-kit/swap
- App Kit Unified Balance: https://docs.arc.network/app-kit/unified-balance
- Circle Wallets: https://developers.circle.com/wallets
- Circle Agent Wallet quickstart: https://developers.circle.com/agent-stack/agent-wallets/quickstart
- Circle CLI command reference: https://developers.circle.com/agent-stack/circle-cli/command-reference
- Dev-controlled wallets on Arc Testnet: https://developers.circle.com/wallets/dev-controlled/create-your-first-wallet
- Circle Contracts templates: https://developers.circle.com/contracts/scp-templates-overview
- CCTP: https://developers.circle.com/cctp
- CCTP Ethereum to Arc quickstart: https://developers.circle.com/cctp/quickstarts/transfer-usdc-ethereum-to-arc
- Gateway supported blockchains: https://developers.circle.com/gateway/references/supported-blockchains
- Paymaster: https://developers.circle.com/stablecoins/paymaster-overview
- Testnet faucets: https://developers.circle.com/wallets/developer-console-faucet
- Agent Wallet chain identifiers: https://developers.circle.com/agent-stack/agent-wallets/supported-blockchains

## Local Context Bundle

The Arc Canteen CLI can download docs and sample codebases for agent context:

```sh
arc-canteen context sync
arc-canteen context --paths
```

Important synced docs include:

- `docs/docs.arc.network/arc/references/connect-to-arc.md`
- `docs/docs.arc.network/arc/tutorials/deploy-on-arc.md`
- `docs/docs.arc.network/app-kit.md`
- `docs/developers.circle.com/wallets/dev-controlled/create-your-first-wallet.md`
- `docs/developers.circle.com/cctp/quickstarts/transfer-usdc-ethereum-to-arc.md`
- `docs/developers.circle.com/gateway/references/supported-blockchains.md`
- `docs/developers.circle.com/paymaster.md`
- `samples/arc-commerce/`
- `samples/arc-escrow/`
- `samples/arc-fintech/`
- `samples/arc-multichain-wallet/`
- `samples/arc-p2p-payments/`

## Secrets

Do not commit the Canteen RPC URL from `~/.arc-canteen/env`; it contains a personal token.
