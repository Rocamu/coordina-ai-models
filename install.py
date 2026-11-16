#!/usr/bin/env python3
"""Install Coordina locally with backups. No downloads or running-app restart."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import zipfile
from coordina import VERSION, SKILL_REVISION

ROOT = Path(__file__).resolve().parent
USER = Path.home()
STATE = Path(os.environ.get("COORDINA_HOME", str(USER/".local/share/coordina"))).expanduser()
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
BACKUP = STATE/"backups"/STAMP
PYTHON = sys.executable
RUNTIME = USER/("Library/Application Support/Coordina/runtime" if sys.platform == "darwin" else ".local/share/coordina/runtime")
os.umask(0o077)


# Transaction journal: every file the installer is about to modify is backed up
# once (its pre-install state), every file or directory it creates is recorded,
# and rollback() restores that state if any step fails or the run is interrupted.
JOURNAL = {"created": [], "modified": {}, "dirs": []}
SOURCE_MODULES = ("lifecycle.py", "coordina.py", "live.py", "routing.py", "opencode_backend.py", "deepseek_official.py", "freellm_backend.py", "portable.py")


def reset_journal():
    JOURNAL["created"].clear(); JOURNAL["modified"].clear(); JOURNAL["dirs"].clear()


def track_mkdir(path, mode=0o777):
    """mkdir -p that records every directory it creates, so rollback can remove it."""
    path = Path(path)
    missing = []
    probe = path
    while not probe.exists() and probe != probe.parent:
        missing.append(probe); probe = probe.parent
    for d in reversed(missing):
        d.mkdir(mode=mode)
        JOURNAL["dirs"].append(str(d))
    return path


def save(path):
    """Record a path before the installer (or a CLI it calls) writes to it."""
    path = Path(path)
    key = str(path)
    if key in JOURNAL["modified"]:
        return  # the pre-install copy is already kept; never overwrite it
    if path.is_file():
        target = BACKUP/key.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if key not in JOURNAL["created"]:
            JOURNAL["modified"][key] = str(target)
    elif not path.exists() and key not in JOURNAL["created"]:
        JOURNAL["created"].append(key)


def rollback():
    """Undo this run: restore modified files, delete created files and empty new dirs."""
    restored, removed = [], []
    for key, backup in JOURNAL["modified"].items():
        try:
            Path(key).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, key); restored.append(key)
        except OSError as e:
            print("No se pudo restaurar "+key+": "+str(e), file=sys.stderr)
    for key in reversed(JOURNAL["created"]):
        path = Path(key)
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path); removed.append(key)
            elif path.exists() or path.is_symlink():
                path.unlink(); removed.append(key)
        except OSError as e:
            print("No se pudo borrar "+key+": "+str(e), file=sys.stderr)
    # Directories that did not exist before this run hold only what this run put there.
    for key in reversed(JOURNAL["dirs"]):
        if os.path.isdir(key) and not os.path.islink(key):
            shutil.rmtree(key, ignore_errors=True); removed.append(key)
    return restored, removed


def preflight(args):
    """Check everything that can be checked before the first write. No network is used."""
    problems = []
    if sys.version_info < (3, 9):
        problems.append("Se necesita Python 3.9 o superior (este es "+sys.version.split()[0]+").")
    if sys.platform not in ("darwin", "linux"):
        problems.append("Supported platforms: macOS and Linux.")
    missing = [n for n in SOURCE_MODULES if not (ROOT/n).is_file()]
    if not (ROOT/"plugin/skills/coordina/SKILL.md").is_file():
        missing.append("plugin/skills/coordina/SKILL.md")
    if missing:
        problems.append("Faltan archivos del repositorio: "+", ".join(missing))
    if not os.access(USER, os.W_OK):
        problems.append("No hay permiso de escritura en "+str(USER))
    for folder in (STATE, RUNTIME):
        probe = folder
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not os.access(probe, os.W_OK):
            problems.append("No hay permiso de escritura en "+str(probe))
    full = not any(a in args for a in ("--update-skills", "--update-runtime", "--antigravity"))
    if full:
        bins = {n: shutil.which(n) for n in ("codex", "claude")}
        for path in (USER/".claude.json", USER/"Library/Application Support/Claude/claude_desktop_config.json"):
            if path.exists():
                try:
                    json.loads(path.read_text())
                except (OSError, ValueError) as e:
                    problems.append(str(path)+" no es JSON válido; corrígelo antes de instalar ("+str(e)+").")
        config = USER/".codex/config.toml"
        if config.exists() and not os.access(config, os.R_OK | os.W_OK):
            problems.append("No se puede leer o escribir "+str(config))
    if problems:
        raise SystemExit("No se instaló nada. Corrige esto y vuelve a ejecutar install.py:\n- "+"\n- ".join(problems))


def write_json(path, data):
    track_mkdir(path.parent)
    if path.exists() and json.loads(path.read_text()) == data:
        return
    save(path)
    tmp = path.with_name(path.name+".coordina-tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n")
    tmp.replace(path)


def skill_at(path):
    track_mkdir(path)
    source_root = ROOT/"plugin/skills/coordina"
    sources = [source_root/"SKILL.md"] + sorted((source_root/"references").glob("*.md"))
    for source in sources:
        dest = path/source.relative_to(source_root)
        if dest.exists() and dest.read_bytes() == source.read_bytes():
            continue
        save(dest)
        track_mkdir(dest.parent)
        tmp = dest.with_name(dest.name+".coordina-tmp")
        shutil.copy2(source, tmp)
        tmp.replace(dest)


def deploy_runtime():
    """Real files outside Desktop; all clients keep the same durable state."""
    track_mkdir(RUNTIME, mode=0o700)
    if RUNTIME.is_symlink() or RUNTIME.parent.is_symlink():
        raise SystemExit("El runtime debe ser una carpeta real fuera del Escritorio.")
    # Stage the whole runtime first; only when every file is copied and verified
    # are the live files replaced (each with an atomic rename).
    staging = RUNTIME/(".staging-"+STAMP)
    staging.mkdir(mode=0o700)
    try:
        staged = []
        for name in SOURCE_MODULES:
            src, tmp = ROOT/name, staging/name
            shutil.copyfile(src, tmp)
            tmp.chmod(0o600)
            if tmp.read_bytes() != src.read_bytes():
                raise OSError("Copia incompleta de "+name)
            staged.append(name)
        for name in staged:
            dest = RUNTIME/name
            if dest.exists() and dest.read_bytes() == (staging/name).read_bytes():
                continue
            save(dest)
            (staging/name).replace(dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def mcp_wire(client):
    config = STATE/"config.json"
    preferences = json.loads(config.read_text()) if config.exists() else {}
    interpreter = preferences.get("client_interpreters", {}).get(client, PYTHON)
    if interpreter != PYTHON and (not Path(interpreter).is_absolute() or not Path(interpreter).is_file()):
        raise ValueError("El intérprete configurado para "+client+" no existe o no es absoluto")
    return {"command":interpreter,"args":["-B",str(RUNTIME/"coordina.py"),"mcp","--client",client],
            "env":{"COORDINA_HOME":str(STATE),"PYTHONDONTWRITEBYTECODE":"1","COORDINA_RELEASE":VERSION}}


def refresh_connection_versions():
    """Change only this server's release marker so clients can reconnect normally."""
    paths = [ROOT/"plugin/.mcp.json", USER/".codex/plugins/coordina/.mcp.json",
             USER/"Library/Application Support/Claude/claude_desktop_config.json",
             USER/".claude.json"]
    paths += list((USER/".codex/plugins/cache/personal/coordina").glob("*/.mcp.json"))
    paths += list((USER/".claude/plugins/synced").glob("*/coordina*/.mcp.json"))
    changed=[]
    for path in paths:
        if not path.exists(): continue
        data=json.loads(path.read_text());server=data.get("mcpServers",{}).get("coordina")
        if not isinstance(server,dict) or server.get("env",{}).get("COORDINA_RELEASE")==VERSION: continue
        server.setdefault("env",{})["COORDINA_RELEASE"]=VERSION
        write_json(path,data);changed.append(str(path))
    path=USER/".codex/config.toml"
    if path.exists():
        text=path.read_text()
        pattern=re.compile(r"(^\[mcp_servers\.coordina\.env\][^\n]*\n)(.*?)(?=^\[|\Z)",re.M|re.S)
        match=pattern.search(text)
        if match:
            body=match.group(2);line='COORDINA_RELEASE = '+json.dumps(VERSION)+'\n'
            item=re.compile(r'^COORDINA_RELEASE\s*=[^\n]*(?:\n|$)',re.M)
            newbody=item.sub(lambda _:line,body) if item.search(body) else body.rstrip()+'\n'+line+'\n'
            updated=text[:match.start(2)]+newbody+text[match.end(2):]
            if updated!=text:
                save(path);tmp=path.with_name(path.name+'.coordina-tmp');tmp.write_text(updated);tmp.replace(path)
                changed.append(str(path))
    return changed


def configure_codex(auto_approve=False):
    """Register this server, preserving unrelated settings and policies.

    Tool auto-approval for Coordina's own MCP tools is written only when the user
    opts in (install.py --auto-approve). Without it, Codex keeps asking as usual;
    an approval line already present from an earlier opt-in is left untouched."""
    path = USER/".codex/config.toml"
    original = path.read_text() if path.exists() else ""
    text = original
    wire = mcp_wire("codex")
    sections = {
        "mcp_servers.coordina": {"command":wire["command"],"args":wire["args"]},
        "mcp_servers.coordina.env": wire["env"],
    }
    if auto_approve:
        sections["mcp_servers.coordina"]["default_tools_approval_mode"] = "approve"
        sections['plugins."coordina@personal".mcp_servers.coordina'] = {"default_tools_approval_mode":"approve"}
    for name, entries in sections.items():
        pattern = re.compile(r"^\["+re.escape(name)+r"\][ \t]*(?:#[^\n]*)?\n(?P<body>.*?)(?=^\[|\Z)", re.M|re.S)
        match = pattern.search(text)
        body = match.group("body") if match else ""
        for key, value in entries.items():
            line = key+" = "+json.dumps(value,ensure_ascii=False)+"\n"
            item = re.compile(r"^"+re.escape(key)+r"[ \t]*=[^\n]*(?:\n|$)",re.M)
            body = item.sub(lambda _:line,body) if item.search(body) else body.rstrip()+"\n"+line
        block = "["+name+"]\n"+body.rstrip()+"\n\n"
        text = text[:match.start()]+block+text[match.end():] if match else text.rstrip()+"\n\n"+block
    if text != original:
        save(path)
        track_mkdir(path.parent)
        tmp = path.with_name(path.name+".coordina-tmp")
        tmp.write_text(text)
        tmp.replace(path)


def install_antigravity():
    """Antigravity plugin: Coordina MCP (coordina_reply, coordinate) and the skill."""
    if not Path("/Applications/Antigravity.app").exists() and not (USER/".gemini/config").exists():
        return None
    target = USER/".gemini/config/plugins/coordina"
    write_json(target/"plugin.json", {"name":"coordina"})
    write_json(target/"mcp_config.json", {"mcpServers":{"coordina":mcp_wire("antigravity")}})
    skill_at(target/"skills/coordina")
    return target


def build_zip():
    out = ROOT/"Coordina-Claude.zip"
    save(out)
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        for path in sorted((ROOT/"plugin").rglob("*")):
            if path.is_file(): z.write(path, path.relative_to(ROOT/"plugin"))
    return out


def refresh_skills():
    """Update only the installed copies of the shared skill."""
    paths = [USER/".codex/skills/coordina", USER/".claude/skills/coordina", USER/".codex/plugins/coordina/skills/coordina",
             USER/".gemini/config/plugins/coordina/skills/coordina"]
    # Installed copies of this plugin only; preserve all other plugins and settings.
    paths += list((USER/".codex/plugins/cache/personal/coordina").glob("*/skills/coordina"))
    paths += list((USER/".claude/plugins/synced").glob("*/coordina*/skills/coordina"))
    installed=[]
    for path in paths:
        if path.exists():
            skill_at(path);installed.append(path)
    # Harness discovers its own user skill root, independently of Claude/Codex.
    if (USER/".dsh/skills").is_dir():
        path=USER/".dsh/skills/coordina"
        skill_at(path);installed.append(path)
    return installed


def write_skill_manifest(installed):
    """Record expected content and preserved client extensions without inference."""
    canonical=ROOT/"plugin/skills/coordina"
    files=[canonical/"SKILL.md"]+sorted((canonical/"references").glob("*.md"))
    expected={str(p.relative_to(canonical)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    copies=[]
    for path in installed:
        actual={name:hashlib.sha256((path/name).read_bytes()).hexdigest()
                if (path/name).is_file() else None for name in expected}
        extras=sorted(str(p.relative_to(path)) for p in path.rglob("*")
                      if p.is_file() and str(p.relative_to(path)) not in expected)
        copies.append({"path":str(path),"hashes":actual,"matches_canonical":actual==expected,
                       "unmanaged_files_preserved":extras})
    data={"runtime_version":VERSION,"skill_revision":SKILL_REVISION,"canonical":str(canonical),"hashes":expected,
          "extensions":{"references/local-harness.md":{"applies_to":["harness"],"preserved_rules":["P-005 ranking, cost and quality", "forum edit evidence and closing record", "memory warning one task per wave"]},
                        "references/local-foro.md":{"applies_to":["codex-new","claude-new"],"preserved_rules":["only foro-agentes MCP exception"]}},
          "installed":copies}
    path=STATE/"skill-installation-manifest.json"
    write_json(path,data)
    return data


def _interrupted(signum, frame):
    raise KeyboardInterrupt("señal "+str(signum))


def main(argv=None):
    """Run the installer as one transaction: preflight, then install, or roll back."""
    args = list(sys.argv[1:] if argv is None else argv)
    reset_journal()
    previous = signal.signal(signal.SIGTERM, _interrupted)
    try:
        preflight(args)
        install(args)
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code in (0, None):
            raise
        restored, removed = rollback()
        if not restored and not removed:
            raise
        print("La instalación falló y se deshizo: "+str(len(restored))+" archivos restaurados, "+str(len(removed))+" rutas creadas eliminadas.", file=sys.stderr)
        if JOURNAL["modified"]:
            print("Respaldos del estado anterior: "+str(BACKUP), file=sys.stderr)
        raise SystemExit("Error: "+(str(error) or type(error).__name__)+". Corrige la causa y vuelve a ejecutar install.py (es seguro repetirlo).")
    finally:
        signal.signal(signal.SIGTERM, previous)


def install(args):
    sys.argv = [sys.argv[0]] + args
    auto_approve = "--auto-approve" in args
    if "--update-skills" in sys.argv[1:]:
        write_skill_manifest(refresh_skills())
        out=build_zip()
        print("Skills y manifiesto actualizados; runtime y conexiones conservados.")
        print("ZIP para Cowork: "+str(out))
        return
    deploy_runtime()
    if "--update-runtime" in sys.argv[1:]:
        # A code/skill update must not re-register servers or alter permissions.
        write_skill_manifest(refresh_skills())
        changed=refresh_connection_versions()
        out = build_zip()
        print("Runtime y skills actualizados sin reiniciar aplicaciones ni cambiar permisos.")
        print("Las conexiones MCP ya cargadas pueden conservar el código anterior; usa la CLI actual.")
        print("Marcador de versión actualizado en "+str(len(changed))+" configuraciones de Coordina; no se cambiaron permisos.")
        print("ZIP para Cowork: "+str(out))
        if BACKUP.exists(): print("Respaldos: "+str(BACKUP))
        return
    if "--antigravity" in sys.argv[1:]:
        # Partial install: no CLI registration, no changes to Codex or Claude settings.
        target = install_antigravity()
        if not target: raise SystemExit("Antigravity no está instalado.")
        write_skill_manifest(refresh_skills())
        out = build_zip()
        print("Plugin de Antigravity: "+str(target))
        print("Skill actualizada; ZIP para Cowork: "+str(out))
        if BACKUP.exists(): print("Respaldos: "+str(BACKUP))
        return
    track_mkdir(STATE, mode=0o700)
    track_mkdir(STATE/"intercambio", mode=0o700)
    bins = {n:shutil.which(n) for n in ("codex", "claude", "gemini", "opencode")}
    conf = STATE/"config.json"
    cfg = json.loads(conf.read_text()) if conf.exists() else {}
    cfg.update(binaries=bins)
    cfg.setdefault("models", {})
    if auto_approve:
        cfg["auto_approve"] = True
    write_json(conf, cfg)
    wire = mcp_wire("desktop")
    plugin = ROOT/"plugin"
    write_json(plugin/".mcp.json", {"mcpServers":{"coordina":wire}})
    write_json(plugin/".claude-plugin/plugin.json", {"name":"coordina","version":VERSION,
               "description":"Coordina trabajos con GPT/Codex o Claude sin copiar y pegar.","author":{"name":"Coordina contributors"}})
    write_json(plugin/".codex-plugin/plugin.json", {"name":"coordina","version":VERSION,
               "description":"Colaboración local entre GPT/Codex y Claude.","skills":"./skills/","mcpServers":"./.mcp.json",
               "interface":{"displayName":"Coordina","shortDescription":"Coordina con GPT o Claude"}})
    if bins['codex']:
        skill_at(USER/'.codex/skills/coordina')
        configure_codex(auto_approve)
    if bins['claude']:
        skill_at(USER/'.claude/skills/coordina')
        path = USER/'.claude.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        data.setdefault('mcpServers', {})['coordina'] = dict(type='stdio', **mcp_wire('claude-code'))
        write_json(path, data)
    out = build_zip()
    agy = install_antigravity()
    bindir = USER/".local/bin"
    track_mkdir(bindir)
    launcher = bindir/"coordina"
    save(launcher)
    import shlex
    launcher.write_text("#!/bin/sh\nexport COORDINA_HOME="+shlex.quote(str(STATE))+"\nexport PYTHONDONTWRITEBYTECODE=1\nexec "+shlex.quote(PYTHON)+" -B "+shlex.quote(str(RUNTIME/"coordina.py"))+" \"$@\"\n")
    launcher.chmod(0o700)
    save(STATE/"last-install.json")
    (STATE/"last-install.json").write_text(json.dumps({"backup":str(BACKUP),"root":str(ROOT),"runtime":str(RUNTIME),"state":str(STATE),"python":PYTHON,
        "installed":(["Codex MCP + skill"] if bins["codex"] else [])+(["Claude Code MCP + skill"] if bins["claude"] else [])+(["Antigravity plugin (MCP + skill)"] if agy else []),
        "detected_models":[name for name, path in bins.items() if path],
        "available":["Cowork plugin ZIP"],"cowork_zip":str(out)},indent=2))
    write_skill_manifest(refresh_skills())
    print("Instalado. Respaldos: "+str(BACKUP))
    print("Aprobación automática de herramientas: "+("ACTIVADA (--auto-approve)" if cfg.get("auto_approve") is True else "desactivada (por defecto; ver SECURITY.md)"))
    print("Plugin para Cowork: "+str(out))
    print("Diagnóstico: coordina doctor; primer encargo: coordina run 'tu consulta'")


if __name__ == "__main__":
    main()
