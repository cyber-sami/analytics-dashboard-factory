"""Thin Gemini (Google AI Studio) wrapper. The only place that talks to an LLM.

Keep it tiny: one function for text, one for JSON. Key comes from .env
(GEMINI_API_KEY); never commit it.

The newest models get overloaded (503) when a whole hackathon shares the free
tier, so every call walks a fallback chain instead of failing the demo.
"""
import concurrent.futures as cf
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv(Path(__file__).parent.parent / ".env")

# First entry can be overridden with GEMINI_MODEL; the rest are fallbacks.
# Order = fastest and most reliable first. Measured 2026-11-07 on the shared free-tier key: flash-lite answers in
# 0.7-1.2 s; 3.6/3.5-flash are usually out of quota (instant 429); gemma-4-26b mostly 500s; gemma-4-31b stalled for
# 10 s, so it is gone. Swap models here if the picture changes (`client.models.list()`).
CHAIN = [m for m in [os.getenv("GEMINI_MODEL"), "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.5-flash",
                     "gemma-4-26b-a4b-it"] if m]
RETRYABLE = {429, 500, 503, 504}
TIMEOUT_MS = 10000          # the SDK/API timeout. The Gemini API REJECTS anything below 10 s ("Minimum allowed deadline is 10s")
ATTEMPT_S = 4.0             # OUR wall-clock limit per attempt (flash-lite is ~1 s, 4.3 s worst seen cold): a stalled model is
                            # abandoned after this and the next one is tried; its request just finishes in the background
BUDGET_S = 5.0              # hard cap for one call across the whole chain; past it callers use the rule-based parser
OUTAGE_S = 20               # after a FULL pass fails (every model out), skip Gemini this long so only one request pays the wait
RETRY_PAUSE_S = 0.4         # one quick retry of the FIRST model on a transient failure (a blip must not cost the demo)
SKIP_QUOTA_S = 20           # a model that said 429 sits out this long (a 429 is instant, so asking again is cheap)
SKIP_ERROR_S = 10           # a model that failed otherwise (5xx, timeout) sits out briefly
_client = None
_skip_until: dict[str, float] = {}   # model -> time before which we do not ask it
_outage_until = 0.0                  # all models failed: do not even try until then
_pool = cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="gemini")   # abandoned (stalled) calls finish here


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY missing. Put it in .env (see engine/.env.example).")
        _client = genai.Client(api_key=key, http_options={"timeout": TIMEOUT_MS})
    return _client


def _generate(prompt: str, config: types.GenerateContentConfig):
    """Walk the chain, skipping models that just failed, within a hard time budget. When nothing is left to try this
    raises at once, and the callers fall back to rules (intent_llm) so the app never hangs."""
    global _outage_until
    if time.time() < _outage_until:
        raise RuntimeError("Gemini unavailable (all models failed a moment ago; using rules)")
    start, last, tried = time.time(), None, 0
    for i, model in enumerate(CHAIN):
        if time.time() - start > BUDGET_S:
            break
        if time.time() < _skip_until.get(model, 0):
            continue   # this model failed a moment ago: do not pay for it again
        for attempt in (0, 1):
            tried += 1
            try:
                fut = _pool.submit(_get_client().models.generate_content, model=model, contents=prompt, config=config)
                out = fut.result(timeout=max(0.1, min(ATTEMPT_S, BUDGET_S - (time.time() - start))))
                _skip_until.pop(model, None)
                _outage_until = 0.0
                return out
            except cf.TimeoutError as e:   # stalled: stop waiting, do not retry (that would double the wait), next model
                last = e
                _skip_until[model] = time.time() + SKIP_ERROR_S
                break
            except (errors.ServerError, errors.ClientError) as e:
                code = getattr(e, "code", None)
                if code not in RETRYABLE and not isinstance(e, errors.ServerError) and code != 404:
                    raise   # a real request error (400 etc.): another model will not fix it
                last = e
                _skip_until[model] = time.time() + (SKIP_QUOTA_S if code == 429 else 3600 if code == 404 else SKIP_ERROR_S)
                transient = code != 429 and code != 404
            except Exception as e:  # timeouts, connection resets
                last, transient = e, True
                _skip_until[model] = time.time() + SKIP_ERROR_S
            # one quick retry, only for the first model and only for a transient failure, only inside the budget
            if i == 0 and attempt == 0 and transient and time.time() - start + RETRY_PAUSE_S < BUDGET_S:
                _skip_until.pop(model, None)
                time.sleep(RETRY_PAUSE_S)
                continue
            break
    if tried:   # we really asked and everybody failed (not merely skipped): sustained outage, back off
        _outage_until = time.time() + OUTAGE_S
    raise RuntimeError(f"Gemini unavailable ({'no model available right now' if not tried else f'all models failed: {type(last).__name__} {last}'})")


def ask(prompt: str, system: str | None = None, temperature: float = 0.2) -> str:
    """Plain text completion."""
    cfg = types.GenerateContentConfig(system_instruction=system, temperature=temperature)
    return _generate(prompt, cfg).text or ""


def ask_json(prompt: str, system: str | None = None, schema=None, temperature: float = 0.1) -> dict:
    """Completion that must return JSON. Pass a pydantic model / dict as `schema` to enforce shape."""
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )
    return json.loads(_generate(prompt, cfg).text)


if __name__ == "__main__":
    print(ask("Say 'pipeline ready' and nothing else."))
    print(ask_json('Return JSON {"ok": true, "note": "<one short sentence>"}'))
