#!/usr/bin/env python3
"""配置并验证 DeepSeek 作为 Codex 原生子 Agent。"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MODEL = "deepseek-v4-flash"
PROVIDER = "deepseek"
ROLE = "DeepSeek"
EFFORT = "high"
PARENT_MULTI_AGENT_VERSION = "v1"
MAX_STATE_DATABASES = 32
METADATA_WAIT_SECONDS = 5.0
LOCK_WAIT_SECONDS = 5.0
KEYCHAIN_SERVICE = "codex-deepseek-api-key"
OFFICIAL_SETUP_URL = "https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh"
PROVIDER_BEGIN = "# BEGIN CODEX-DEEPSEEK-SUBAGENT PROVIDER"
PROVIDER_END = "# END CODEX-DEEPSEEK-SUBAGENT PROVIDER"
ROLE_BEGIN = "# BEGIN CODEX-DEEPSEEK-SUBAGENT ROLE"
ROLE_END = "# END CODEX-DEEPSEEK-SUBAGENT ROLE"
MIN_CODEX_VERSION = (0, 144, 0)


class ManagerError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class Paths:
    home: Path
    config: Path
    catalog: Path
    agent: Path
    state_dir: Path
    manifest: Path


def resolve_paths(codex_home: str | None) -> Paths:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    return Paths(
        home=home,
        config=home / "config.toml",
        catalog=home / "models-with-deepseek.json",
        agent=home / "agents" / f"{ROLE}.toml",
        state_dir=home / "codex-deepseek-subagent",
        manifest=home / "codex-deepseek-subagent" / "manifest.json",
    )


def result(status: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": status, **kwargs}


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(payload.get("status", "unknown"))
    for key, value in payload.items():
        if key != "status":
            print(f"{key}: {value}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(map(int, match.groups())) if match else None


def find_codex() -> str:
    configured = os.environ.get("CODEX_BIN")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    found = shutil.which("codex")
    if found:
        return found
    raise ManagerError("codex_missing", "没有找到 Codex CLI。请先安装或启动 Codex。")


def codex_version(codex_bin: str) -> tuple[int, int, int]:
    proc = subprocess.run([codex_bin, "--version"], capture_output=True, text=True, timeout=15)
    version = parse_version(f"{proc.stdout}\n{proc.stderr}")
    if proc.returncode != 0 or not version:
        raise ManagerError("codex_version_unknown", "无法读取 Codex 版本。")
    return version


def keychain_account() -> str:
    return getpass.getuser()


def keychain_available() -> bool:
    return sys.platform == "darwin" and Path("/usr/bin/security").is_file()


def keychain_has_key() -> bool:
    if not keychain_available():
        return False
    proc = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-a", keychain_account(), "-s", KEYCHAIN_SERVICE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def store_keychain_key(secret: str) -> None:
    if not keychain_available():
        raise ManagerError("unsupported_platform", "当前版本只支持 macOS Keychain。")
    if not secret.startswith("sk-"):
        raise ManagerError("invalid_api_key", "DeepSeek API Key 应以 sk- 开头。")
    proc = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            keychain_account(),
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            secret,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise ManagerError("keychain_write_failed", "无法把 API Key 写入 macOS Keychain。")


def remove_keychain_key() -> bool:
    if not keychain_available() or not keychain_has_key():
        return False
    proc = subprocess.run(
        ["/usr/bin/security", "delete-generic-password", "-a", keychain_account(), "-s", KEYCHAIN_SERVICE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def parse_toml_text(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManagerError("invalid_config", f"config.toml 无法解析：{exc}") from exc


def remove_marked_block(text: str, begin: str, end: str) -> str:
    pattern = re.compile(
        rf"\n?{re.escape(begin)}.*?{re.escape(end)}\n?",
        flags=re.DOTALL,
    )
    return pattern.sub("\n", text).rstrip() + ("\n" if text else "")


def remove_managed_blocks(text: str) -> str:
    text = remove_marked_block(text, PROVIDER_BEGIN, PROVIDER_END)
    return remove_marked_block(text, ROLE_BEGIN, ROLE_END)


def remove_toml_table(text: str, table: str) -> str:
    lines = text.splitlines()
    tokens = [
        rf"(?:{re.escape(part)}|\"{re.escape(part)}\"|'{re.escape(part)}')"
        for part in table.split(".")
    ]
    header = re.compile(r"^\[\s*" + r"\s*\.\s*".join(tokens) + r"\s*\]\s*(?:#.*)?$")
    start = next((index for index, line in enumerate(lines) if header.match(line.strip())), None)
    if start is None:
        return text
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].strip().startswith("[")),
        len(lines),
    )
    kept = lines[:start] + lines[end:]
    return "\n".join(kept).rstrip() + "\n"


def top_level_key(text: str, key: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            break
        match = re.match(rf"{re.escape(key)}\s*=\s*\"([^\"]+)\"", stripped)
        if match:
            return match.group(1)
    return None


def set_top_level_key(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    assignment = f'{key} = "{value}"'
    first_table = next((i for i, line in enumerate(lines) if line.strip().startswith("[")), len(lines))
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(first_table):
        if key_pattern.match(lines[index]):
            lines[index] = assignment
            return "\n".join(lines).rstrip() + "\n"
    lines.insert(first_table, assignment)
    if first_table and lines[first_table - 1].strip():
        lines.insert(first_table + 1, "")
    return "\n".join(lines).rstrip() + "\n"


def remove_top_level_key_if_value(text: str, key: str, expected: str) -> str:
    lines = text.splitlines()
    first_table = next((i for i, line in enumerate(lines) if line.strip().startswith("[")), len(lines))
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=\s*"{re.escape(expected)}"\s*$')
    kept = [line for index, line in enumerate(lines) if not (index < first_table and pattern.match(line))]
    return "\n".join(kept).rstrip() + "\n"


def expected_agent_text() -> str:
    return f'''name = "{ROLE}"
description = "Text-only DeepSeek subagent for coding, repository research, review, and verification. Do not use it for image, video, screenshot, or other visual inspection; the parent agent must inspect visual inputs and pass the findings as text."
model = "{MODEL}"
model_provider = "{PROVIDER}"
model_reasoning_effort = "{EFFORT}"
developer_instructions = """
You are a focused DeepSeek subagent running inside Codex.

Complete the bounded task assigned by the parent agent, use available tools when needed, and return a concise evidence-based result.
You are text-only. Do not claim to inspect images, videos, screenshots, or other visual inputs. If visual evidence is required and the parent did not provide a textual description, report that limitation clearly.
Do not spawn additional subagents unless the user or parent explicitly asks for nested delegation.
"""
'''


def managed_provider_block() -> str:
    account = keychain_account().replace("\\", "\\\\").replace('"', '\\"')
    return f'''
{PROVIDER_BEGIN}
[model_providers.{PROVIDER}]
name = "DeepSeek"
base_url = "https://api.deepseek.com/"
wire_api = "responses"

[model_providers.{PROVIDER}.auth]
command = "/usr/bin/security"
args = ["find-generic-password", "-a", "{account}", "-s", "{KEYCHAIN_SERVICE}", "-w"]
timeout_ms = 5000
refresh_interval_ms = 0
{PROVIDER_END}
'''


def expected_provider_auth() -> dict[str, Any]:
    return {
        "command": "/usr/bin/security",
        "args": [
            "find-generic-password",
            "-a",
            keychain_account(),
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        "timeout_ms": 5000,
        "refresh_interval_ms": 0,
    }


def provider_conflicts(provider: dict[str, Any] | None) -> list[str]:
    if not provider:
        return []
    issues: list[str] = []
    expected = {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/",
        "wire_api": "responses",
    }
    for key, value in expected.items():
        if provider.get(key) != value:
            issues.append(f"model_providers.deepseek.{key}")
    auth = provider.get("auth")
    if not isinstance(auth, dict):
        issues.append("model_providers.deepseek.auth")
        return issues
    for key, value in expected_provider_auth().items():
        if auth.get(key) != value:
            issues.append(f"model_providers.deepseek.auth.{key}")
    return issues


def compatible_existing(parsed: dict[str, Any], paths: Paths) -> tuple[bool, list[str]]:
    issues: list[str] = []
    provider = (parsed.get("model_providers") or {}).get(PROVIDER)
    issues.extend(provider_conflicts(provider))
    agent = (parsed.get("agents") or {}).get(ROLE)
    if agent:
        if set(agent) - {"description", "config_file"}:
            issues.append("agents.DeepSeek")
        if Path(agent.get("config_file", "")).expanduser() != paths.agent:
            issues.append("agents.DeepSeek.config_file")
    return not issues, issues


def fetch_official_deepseek_model() -> dict[str, Any]:
    request = urllib.request.Request(
        OFFICIAL_SETUP_URL,
        headers={"User-Agent": "codex-deepseek-subagent/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            script = response.read().decode("utf-8")
    except Exception as exc:
        raise ManagerError("official_catalog_unavailable", "无法获取 DeepSeek 官方 Codex 模型目录。") from exc
    match = re.search(
        r"cat > \"\$TMP_MODELS\" <<'CODEX_MODELS_JSON'\n(.*?)\nCODEX_MODELS_JSON",
        script,
        flags=re.DOTALL,
    )
    if not match:
        raise ManagerError("official_catalog_changed", "DeepSeek 官方安装脚本格式已变化，未执行任何远程脚本。")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ManagerError("official_catalog_invalid", "DeepSeek 官方模型目录不是有效 JSON。") from exc
    for model in payload.get("models", []):
        if model.get("slug") == MODEL:
            return model
    raise ManagerError("official_model_missing", f"官方目录中没有 {MODEL}。")


def run_codex_models(codex_bin: str, paths: Paths) -> dict[str, Any]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    proc = subprocess.run(
        [codex_bin, "debug", "models"],
        capture_output=True,
        text=True,
        env=env,
        timeout=45,
    )
    if proc.returncode != 0:
        raise ManagerError("codex_catalog_failed", "Codex 无法读取当前模型目录。", {"stderr": proc.stderr[-800:]})
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ManagerError("codex_catalog_invalid", "Codex 返回的模型目录不是有效 JSON。") from exc


def load_base_catalog(codex_bin: str, paths: Paths, config: dict[str, Any]) -> dict[str, Any]:
    configured_path = config.get("model_catalog_json")
    if configured_path:
        candidate = Path(configured_path).expanduser()
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text())
                if isinstance(data.get("models"), list):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
    return run_codex_models(codex_bin, paths)


def merged_catalog(base: dict[str, Any], deepseek_model: dict[str, Any], parent_model: str) -> dict[str, Any]:
    models = [model for model in base.get("models", []) if model.get("slug") != MODEL]
    models.append(deepseek_model)
    parent_found = False
    for model in models:
        if model.get("slug") == parent_model:
            model["multi_agent_version"] = PARENT_MULTI_AGENT_VERSION
            parent_found = True
            break
    if not parent_found:
        raise ManagerError("parent_model_missing", f"模型目录中没有父模型 {parent_model}。")
    models.sort(key=lambda item: item.get("slug", ""))
    return {"models": models}


def configured_parent_model(config: dict[str, Any]) -> str:
    model = config.get("model")
    if isinstance(model, str) and model and not model.startswith("deepseek"):
        return model
    return "gpt-5.6-sol"


def make_backup(paths: Paths) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = paths.state_dir / "backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    for source in (paths.config, paths.catalog, paths.agent, paths.manifest):
        if source.is_file():
            shutil.copy2(source, backup / source.name)
    return backup


def write_manifest(paths: Paths, payload: dict[str, Any]) -> None:
    atomic_write(paths.manifest, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def read_manifest(paths: Paths) -> dict[str, Any]:
    if not paths.manifest.is_file():
        return {}
    try:
        payload = json.loads(paths.manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def install(paths: Paths, codex_bin: str) -> dict[str, Any]:
    paths.home.mkdir(parents=True, exist_ok=True)
    config_text = paths.config.read_text() if paths.config.is_file() else ""
    parsed = parse_toml_text(config_text) if config_text.strip() else {}
    previous_manifest = read_manifest(paths)
    provider_marker_present = PROVIDER_BEGIN in config_text and PROVIDER_END in config_text
    role_marker_present = ROLE_BEGIN in config_text and ROLE_END in config_text
    unmanaged_config = remove_managed_blocks(config_text)
    unmanaged_parsed = parse_toml_text(unmanaged_config) if unmanaged_config.strip() else {}
    compatible, conflicts = compatible_existing(unmanaged_parsed, paths)
    if not compatible:
        raise ManagerError("conflict", "发现不兼容的现有 DeepSeek 配置。", {"fields": conflicts})
    if paths.agent.is_file() and paths.agent.read_text() != expected_agent_text():
        raise ManagerError("conflict", "现有 DeepSeek Agent 文件与目标配置不同。", {"path": str(paths.agent)})
    legacy_role_present = bool((unmanaged_parsed.get("agents") or {}).get(ROLE))
    if legacy_role_present:
        unmanaged_config = remove_toml_table(unmanaged_config, f"agents.{ROLE}")
        unmanaged_parsed = parse_toml_text(unmanaged_config) if unmanaged_config.strip() else {}

    catalog_preexisted_now = paths.catalog.is_file()
    agent_preexisted_now = paths.agent.is_file()
    selected_before = parsed.get("model_catalog_json") == str(paths.catalog)
    previous_schema = previous_manifest.get("schema_version", 1) if previous_manifest else 2
    previous_selection_managed = bool(previous_manifest.get("managed_catalog_selection"))
    if previous_manifest and previous_schema < 2 and selected_before:
        previous_selection_managed = True
    managed_catalog_selection = previous_selection_managed or not selected_before
    previous_catalog_value = (
        previous_manifest.get("previous_model_catalog_json")
        if previous_selection_managed and selected_before
        else parsed.get("model_catalog_json")
    )
    if previous_manifest and previous_schema < 2:
        catalog_preexisted = bool(previous_manifest.get("catalog_preexisted", False))
        agent_preexisted = bool(
            previous_manifest.get(
                "agent_preexisted",
                not previous_manifest.get("managed_agent_file", False),
            )
        )
    else:
        catalog_preexisted = bool(previous_manifest.get("catalog_preexisted", catalog_preexisted_now))
        agent_preexisted = bool(previous_manifest.get("agent_preexisted", agent_preexisted_now))

    backup = make_backup(paths)
    try:
        deepseek_model = fetch_official_deepseek_model()
        base = load_base_catalog(codex_bin, paths, parsed)
        parent_model = configured_parent_model(parsed)
        previous_parent = previous_manifest.get("parent_model")
        if previous_parent and previous_parent != parent_model:
            previous_entry = next(
                (item for item in base.get("models", []) if item.get("slug") == previous_parent),
                None,
            )
            if previous_entry is not None:
                original_version = previous_manifest.get("parent_original_multi_agent_version")
                if original_version is None:
                    previous_entry.pop("multi_agent_version", None)
                else:
                    previous_entry["multi_agent_version"] = original_version
        current_parent_entry = next(
            (item for item in base.get("models", []) if item.get("slug") == parent_model),
            None,
        )
        if not current_parent_entry:
            raise ManagerError("parent_model_missing", f"模型目录中没有父模型 {parent_model}。")
        parent_original_version = (
            previous_manifest.get("parent_original_multi_agent_version")
            if previous_parent == parent_model
            else current_parent_entry.get("multi_agent_version")
        )
        catalog = merged_catalog(base, deepseek_model, parent_model)
        catalog_bytes = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode()

        new_config = unmanaged_config
        provider_exists = bool((unmanaged_parsed.get("model_providers") or {}).get(PROVIDER))
        if not provider_exists:
            new_config = new_config.rstrip() + "\n" + managed_provider_block()
        new_config = set_top_level_key(new_config, "model_catalog_json", str(paths.catalog))
        parse_toml_text(new_config)
        json.loads(catalog_bytes)

        atomic_write(paths.catalog, catalog_bytes)
        if not paths.agent.is_file():
            atomic_write(paths.agent, expected_agent_text().encode(), mode=0o644)
        atomic_write(paths.config, new_config.encode())

        previous_agent_managed = bool(previous_manifest.get("managed_agent_file"))
        managed_agent_file = previous_agent_managed or not agent_preexisted_now
        catalog_original_backup = previous_manifest.get("catalog_original_backup")
        if not catalog_original_backup and catalog_preexisted_now:
            candidate = backup / paths.catalog.name
            if candidate.is_file():
                catalog_original_backup = str(candidate)
        adopted_existing = provider_exists or agent_preexisted or catalog_preexisted
        manifest = {
            "schema_version": 2,
            "installed_at": datetime.now().isoformat(timespec="seconds"),
            "backup": str(backup),
            "previous_model_catalog_json": previous_catalog_value,
            "managed_catalog_selection": managed_catalog_selection,
            "managed_provider_block": provider_marker_present or not provider_exists,
            "managed_agent_file": managed_agent_file,
            "catalog_preexisted": catalog_preexisted,
            "catalog_original_backup": catalog_original_backup,
            "agent_preexisted": agent_preexisted,
            "legacy_role_block_removed": role_marker_present or legacy_role_present,
            "adopted_existing": adopted_existing,
            "parent_model": parent_model,
            "parent_multi_agent_version": PARENT_MULTI_AGENT_VERSION,
            "parent_original_multi_agent_version": parent_original_version,
            "config_sha256": sha256_bytes(new_config.encode()),
            "catalog_sha256": sha256_bytes(catalog_bytes),
            "agent_sha256": sha256_bytes(expected_agent_text().encode()),
        }
        write_manifest(paths, manifest)
        return {"backup": str(backup), "adopted_existing": adopted_existing}
    except Exception:
        restore_backup(paths, backup)
        raise


def static_status(paths: Paths, codex_bin: str | None = None) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "config_exists": paths.config.is_file(),
        "catalog_exists": paths.catalog.is_file(),
        "agent_exists": paths.agent.is_file(),
        "credential_present": keychain_has_key(),
        "manifest_exists": paths.manifest.is_file(),
    }
    errors: list[str] = []
    parsed: dict[str, Any] = {}
    if paths.config.is_file():
        try:
            parsed = parse_toml_text(paths.config.read_text())
            checks["config_valid"] = True
        except ManagerError as exc:
            checks["config_valid"] = False
            errors.append(str(exc))
    provider = (parsed.get("model_providers") or {}).get(PROVIDER)
    role = (parsed.get("agents") or {}).get(ROLE)
    checks["provider_registered"] = bool(provider)
    checks["provider_valid"] = bool(provider) and not provider_conflicts(provider)
    checks["agent_discovery"] = "standalone"
    checks["legacy_role_registration_present"] = bool(role)
    checks["legacy_role_registration_absent"] = not bool(role)
    checks["catalog_selected"] = Path(parsed.get("model_catalog_json", "")).expanduser() == paths.catalog
    parent_model = configured_parent_model(parsed)
    checks["parent_model"] = parent_model
    if paths.catalog.is_file():
        try:
            data = json.loads(paths.catalog.read_text())
            checks["model_registered"] = any(item.get("slug") == MODEL for item in data.get("models", []))
            parent_entry = next(
                (item for item in data.get("models", []) if item.get("slug") == parent_model),
                None,
            )
            checks["parent_multi_agent_version"] = (
                parent_entry.get("multi_agent_version") if parent_entry else None
            )
            checks["parent_uses_plaintext_v1"] = (
                checks["parent_multi_agent_version"] == PARENT_MULTI_AGENT_VERSION
            )
        except (OSError, json.JSONDecodeError):
            checks["model_registered"] = False
            checks["parent_uses_plaintext_v1"] = False
            errors.append("模型目录无法解析。")
    else:
        checks["model_registered"] = False
        checks["parent_uses_plaintext_v1"] = False
    checks["agent_content_valid"] = paths.agent.is_file() and paths.agent.read_text() == expected_agent_text()

    version: tuple[int, int, int] | None = None
    if codex_bin:
        try:
            version = codex_version(codex_bin)
            checks["codex_version"] = ".".join(map(str, version))
            checks["codex_supported"] = version >= MIN_CODEX_VERSION
        except ManagerError as exc:
            errors.append(str(exc))
    required = (
        "config_valid",
        "provider_valid",
        "catalog_selected",
        "model_registered",
        "parent_uses_plaintext_v1",
        "agent_content_valid",
        "credential_present",
        "manifest_exists",
        "legacy_role_registration_absent",
    )
    ready = all(checks.get(key) is True for key in required)
    return result("configured" if ready else "partial", checks=checks, errors=errors)


def direct_test(paths: Paths, codex_bin: str) -> dict[str, Any]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    prompt = "Reply exactly DEEPSEEK_DIRECT_OK and nothing else."
    proc = subprocess.run(
        [
            codex_bin,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--json",
            "-s",
            "read-only",
            "-C",
            str(paths.home),
            "-m",
            MODEL,
            "-c",
            'model_provider="deepseek"',
            "-c",
            'model_reasoning_effort="high"',
            prompt,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if proc.returncode != 0 or "DEEPSEEK_DIRECT_OK" not in proc.stdout:
        raise ManagerError(
            "direct_test_failed",
            "DeepSeek 直连测试失败。",
            {"stderr": proc.stderr[-1000:]},
        )
    return {"direct": True}


def choose_parent_model(paths: Paths) -> str:
    parsed = parse_toml_text(paths.config.read_text())
    return configured_parent_model(parsed)


def query_child_metadata(
    paths: Paths,
    child_id: str,
    deadline: float | None = None,
) -> dict[str, Any] | None:
    candidates: list[tuple[float, Path]] = []
    for state_db in paths.home.glob("state_*.sqlite"):
        try:
            candidates.append((state_db.stat().st_mtime, state_db))
        except OSError:
            continue
    for _, state_db in sorted(candidates, reverse=True)[:MAX_STATE_DATABASES]:
        if deadline is not None and time.monotonic() >= deadline:
            return None
        try:
            with sqlite3.connect(
                f"file:{state_db}?mode=ro",
                uri=True,
                timeout=0.05,
            ) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(threads)").fetchall()
                }
                required = {"id", "model_provider", "model", "reasoning_effort", "agent_role"}
                if not required.issubset(columns):
                    continue
                row = connection.execute(
                    "SELECT model_provider, model, reasoning_effort, agent_role FROM threads WHERE id = ?",
                    (child_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            continue
        if row:
            return {
                "model_provider": row[0],
                "model": row[1],
                "reasoning_effort": row[2],
                "agent_role": row[3],
            }
    return None


def wait_for_child_metadata(
    paths: Paths,
    child_id: str,
    timeout_seconds: float = METADATA_WAIT_SECONDS,
    poll_interval: float = 0.2,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        metadata = query_child_metadata(paths, child_id, deadline)
        if metadata is not None:
            return metadata
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(poll_interval, remaining))


def native_test(paths: Paths, codex_bin: str) -> dict[str, Any]:
    parent_model = choose_parent_model(paths)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    prompt = (
        'Use the native spawn_agent tool exactly once. Set agent_type to DeepSeek and fork_turns to none. '
        'Give it this task: Reply exactly NATIVE_DEEPSEEK_OK. '
        "Then wait for that subagent and return only its final response."
    )
    proc = subprocess.run(
        [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-s",
            "read-only",
            "-C",
            str(paths.home),
            "-m",
            parent_model,
            prompt,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    if proc.returncode != 0:
        raise ManagerError(
            "native_test_failed",
            "新 Codex 任务中的原生 spawn_agent 测试失败。",
            {"stderr": proc.stderr[-1200:]},
        )
    child_ids: list[str] = []
    child_messages: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "collab_tool_call"
            and item.get("tool") == "spawn_agent"
        ):
            child_ids.extend(item.get("receiver_thread_ids") or [])
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "collab_tool_call"
            and item.get("tool") == "wait"
        ):
            for receiver_id, state in (item.get("agents_states") or {}).items():
                if not isinstance(state, dict):
                    continue
                message = state.get("message")
                if state.get("status") == "completed" and isinstance(message, str):
                    child_messages[receiver_id] = message.strip()
    child_id = child_ids[0] if len(child_ids) == 1 else None
    child_message = child_messages.get(child_id) if child_id else None
    metadata = wait_for_child_metadata(paths, child_id) if child_id else None
    expected = {
        "model_provider": PROVIDER,
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "agent_role": ROLE,
    }
    if len(child_ids) != 1 or child_message != "NATIVE_DEEPSEEK_OK" or metadata != expected:
        raise ManagerError(
            "native_route_mismatch",
            "原生子 Agent 路由验收证据不完整或不符合 DeepSeek 配置。",
            {
                "child_ids": child_ids,
                "child_message": child_message,
                "metadata": metadata,
                "expected": expected,
            },
        )
    return {"fresh_session_native": True, "child_id": child_id, **expected}


def run_tests(paths: Paths, codex_bin: str) -> dict[str, Any]:
    status = static_status(paths, codex_bin)
    if status["status"] != "configured":
        raise ManagerError("not_configured", "静态配置尚未完整，不能运行实时测试。", status)
    direct = direct_test(paths, codex_bin)
    native = native_test(paths, codex_bin)
    return result("ready", **direct, **native, new_task_required=True, restart_required=True)


def restore_backup(paths: Paths, backup: Path) -> None:
    for target in (paths.config, paths.catalog, paths.agent, paths.manifest):
        source = backup / target.name
        if source.is_file():
            atomic_write(target, source.read_bytes(), mode=0o644 if target == paths.agent else 0o600)
        elif target.is_file():
            target.unlink()


def setup(paths: Paths, codex_bin: str, api_key_stdin: bool, skip_live_test: bool) -> dict[str, Any]:
    version = codex_version(codex_bin)
    if version < MIN_CODEX_VERSION:
        raise ManagerError(
            "unsupported",
            f"Codex 版本过低：{'.'.join(map(str, version))}，最低需要 0.144.0。",
        )
    if not keychain_available():
        raise ManagerError("unsupported", "当前版本只支持 macOS Keychain。")
    credential_created = False
    if not keychain_has_key():
        if not api_key_stdin:
            return result("credential_missing", credential="deepseek_api_key")
        secret = sys.stdin.readline().strip()
        if not secret:
            raise ManagerError("credential_missing", "标准输入中没有 API Key。")
        store_keychain_key(secret)
        secret = ""
        credential_created = True

    install_result: dict[str, Any] | None = None
    try:
        install_result = install(paths, codex_bin)
        if skip_live_test:
            return result(
                "configured",
                **install_result,
                new_task_required=True,
                restart_required=True,
            )
        tested = run_tests(paths, codex_bin)
        return {**tested, **install_result}
    except Exception:
        if install_result and install_result.get("backup"):
            restore_backup(paths, Path(install_result["backup"]))
        if credential_created:
            remove_keychain_key()
        raise


def disable(paths: Paths) -> dict[str, Any]:
    if not paths.manifest.is_file():
        raise ManagerError("not_managed", "没有找到本 Skill 的管理记录，拒绝修改现有配置。")
    manifest = read_manifest(paths)
    if manifest.get("managed_agent_file") and paths.agent.is_file():
        if sha256_bytes(paths.agent.read_bytes()) != manifest.get("agent_sha256"):
            raise ManagerError(
                "conflict",
                "DeepSeek Agent 文件已被修改，拒绝停用。",
                {"path": str(paths.agent)},
            )
    changed = False
    if paths.config.is_file():
        text = paths.config.read_text()
        updated = remove_marked_block(text, ROLE_BEGIN, ROLE_END)
        if updated != text:
            parse_toml_text(updated)
            atomic_write(paths.config, updated.encode())
            changed = True
    if manifest.get("managed_agent_file") and paths.agent.is_file():
        paths.agent.unlink()
        changed = True
    return result(
        "disabled",
        changed=changed,
        agent_preserved=not bool(manifest.get("managed_agent_file")),
        credential_preserved=keychain_has_key(),
    )


def uninstall(paths: Paths, remove_credential: bool) -> dict[str, Any]:
    manifest = read_manifest(paths)
    if not manifest:
        raise ManagerError("not_managed", "没有找到本 Skill 的管理记录，拒绝修改现有配置。")
    if paths.catalog.is_file() and sha256_bytes(paths.catalog.read_bytes()) != manifest.get("catalog_sha256"):
        raise ManagerError(
            "conflict",
            "模型目录已被修改，拒绝卸载。",
            {"path": str(paths.catalog)},
        )
    backup = make_backup(paths)
    try:
        disabled = disable(paths)
        if paths.config.is_file():
            text = paths.config.read_text()
            if manifest.get("managed_provider_block"):
                text = remove_marked_block(text, PROVIDER_BEGIN, PROVIDER_END)
            if manifest.get("managed_catalog_selection"):
                previous_catalog = manifest.get("previous_model_catalog_json")
                if previous_catalog is None:
                    text = remove_top_level_key_if_value(text, "model_catalog_json", str(paths.catalog))
                elif top_level_key(text, "model_catalog_json") == str(paths.catalog):
                    text = set_top_level_key(text, "model_catalog_json", previous_catalog)
            parse_toml_text(text)
            atomic_write(paths.config, text.encode())
        catalog_removed = False
        catalog_restored = False
        if paths.catalog.is_file():
            original_backup = manifest.get("catalog_original_backup")
            if manifest.get("catalog_preexisted") and original_backup and Path(original_backup).is_file():
                atomic_write(paths.catalog, Path(original_backup).read_bytes())
                catalog_restored = True
            elif not manifest.get("catalog_preexisted"):
                paths.catalog.unlink()
                catalog_removed = True
        paths.manifest.unlink(missing_ok=True)
    except Exception:
        restore_backup(paths, backup)
        raise
    removed_credential = remove_keychain_key() if remove_credential else False
    return result(
        "uninstalled",
        disabled=disabled,
        catalog_removed=catalog_removed,
        catalog_restored=catalog_restored,
        credential_removed=removed_credential,
    )


@contextmanager
def operation_lock(paths: Paths, timeout_seconds: float = LOCK_WAIT_SECONDS):
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    with (paths.state_dir / "manager.lock").open("a+") as lock_file:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ManagerError(
                        "operation_in_progress",
                        "另一个 DeepSeek 配置操作仍在进行，请稍后重试。",
                    ) from exc
                time.sleep(min(0.1, remaining))
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "setup", "test", "repair", "disable", "uninstall"))
    parser.add_argument("--codex-home")
    parser.add_argument("--api-key-stdin", action="store_true")
    parser.add_argument("--skip-live-test", action="store_true")
    parser.add_argument("--remove-credential", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    paths = resolve_paths(args.codex_home)
    try:
        codex_bin = find_codex() if args.command in {"status", "setup", "repair", "test"} else None
        if args.command == "status":
            payload = static_status(paths, codex_bin)
        else:
            with operation_lock(paths):
                if args.command in {"setup", "repair"}:
                    payload = setup(paths, codex_bin or "", args.api_key_stdin, args.skip_live_test)
                elif args.command == "test":
                    payload = run_tests(paths, codex_bin or "")
                elif args.command == "disable":
                    payload = disable(paths)
                else:
                    payload = uninstall(paths, args.remove_credential)
        emit(payload, args.json)
        return 0 if payload["status"] not in {"partial", "credential_missing"} else 2
    except ManagerError as exc:
        emit(result(exc.code, message=str(exc), **exc.details), args.json)
        return 2
    except subprocess.TimeoutExpired:
        emit(result("timeout", message="操作超时，未输出任何凭据。"), args.json)
        return 3
    except Exception as exc:
        emit(result("failed", message=f"{type(exc).__name__}: {exc}"), args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
