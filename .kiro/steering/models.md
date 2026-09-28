---
inclusion: always
---

# Models: no Anthropic Claude anywhere in Minnal's runtime

Minnal must run without any Anthropic Claude model. This is a hard project constraint, enforced four ways: this steering file, a single model config, IAM that only allows the approved model ARNs, and the Kiro guard hook that blocks any write containing an `anthropic.` model ID outside `docs/` and `.kiro/`.

## Approved models (Amazon Bedrock, us-east-1 for the demo; Mumbai noted for production)

| Tier | Model | Bedrock ID | Why | ap-south-1 |
|---|---|---|---|---|
| **Default (all text agents)** | Amazon **Nova 2 Lite** | `us.amazon.nova-2-lite-v1:0` (inference profile; base `amazon.nova-2-lite-v1:0`) | Amazon's own model, 1M context, tool use, Converse API, low cost | yes |
| **Reasoning tier** (Commander, Diagnostics, Safety) | OpenAI **gpt-oss-120b** (open-weight) | `openai.gpt-oss-120b-1:0` (in-region only, no `us.` prefix) | Strong multi-step reasoning and tool calling; 128K context | yes |
| Voice | Amazon **Nova 2 Sonic** | `amazon.nova-2-sonic-v1:0` | Speech-to-speech citizen line | no (us-east-1) |
| Embeddings (KB) | Amazon **Titan Text Embeddings V2** | `amazon.titan-embed-text-v2:0` | Wide region support; S3 Vectors compatible | yes |

Alternates you may evaluate (same Converse interface, no code change): Mistral Large 3, DeepSeek V3.2, Qwen3 235B A22B, Kimi K3. Any switch is decided by an AgentCore Evaluations A/B run and recorded in an ADR.

## Rules
1. **One source of truth:** `patterns/agui-minnal/config/models.yaml` maps each agent to a model ID and inference settings. Agents read it through `Settings`; no model ID string appears anywhere else in code.
2. **FAST default must be replaced:** the FAST `agui-strands-agent` template ships with a Claude model ID in `agent.py`. Phase 0 replaces it with the config lookup.
3. **IAM allow-list:** runtime roles may invoke only the approved model and inference-profile ARNs (replace FAST's `foundation-model/*`).
4. Use the Strands `BedrockModel` (Converse). Set `temperature` ≤ 0.3 for Commander, Safety and Diagnostics; structured output via Pydantic.
5. Verify every model parameter (reasoning settings, max tokens, tool-choice support) with the **aws-knowledge** MCP before using it; model capabilities differ.
6. Tests must include `tests/test_no_claude.py`, which fails if any runtime file or config references an Anthropic model ID.

```yaml
# patterns/agui-minnal/config/models.yaml
default: { model_id: us.amazon.nova-2-lite-v1:0, temperature: 0.2, max_tokens: 4096 }
agents:
  commander:    { model_id: openai.gpt-oss-120b-1:0, temperature: 0.2, max_tokens: 8192 }
  diagnostics:  { model_id: openai.gpt-oss-120b-1:0, temperature: 0.1 }
  safety:       { model_id: openai.gpt-oss-120b-1:0, temperature: 0.0 }
  hazard:       { model_id: us.amazon.nova-2-lite-v1:0 }
  dispatch:     { model_id: us.amazon.nova-2-lite-v1:0 }
  pio:          { model_id: us.amazon.nova-2-lite-v1:0 }
  scribe:       { model_id: us.amazon.nova-2-lite-v1:0 }
  citizen_line: { model_id: amazon.nova-2-sonic-v1:0 }
embeddings:     { model_id: amazon.titan-embed-text-v2:0 }
```
