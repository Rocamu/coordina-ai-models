"""Bounded OpenCode executor using the installed desktop CLI and its own auth.

This is a tool permission boundary, not an operating-system sandbox. No API
credentials are copied and no existing desktop session is continued.
"""
import json
import os
import hashlib
import uuid
from pathlib import Path

MODEL = "opencode/deepseek-v4.1-flash-free"
MODELS = {"deepseek": MODEL, "glm": "opencode/glm-5.3-flash-free",
          "nemotron": "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b"}

def selected_model(spec):
    return MODELS[spec.get("target", "deepseek")]
BINARY = "/Applications/OpenCode.app/Contents/Resources/opencode-cli"
AGENT = "coordina-executor"

import os
# Allowed root for output_path. Override with COORDINA_OUTPUT_ROOT.
OUTPUT_ROOT = Path(os.environ.get("COORDINA_OUTPUT_ROOT", str(Path.home() / "Documents/Coordina/outputs"))).expanduser()

def validate_output_path(value):
    p = Path(value)
    if not p.is_absolute() or ".." in p.parts or not p.name:
        raise ValueError("output_path debe ser una ruta absoluta sin ..")
    try:
        rel = p.relative_to(OUTPUT_ROOT)
    except ValueError:
        raise ValueError("output_path solo admite rutas bajo COORDINA_OUTPUT_ROOT (" + str(OUTPUT_ROOT) + ")")
    if not rel.parts:
        raise ValueError("output_path debe indicar un archivo")
    return p

def write_response(value, answer):
    """Publish exact UTF-8 text once, without following directory symlinks."""
    p = validate_output_path(value)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    temp = ".coordina-output-" + uuid.uuid4().hex
    created = False
    try:
        for component in p.parent.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        data = answer.encode("utf-8")
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        created = True
        with os.fdopen(out, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # link fails atomically if the destination already exists, including a symlink.
        os.link(temp, p.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
        return {"path": str(p), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    finally:
        if created:
            os.unlink(temp, dir_fd=fd)
        os.close(fd)

def check_output_access(value):
    """Check from the requesting MCP process before spending inference quota."""
    p = validate_output_path(value)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    temp = ".coordina-probe-" + uuid.uuid4().hex
    created = False
    try:
        for component in p.parent.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            os.stat(p.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("output_path ya existe; no se sobrescribe")
        probe = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        created = True
        os.close(probe)
        return {"path": str(p), "writable": True, "model_called": False}
    finally:
        if created:
            os.unlink(temp, dir_fd=fd)
        os.close(fd)


def command(spec):
    if not Path(BINARY).is_file():
        raise ValueError("Instala OpenCode desktop: falta su CLI empaquetada")
    return [BINARY, "run", "--standalone", "--agent", AGENT,
            "--model", selected_model(spec), "--format", "json"]


def environment(spec):
    root = str(Path(spec["workspace"]).resolve())
    actions = ["read", "glob", "grep"]
    if spec["mode"] == "edit":
        actions.append("edit")
    permissions = [{"action": "*", "resource": "*", "effect": "deny"}]
    permissions += [{"action": a, "resource": "*", "effect": "allow"} for a in actions]
    policies = [{"action": a, "resource": "*", "effect": "deny"}
                for a in ("tool.use", "mcp.use", "skill.use", "plugin.use", "provider.use")]
    policies.append({"action": "provider.use", "resource": selected_model(spec).split("/", 1)[0], "effect": "allow"})
    for a in actions:
        policies.append({"action": "tool.use", "resource": a + ":*", "effect": "allow"})
    # Internal read/edit resources are Location-relative, external ones absolute.
    # Also deny ../ into the surrounding Git worktree, which OpenCode may regard
    # as internal even when it lies outside the requested workspace.
    denied = ["external_directory:*", "read:/*", "edit:/*", "read:../*", "edit:../*",
              "read:*../*", "edit:*../*", "glob:../*", "glob:/*"]
    for suffix in (".env", ".env.*", ".ssh/*", ".git/*"):
        denied += ["read:" + suffix, "read:*/" + suffix]
    for suffix in (".git/*", ".opencode/*", "opencode.json*", ".env*"):
        denied += ["edit:" + suffix, "edit:*/" + suffix]
    for resource in denied:
        policies.append({"action": "tool.use", "resource": resource, "effect": "deny"})
    config = {"agents": {AGENT: {"mode": "primary", "description": "Ejecutor acotado de Coordina",
                                "permissions": permissions}}, "policies": policies}
    # The CLI also consults PWD: cwd alone can retain the caller's Location.
    return {"PWD": root, "OPENCODE_CONFIG_CONTENT": json.dumps(config)}


def parse(raw, spec=None):
    texts, denied, sessions = [], [], set()
    cost = 0
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part") or {}
        if event.get("sessionID"):
            sessions.add(event["sessionID"])
        if event.get("type") == "error":
            raise RuntimeError("OpenCode: " + json.dumps(event.get("error", event), ensure_ascii=False)[:2500])
        if event.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
        if event.get("type") == "step_finish":
            cost += part.get("cost", 0) or 0
        if event.get("type") == "tool_use" and part.get("state", {}).get("status") == "error":
            denied.append({"tool": part.get("tool"), "error": str(part["state"].get("error", ""))[:500]})
    return "\n\n".join(texts), {"model": selected_model(spec or {}), "session_ids": sorted(sessions),
                                   "reported_cost": cost, "tool_errors": denied,
                                   "permission_boundary": "OpenCode policies; no shell, MCP, plugins or subagents"}
