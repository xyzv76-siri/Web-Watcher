# Phase 10B — Real LLM Provider (SenseNova-compatible)

**Generated:** 2026-08-17T03:33:00Z
**Worker:** Autonomous implementation worker
**Status:** ✅ IMPLEMENTATION COMPLETE — REAL SMOKE TEST BLOCKED (missing secret)

---

## 1. Executive Summary

Implemented the first concrete LLM Provider behind the frozen Phase 10A `AIProvider` Protocol, targeting the currently identified SenseNova-compatible service at `token.sensenova.cn/v1`. All implementation, offline tests, static/security audits complete. The single real API smoke test is **BLOCKED** because the dedicated `SENSENOVA_API_KEY` environment variable is not set — and per instructions, the provider must never read credentials from QwenPaw or any other source.

| Dimension | Result |
|---|---|
| Tests | **337/337 pass** (297 baseline + 40 new, zero regressions) |
| Security audits | **All pass** — no hardcoded secrets, no QwenPaw dependency, no non-stdlib imports, no credential logging |
| Production isolation | **Pass** — no source files modified outside Phase 10B scope; AI Radar untouched |
| Real LLM smoke test | **BLOCKED** — `SENSENOVA_API_KEY` not set |

---

## 2. Implementation Details

### 2.1 New Files (3 source + 1 test)

| File | Lines | Purpose |
|---|---|---|
| `src/web_watcher/ai_config.py` | 88 | Frozen `SenseNovaProviderConfig` dataclass + `load_sensenova_provider_config()` factory |
| `src/web_watcher/ai_provider.py` | 42 | `build_sensenova_provider()` factory combining config + provider construction |
| `src/web_watcher/llm_provider.py` | 243 | `SenseNovaLLMProvider` class implementing `AIProvider` Protocol via stdlib `urllib.request` |
| `tests/test_llm_provider.py` | 810 | 40 offline tests covering config, provider, errors, security, and integration |

### 2.2 Provider Design

```
SenseNovaProviderConfig (frozen dataclass)
├── base_url: str          (default: "https://token.sensenova.cn/v1")
├── api_path: str          (default: "/chat/completions")
├── model: str             (default: "sensenova-6.7-flash-lite")
├── timeout_seconds: float (default: 30.0)
├── max_attempts: int      (default: 3)
├── env_var_name: str      (default: "SENSENOVA_API_KEY")
├── response_format_json: bool (default: True)
└── system_prompt: str | None  (default: built-in JSON-format prompt)
```

**Architecture:**
```
build_sensenova_provider(config | **kwargs)
  └─→ load_sensenova_provider_config(**kwargs)
        └─→ SenseNovaProviderConfig (frozen, no secrets)
  └─→ SenseNovaLLMProvider(config)
        └─→ invoke(prompt, context) → ProviderResponse
              └─→ urllib.request.urlopen() [stdlib only]
                    └─→ Extract choices[0].message.content
                    └─→ Return ProviderResponse(content=..., metadata={http_status, model, usage})
```

### 2.3 Error Handling Matrix

| Condition | Behavior | Error Type | Retries |
|---|---|---|---|
| API key missing | Raise immediately | `ProviderError` | No |
| HTTP 401/403 | Raise immediately | `ProviderError` | No |
| HTTP 408 | Raise immediately | `ProviderTimeoutError` | No |
| HTTP 429/500/502/503/504 | Retry with backoff | `ProviderError` / `ProviderTimeoutError` | Yes |
| Network error | Retry with backoff | `ProviderError` | Yes |
| Non-JSON response | Raise immediately | `ProviderError` | No |
| Empty/malformed choices | Return empty content | None (Phase 10A handles) | No |

Backoff: `min(attempt * 0.5s, 5s)` between retry attempts.

### 2.4 Security Guarantees (verified by automated tests)

1. **No hardcoded secrets** — source scanned for `API_KEY=`, `api_key=`, `token=`, `secret=` patterns; none found
2. **No QwenPaw dependency** — source scanned for `qwenpaw`, `qwen`; none found
3. **No PolicyEngine coupling** — source scanned for `PolicyEngine`, `PolicyDecision`; none found
4. **No credential logging** — source scanned for `print.*key`, `logger.*secret`; none found
5. **No non-stdlib network imports** — source scanned for `requests`, `httpx`, `openai`, `anthropic`; none found
6. **API key never in ProviderResponse** — test verifies key absent from both `content` and `metadata`
7. **No framework config reads** — provider reads only the dedicated env var

---

## 3. Test Results

### 3.1 Phase 10B Tests (40 tests, 100% pass)

```
tests/test_llm_provider.py
├── TestSenseNovaProviderConfig (4 tests)
│   ├── test_default_values
│   ├── test_explicit_values_override_defaults
│   ├── test_config_is_frozen
│   └── test_config_never_contains_secret
├── TestSenseNovaLLMProviderConstruction (2 tests)
│   ├── test_provider_accepts_config
│   └── test_provider_config_readonly
├── TestAPIKeyHandling (3 tests)
│   ├── test_missing_api_key_raises_provider_error
│   ├── test_api_key_never_appears_in_metadata
│   └── test_empty_api_key_raises_provider_error
├── TestProviderSuccess (5 tests)
│   ├── test_success_returns_extracted_content
│   ├── test_metadata_includes_model
│   ├── test_metadata_includes_usage
│   ├── test_response_format_json_included_in_payload
│   └── test_response_format_json_omitted_when_disabled
├── TestProviderErrors (9 tests)
│   ├── test_http_401_raises_provider_error_no_retry
│   ├── test_http_403_raises_provider_error_no_retry
│   ├── test_http_408_raises_provider_timeout_error
│   ├── test_http_500_retries_then_succeeds
│   ├── test_http_500_exhausts_retries_raises_provider_error
│   ├── test_http_429_retries
│   ├── test_network_error_raises_provider_error
│   ├── test_non_json_response_raises_provider_error
│   ├── test_empty_choices_returns_empty_content
│   └── test_missing_message_returns_empty_content
├── TestBuildSenseNovaProvider (3 tests)
│   ├── test_factory_with_config
│   ├── test_factory_with_kwargs
│   └── test_factory_no_args_uses_defaults
├── TestSecurityRegressions (5 tests)
│   ├── test_provider_source_no_hardcoded_secrets
│   ├── test_provider_source_no_qwenpaw_config_import
│   ├── test_provider_source_no_policy_engine
│   ├── test_config_source_no_hardcoded_secrets
│   └── test_provider_never_reads_qwenpaw_env
└── TestIntegrationOffline (8 tests)
    ├── test_ai_judge_with_sensenova_provider_valid_response
    ├── test_ai_judge_with_sensenova_provider_invalid_json
    ├── test_ai_judge_with_sensenova_provider_missing_key_fails
    ├── test_provider_uses_configured_endpoint_url
    ├── test_provider_sets_correct_http_method
    ├── test_provider_sets_content_type_header
    ├── test_provider_sets_authorization_header_format
    └── test_provider_never_logs_secret
```

### 3.2 Full Regression (337 tests, 100% pass)

```
297 baseline (Phase 2–10A) + 40 Phase 10B = 337 total
Time: 10.55s
```

### 3.3 Contract Isolation Guard (5/5 pass)

```
tests/test_no_network.py
├── test_contract_files_have_no_network_library_imports ... PASSED
├── test_adapters_module_is_pure_contract ... PASSED
├── test_fetch_module_has_no_concrete_adapter_classes ... PASSED
├── test_no_forbidden_concrete_adapters ... PASSED
└── test_all_python_files_compile ... PASSED
```

---

## 4. Production Isolation Verification

### 4.1 Frozen Phase 10A Files (unchanged)

| File | MD5 | Status |
|---|---|---|
| `src/web_watcher/ai_contract.py` | `073969758611dfdb9dfce28bac8d2b25` | ✅ Unchanged |
| `src/web_watcher/ai_errors.py` | `7e9f2498053b16068b35a1de2b30639b` | ✅ Unchanged |
| `tests/test_ai_contract.py` | `f7d57093fdc00eab69cb2d25f647af27` | ✅ Unchanged |

### 4.2 Allowed New Files Only

```
src/web_watcher/ai_config.py     ✅ NEW (allowed)
src/web_watcher/ai_provider.py   ✅ NEW (allowed)
src/web_watcher/llm_provider.py  ✅ NEW (allowed)
tests/test_llm_provider.py       ✅ NEW (allowed)
reports/PHASE_10B_AUTONOMOUS_REPORT.md  ✅ NEW (allowed)
```

### 4.3 AI Radar Boundary

AI Radar project is a separate repository. No AI Radar source, test, config, database, or production files were touched by Phase 10B.

### 4.4 Frozen Phase 2–9 Modules

No modifications to `models.py`, `policy.py`, `fetch.py`, `adapters.py`, `targets.py`, `snapshots.py`, `event_correlator.py`, `repository.py`, `storage.py`, `config.py`, `main.py`, or any Phase 2–9 test files.

---

## 5. Real LLM Smoke Test — BLOCKED

**Status:** ❌ BLOCKED — missing dedicated secret

**Root cause:** `SENSENOVA_API_KEY` environment variable is not set in the execution environment.

**Provider behavior when key is missing:**
```
ProviderError: API key not found: set SENSENOVA_API_KEY environment variable
```

**Per task instructions:**
> "If the dedicated Web Watcher secret is absent, do NOT obtain it from QwenPaw. Complete implementation and offline tests, mark the real smoke test BLOCKED due to missing dedicated secret, and STOP."

**To unblock:** Set `SENSENOVA_API_KEY` in the execution environment, then invoke:
```python
from web_watcher.ai_provider import build_sensenova_provider
provider = build_sensenova_provider()
response = provider.invoke("test prompt", {})
```

Or with custom config:
```python
from web_watcher.ai_config import load_sensenova_provider_config
from web_watcher.llm_provider import SenseNovaLLMProvider
config = load_sensenova_provider_config(
    base_url="https://token.sensenova.cn/v1",
    model="sensenova-6.7-flash-lite",
)
provider = SenseNovaLLMProvider(config=config)
```

---

## 6. Dependencies

**Zero new dependencies added.** The implementation uses only Python stdlib:

| Module | Purpose |
|---|---|
| `urllib.request` | HTTP client |
| `urllib.error` | HTTP error handling |
| `json` | Request/response serialization |
| `os` | Environment variable access |
| `ssl` | SSL error handling |
| `time` | Retry backoff |
| `dataclasses` | Config dataclass |
| `typing` | Type annotations |

`pyproject.toml` was **not modified**.

---

## 7. What Was NOT Done (Out of Scope)

- ❌ **Real API smoke test** — BLOCKED (missing `SENSENOVA_API_KEY`)
- ❌ **Phase 10C** — explicitly not entered per task instruction
- ❌ **git commit / git push** — explicitly prohibited
- ❌ **Modifying any file outside the 5 allowed files**
- ❌ **Installing any new Python package**
- ❌ **Reading QwenPaw API key or configuration**
- ❌ **Adding system prompt injection into Phase 10A** (kept in provider config only)
- ❌ **Modifying `AIContext`, `AIJudgment`, `AIProvider`, `AIJudge`, or `AIError`**

---

## 8. Phase Boundary Status

| Phase | Status | Notes |
|---|---|---|
| Phase 2–9 | ✅ FROZEN | No modifications |
| Phase 10A | ✅ FROZEN | Contract unchanged |
| **Phase 10B** | **✅ COMPLETE** | Implementation + offline tests done; real smoke BLOCKED |
| Phase 10C | ⏭️ NOT ENTERED | Per task instruction |

---

## 9. Files Produced

```
src/web_watcher/
├── ai_config.py          (88 lines, NEW — Phase 10B)
├── ai_provider.py        (42 lines, NEW — Phase 10B)
├── llm_provider.py       (243 lines, NEW — Phase 10B)
├── ai_contract.py        (401 lines, FROZEN — Phase 10A)
└── ai_errors.py          (35 lines, FROZEN — Phase 10A)

tests/
├── test_llm_provider.py  (810 lines, NEW — Phase 10B)
└── test_ai_contract.py   (740 lines, FROZEN — Phase 10A)

reports/
└── PHASE_10B_AUTONOMOUS_REPORT.md  (this file)
```

---

**END OF REPORT** — Phase 10B complete. Real LLM smoke test BLOCKED pending `SENSENOVA_API_KEY`.
