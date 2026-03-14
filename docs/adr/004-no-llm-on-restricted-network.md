# ADR-004: No LLM inference on restricted network (ChatGPT web UI + optional Ollama)

**Status**: Accepted (with optional Ollama path documented)
**Date**: 2024-03
**Deciders**: Pipeline architecture team

---

## Context

The pipeline's core function is to assist developers by providing context-enriched prompts. There are two fundamentally different architectures for this:

**Architecture A (Prompt-to-human)**: The pipeline builds a rich prompt. The human pastes the prompt into ChatGPT (or another LLM) via the web UI. The LLM's response goes to the human, not back to the pipeline.

**Architecture B (Pipeline calls LLM)**: The pipeline queries an LLM API directly, gets a response, and displays it to the human without requiring them to copy/paste anything.

The restricted network has ChatGPT available via a web browser but no LLM API access.

## Decision

**Primary approach**: Architecture A. The pipeline builds prompts; the human pastes them into ChatGPT.

**Optional extension**: If the team can get Ollama running on the restricted network (self-managed, no external API), the pipeline can be extended to call the Ollama API (`http://localhost:11434`) directly. This is documented as an option but not required for the initial implementation.

## Rationale

### Architecture A is the constraint, not a limitation

The restricted network provides ChatGPT via a web browser only. There is no ChatGPT API key available in the restricted network. Architecture B (pipeline calling an LLM) is therefore impossible with ChatGPT.

This is not a degraded mode — it is the intended design for the current environment. The prompt builder is designed specifically to make the copy-paste workflow smooth and efficient. The user types a question, gets a formatted prompt, copies it with one click, and pastes it into ChatGPT. The prompt contains enough context that ChatGPT gives a high-quality answer.

### Why not run a local LLM by default?

Running a local LLM (Llama 3, Mistral, etc.) requires:

1. **Hardware**: a 7B parameter model in 4-bit quantization requires ~4GB of GPU VRAM or ~8GB of RAM for acceptable inference speed. Larger models (13B, 70B) require 8GB+ VRAM. Application servers on the restricted network may not have GPUs.

2. **Software**: Ollama must be installed on the restricted network server. This requires either:
   - Approval to install software from the operations team
   - Building an offline installer from source (complex)
   - The Ollama binary can be downloaded on the build machine and transferred, but it's still a new service to manage

3. **Model size**: Llama 3 8B (Q4_K_M quantization) is ~5GB. Adding this to the deployment bundle doubles its size. Mistral 7B is ~4GB.

4. **Quality**: local 7B models are good but not as capable as ChatGPT (GPT-4) for complex technical reasoning. Given that the team already has ChatGPT access, using a weaker local model is a regression.

5. **Latency**: CPU-only inference on a 7B model is ~30–60 seconds per response. GPU inference is ~2–5 seconds. The copy-paste workflow with ChatGPT is approximately 10–15 seconds (copy, switch tabs, paste, read response). CPU-based local LLM would be 3–6× slower.

### When Ollama makes sense

Ollama is a good option if:
- The team's ChatGPT access is slow, rate-limited, or unreliable
- The team wants fully automated workflows (no human copy-paste step)
- A GPU is available on the restricted network server
- The operations team approves Ollama as a managed service

### Ollama integration design (optional)

If the team decides to use Ollama, the pipeline supports it via a configuration option:

```yaml
llm:
  provider: none        # Options: none, ollama
  ollama:
    base_url: http://localhost:11434
    model: llama3       # or mistral, codellama, phi3, etc.
    timeout_seconds: 120
```

With `provider: ollama`, the query flow changes:
1. User submits a question
2. RAG engine retrieves context (same as before)
3. Prompt builder assembles the prompt (same as before)
4. **New**: the pipeline POST to `http://localhost:11434/api/generate` with the prompt
5. The response streams back and is displayed in the web UI

The Ollama-enabled flow provides a fully integrated experience (no copy-paste) at the cost of local inference quality and latency.

The implementation is a thin layer on top of the existing prompt builder:

```python
class LLMProvider:
    @abstractmethod
    def complete(self, prompt: str) -> Generator[str, None, None]:
        """Stream completion tokens."""
        ...

class NullLLMProvider(LLMProvider):
    """No LLM integration. Returns the prompt itself for copy-paste."""
    def complete(self, prompt: str) -> Generator[str, None, None]:
        yield prompt

class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model

    def complete(self, prompt: str) -> Generator[str, None, None]:
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": True},
            stream=True, timeout=120
        )
        for line in resp.iter_lines():
            data = json.loads(line)
            yield data.get("response", "")
            if data.get("done"):
                break
```

The web UI handles both modes: if `provider=none`, it shows the prompt with a copy button; if `provider=ollama`, it streams the response into a text area.

## Consequences

### Positive

- No new infrastructure required for the primary (no-LLM) mode
- Works immediately after deployment with no approval or procurement process
- ChatGPT (GPT-4) is higher quality than local 7B models for complex technical reasoning
- Ollama integration is a clean extension, not a refactor
- Bundle size stays at ~1.5GB without Ollama models

### Negative

- Copy-paste workflow adds manual friction (~10–15 seconds per query)
- ChatGPT web UI requires the developer to switch browser tabs
- No automated workflows (e.g., automated documentation generation) without Ollama or another LLM

### Risks

**Risk**: ChatGPT access changes (license expires, access restricted further).
**Mitigation**: the Ollama path is documented and partially implemented as an extension point. If ChatGPT becomes unavailable, the team can enable Ollama.

**Risk**: response quality is not high enough with the copy-paste workflow.
**Mitigation**: the prompt builder is designed to produce high-quality prompts. The SYSTEM CONTEXT section sets the right framing. The context sections provide accurate technical details. ChatGPT with a well-crafted prompt outperforms a smaller local model even with perfect context.

## Rejected: Running an LLM API proxy on the restricted network

An alternative approach would be to run a proxy service that forwards requests from the restricted network to the ChatGPT API over a controlled channel. This would enable Architecture B without Ollama.

This was rejected because:
- It requires a network policy exception (controlled channel from restricted to external network)
- It introduces security and compliance risk for the organization
- It is an infrastructure concern outside the scope of this pipeline
- The copy-paste workflow is adequate for the use cases (developer tooling, not automated pipelines)
