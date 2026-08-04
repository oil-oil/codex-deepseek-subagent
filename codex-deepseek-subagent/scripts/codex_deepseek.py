#!/usr/bin/env python3
"""配置并验证 DeepSeek 作为 Codex 原生子 Agent。"""

from __future__ import annotations

import argparse
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MODEL = "deepseek-v4-flash"
PROVIDER = "deepseek"
ROLE = "DeepSeek"
EFFORT = "high"
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


def managed_role_block(agent_path: Path) -> str:
    path = str(agent_path).replace("\\", "\\\\").replace('"', '\\"')
    return f'''
{ROLE_BEGIN}
[agents.{ROLE}]
description = "Text-only DeepSeek subagent for coding, repository research, review, and verification."
config_file = "{path}"
{ROLE_END}
'''


def managed_config_block(agent_path: Path) -> str:
    return managed_provider_block() + managed_role_block(agent_path)


def compatible_existing(parsed: dict[str, Any], paths: Paths) -> tuple[bool, list[str]]:
    issues: list[str] = []
    provider = (parsed.get("model_providers") or {}).get(PROVIDER)
    if provider:
        if provider.get("base_url") != "https://api.deepseek.com/":
            issues.append("model_providers.deepseek.base_url")
        if provider.get("wire_api", "responses") != "responses":
            issues.append("model_providers.deepseek.wire_api")
    agent = (parsed.get("agents") or {}).get(ROLE)
    if agent and Path(agent.get("config_file", "")).expanduser() != paths.agent:
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


def merged_catalog(base: dict[str, Any], deepseek_model: dict[str, Any]) -> dict[str, Any]:
    models = [model for model in base.get("models", []) if model.get("slug") != MODEL]
    models.append(deepseek_model)
    models.sort(key=lambda item: item.get("slug", ""))
    return {"models": models}


def make_backup(paths: Paths) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = paths.state_dir / "backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    for source in (paths.config, paths.catalog, paths.agent, paths.manifest):
        if source.is_file():
            shutil.copy2(source, backup / source.name)
    return backup


def write_manifest(paths: Paths, payload: dict[str, Any]) -> None:
    atomic_write(paths.manifest, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def install(paths: Paths, codex_bin: str) -> dict[str, Any]:
    paths.home.mkdir(parents=True, exist_ok=True)
    config_text = paths.config.read_text() if paths.config.is_file() else ""
    parsed = parse_toml_text(config_text) if config_text.strip() else {}
    compatible, conflicts = compatible_existing(parsed, paths)
    if not compatible:
        raise ManagerError("conflict", "发现不兼容的现有 DeepSeek 配置。", {"fields": conflicts})
    if paths.agent.is_file() and paths.agent.read_text() != expected_agent_text():
        raise ManagerError("conflict", "现有 DeepSeek Agent 文件与目标配置不同。", {"path": str(paths.agent)})

    backup = make_backup(paths)
    try:
        deepseek_model = fetch_official_deepseek_model()
        base = load_base_catalog(codex_bin, paths, parsed)
        catalog = merged_catalog(base, deepseek_model)
        catalog_bytes = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode()

        previous_catalog_value = parsed.get("model_catalog_json")
        new_config = remove_managed_blocks(config_text)
        provider_exists = bool((parsed.get("model_providers") or {}).get(PROVIDER))
        role_exists = bool((parsed.get("agents") or {}).get(ROLE))
        agent_exists = paths.agent.is_file()
        if not provider_exists:
            new_config = new_config.rstrip() + "\n" + managed_provider_block()
        if not role_exists:
            new_config = new_config.rstrip() + "\n" + managed_role_block(paths.agent)
        new_config = set_top_level_key(new_config, "model_catalog_json", str(paths.catalog))
        parse_toml_text(new_config)
        json.loads(catalog_bytes)

        atomic_write(paths.catalog, catalog_bytes)
        if not agent_exists:
            atomic_write(paths.agent, expected_agent_text().encode(), mode=0o644)
        atomic_write(paths.config, new_config.encode())

        adopted_existing = provider_exists or role_exists or agent_exists
        manifest = {
            "schema_version": 1,
            "installed_at": datetime.now().isoformat(timespec="seconds"),
            "backup": str(backup),
            "previous_model_catalog_json": previous_catalog_value,
            "managed_provider_block": not provider_exists,
            "managed_role_block": not role_exists,
            "managed_agent_file": not agent_exists,
            "adopted_existing": adopted_existing,
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
    checks["role_registered"] = bool(role)
    checks["catalog_selected"] = Path(parsed.get("model_catalog_json", "")).expanduser() == paths.catalog
    if paths.catalog.is_file():
        try:
            data = json.loads(paths.catalog.read_text())
            checks["model_registered"] = any(item.get("slug") == MODEL for item in data.get("models", []))
        except (OSError, json.JSONDecodeError):
            checks["model_registered"] = False
            errors.append("模型目录无法解析。")
    else:
        checks["model_registered"] = False
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
        "provider_registered",
        "role_registered",
        "catalog_selected",
        "model_registered",
        "agent_content_valid",
        "credential_present",
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


def v1_test_catalog(paths: Paths, codex_bin: str, parent_model: str) -> Path:
    catalog = run_codex_models(codex_bin, paths)
    found = False
    for item in catalog.get("models", []):
        if item.get("slug") == parent_model:
            item["multi_agent_version"] = "v1"
            found = True
    if not found:
        raise ManagerError("parent_model_missing", f"测试目录中没有父模型 {parent_model}。")
    fd, name = tempfile.mkstemp(prefix="codex-deepseek-native-", suffix=".json")
    with os.fdopen(fd, "w") as handle:
        json.dump(catalog, handle)
    return Path(name)


def choose_parent_model(paths: Paths) -> str:
    parsed = parse_toml_text(paths.config.read_text())
    model = parsed.get("model")
    if isinstance(model, str) and model and not model.startswith("deepseek"):
        return model
    return "gpt-5.6-sol"


def query_child_metadata(paths: Paths, child_id: str) -> dict[str, Any] | None:
    state_db = paths.home / "state_5.sqlite"
    if not state_db.is_file():
        return None
    with sqlite3.connect(state_db) as connection:
        row = connection.execute(
            "SELECT model_provider, model, reasoning_effort, agent_role FROM threads WHERE id = ?",
            (child_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "model_provider": row[0],
        "model": row[1],
        "reasoning_effort": row[2],
        "agent_role": row[3],
    }


def native_test(paths: Paths, codex_bin: str) -> dict[str, Any]:
    parent_model = choose_parent_model(paths)
    test_catalog = v1_test_catalog(paths, codex_bin, parent_model)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    prompt = (
        'Use the native spawn_agent tool exactly once. Set agent_type to DeepSeek. '
        'Give it this task: Reply exactly NATIVE_DEEPSEEK_OK. '
        "Then wait for that subagent and return only its final response."
    )
    try:
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
                "--disable",
                "multi_agent_v2",
                "--enable",
                "multi_agent",
                "-c",
                f'model_catalog_json="{test_catalog}"',
                prompt,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
    finally:
        test_catalog.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise ManagerError("native_test_failed", "原生 spawn_agent 测试失败。", {"stderr": proc.stderr[-1200:]})
    child_ids: list[str] = []
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if item.get("type") == "collab_tool_call" and item.get("tool") == "spawn_agent":
            child_ids.extend(item.get("receiver_thread_ids") or [])
    child_id = child_ids[-1] if child_ids else None
    metadata = query_child_metadata(paths, child_id) if child_id else None
    expected = {
        "model_provider": PROVIDER,
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "agent_role": ROLE,
    }
    if "NATIVE_DEEPSEEK_OK" not in proc.stdout or metadata != expected:
        raise ManagerError(
            "native_route_mismatch",
            "子 Agent 返回了结果，但实际路由元数据不符合 DeepSeek 配置。",
            {"child_id": child_id, "metadata": metadata, "expected": expected},
        )
    return {"native": True, "child_id": child_id, **expected}


def run_tests(paths: Paths, codex_bin: str) -> dict[str, Any]:
    status = static_status(paths, codex_bin)
    if status["status"] != "configured":
        raise ManagerError("not_configured", "静态配置尚未完整，不能运行实时测试。", status)
    direct = direct_test(paths, codex_bin)
    native = native_test(paths, codex_bin)
    return result("ready", **direct, **native, restart_required=True)


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
            return result("configured", **install_result, restart_required=True)
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
    manifest = json.loads(paths.manifest.read_text())
    if not manifest.get("managed_role_block"):
        return result("disabled", changed=False, reason="现有配置由用户创建，本 Skill 未删除它。")
    text = paths.config.read_text()
    updated = remove_marked_block(text, ROLE_BEGIN, ROLE_END)
    parse_toml_text(updated)
    atomic_write(paths.config, updated.encode())
    if manifest.get("managed_agent_file") and paths.agent.is_file():
        if sha256_bytes(paths.agent.read_bytes()) == manifest.get("agent_sha256"):
            paths.agent.unlink()
    return result("disabled", changed=True, credential_preserved=keychain_has_key())


def uninstall(paths: Paths, remove_credential: bool) -> dict[str, Any]:
    disabled = disable(paths)
    manifest = json.loads(paths.manifest.read_text()) if paths.manifest.is_file() else {}
    if paths.config.is_file():
        text = paths.config.read_text()
        if manifest.get("managed_provider_block"):
            text = remove_marked_block(text, PROVIDER_BEGIN, PROVIDER_END)
        previous_catalog = manifest.get("previous_model_catalog_json")
        if previous_catalog is None:
            text = remove_top_level_key_if_value(text, "model_catalog_json", str(paths.catalog))
        elif top_level_key(text, "model_catalog_json") == str(paths.catalog):
            text = set_top_level_key(text, "model_catalog_json", previous_catalog)
        parse_toml_text(text)
        atomic_write(paths.config, text.encode())
    removed_catalog = False
    if paths.catalog.is_file() and sha256_bytes(paths.catalog.read_bytes()) == manifest.get("catalog_sha256"):
        paths.catalog.unlink()
        removed_catalog = True
    removed_credential = remove_keychain_key() if remove_credential else False
    return result(
        "uninstalled",
        disabled=disabled,
        catalog_removed=removed_catalog,
        credential_removed=removed_credential,
    )


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
        elif args.command in {"setup", "repair"}:
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
