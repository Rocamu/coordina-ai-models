"""Existing-session routing. Native local IPC; never resume an already-running CLI.

Wire-format research: installed Codex desktop bundle and Claude's session metadata;
see docs/native-protocols.md. No user data is written to the applications' stores.
"""
import contextlib
import datetime
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid

import coordina as b
import lifecycle


def codex_home():
    return Path(os.environ.get("CODEX_HOME",str(Path.home()/".codex")))


def claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR",str(Path.home()/".claude")))


def private_path(path, kind, metadata=False):
    p=Path(path);s=p.lstat()
    good={"socket":stat.S_ISSOCK,"file":stat.S_ISREG,"directory":stat.S_ISDIR}[kind](s.st_mode)
    if not good or stat.S_ISLNK(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode & (0o022 if metadata else 0o077):
        raise ValueError("Propietario, tipo o permisos inesperados en la ruta local de mensajería")
    return p


def read_metadata(path, limit=16384, metadata=True):
    p=private_path(path,"file",metadata)
    if p.stat().st_size>limit:raise ValueError("Metadatos demasiado extensos")
    return json.loads(p.read_text())


@contextlib.contextmanager
def read_db(path):
    p=private_path(path,"file",True)
    c=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=2)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    try:yield c
    finally:c.close()


# Codex answers at once for a loaded thread, but takes 10 s to answer no-client-found
# for one that is not loaded. A shorter wait reports a healthy app as unavailable.
OWNER_WAIT=14
WAKE_WAIT=45
WAKE_REPEAT=180
WOKEN={}


class NativeIPC:
    def __init__(self,path=None,timeout=2):
        self.path=Path(path) if path else codex_home()/"ipc/ipc.sock"
        self.timeout=timeout;self.sock=None;self.client_id="initializing-client"

    def __enter__(self):
        private_path(self.path.parent,"directory");private_path(self.path,"socket")
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            self.sock.settimeout(self.timeout);self.sock.connect(str(self.path))
            p=self.request("initialize",{"clientType":"coordina"})
            if p.get("resultType")!="success" or not isinstance(p.get("result",{}).get("clientId"),str):
                raise ValueError("Codex rechazó la conexión local")
            self.client_id=p["result"]["clientId"]
            return self
        except BaseException:
            self.close();raise

    def close(self):
        if self.sock:self.sock.close();self.sock=None

    def __exit__(self,*_):self.close()

    def readn(self,n):
        result=b""
        while len(result)<n:
            part=self.sock.recv(n-len(result))
            if not part:raise ConnectionError("Codex cerró la conexión local")
            result+=part
        return result

    def write(self,packet):
        data=b.dumps(packet).encode();self.sock.sendall(struct.pack("<I",len(data))+data)

    def request(self,method,params,version=0,target=None):
        rid=str(uuid.uuid4())
        p=dict(type="request",requestId=rid,sourceClientId=self.client_id,version=version,method=method,params=params)
        if target:p["targetClientId"]=target
        self.write(p)
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            self.sock.settimeout(max(.05,deadline-time.monotonic()))
            n=struct.unpack("<I",self.readn(4))[0]
            if n>8*1024*1024:raise ValueError("Trama IPC mayor de 8 MiB")
            p=json.loads(self.readn(n))
            if p.get("type")=="client-discovery-request":
                self.write(dict(type="client-discovery-response",requestId=p["requestId"],response={"canHandle":False}))
            elif p.get("type")=="response" and p.get("requestId")==rid:return p
            # Unsolicited broadcasts are discarded, never retained as session history.
        raise TimeoutError("Codex no respondió a la consulta local")

    @staticmethod
    def owner_result(p):
        if p.get("resultType")=="success" and p.get("handledByClientId"):
            return {"client_id":p["handledByClientId"],"untrusted_input":p.get("result",{}).get("supportsUntrustedAppInput") is True}
        if p.get("error")=="no-client-found":return None
        raise ValueError("Codex no pudo verificar el propietario de la conversación: "+str(p.get("error")))

    def owner(self,session_id):
        return self.owner_result(self.request("thread-owner-discovery",{"hostId":"local","conversationId":session_id},1))

    def owners(self,session_ids):
        """Ask for every thread at once, so N unloaded threads cost one 10 s wait, not N."""
        pending={}
        for session_id in session_ids:
            rid=str(uuid.uuid4());pending[rid]=session_id
            self.write(dict(type="request",requestId=rid,sourceClientId=self.client_id,version=1,method="thread-owner-discovery",
                            params={"hostId":"local","conversationId":session_id}))
        result={};deadline=time.monotonic()+self.timeout
        while pending and time.monotonic()<deadline:
            self.sock.settimeout(max(.05,deadline-time.monotonic()))
            n=struct.unpack("<I",self.readn(4))[0]
            if n>8*1024*1024:raise ValueError("Trama IPC mayor de 8 MiB")
            p=json.loads(self.readn(n))
            if p.get("type")=="client-discovery-request":
                self.write(dict(type="client-discovery-response",requestId=p["requestId"],response={"canHandle":False}))
            elif p.get("type")=="response" and p.get("requestId") in pending:
                result[pending.pop(p["requestId"])]=self.owner_result(p)
        if pending:raise TimeoutError("El inventario de Codex quedó incompleto; no se creará otra sesión por este motivo")
        return result


def wake_codex(session_id,wait=None):
    """An ownerless thread exists but is not loaded in the app, for example after a restart.
    Its own link loads that same thread; it never creates another or selects a different one."""
    if not re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",session_id or ""):return None
    now=time.monotonic()
    if now-WOKEN.get(session_id,-WAKE_REPEAT)<WAKE_REPEAT:return None
    WOKEN[session_id]=now
    try:
        if subprocess.run(["/usr/bin/open","-g","codex://threads/"+session_id],capture_output=True,timeout=10,stdin=subprocess.DEVNULL).returncode:return None
    except (OSError,subprocess.SubprocessError):return None
    deadline=time.monotonic()+(WAKE_WAIT if wait is None else wait)
    while time.monotonic()<deadline:
        try:
            with NativeIPC(timeout=OWNER_WAIT) as ipc:
                owner=ipc.owner(session_id)
            if owner:return owner
        except (OSError,ValueError,TimeoutError,ConnectionError):pass  # The app may still be starting.
        time.sleep(2)
    return None


def process_is_live(row):
    if type(row.get("pid")) is not int or row["pid"]<=0:return False
    if not b.config().get("experimental", {}).get("antigravity", False):
        return []
    env=dict(os.environ,TZ="UTC",LC_ALL="C")
    p=subprocess.run(["/bin/ps","-p",str(row["pid"]),"-o","uid=","-o","lstart="],capture_output=True,text=True,timeout=2,env=env)
    parts=p.stdout.strip().split(None,1)
    return p.returncode==0 and len(parts)==2 and int(parts[0])==os.getuid() and " ".join(parts[1].split())==" ".join(row.get("procStart","").split())


def claude_sessions():
    root=claude_home()/"sessions"
    if not root.exists():return []
    private_path(root,"directory")
    found=[]
    for p in root.glob("*.json"):
        if not re.fullmatch(r"\d+\.json",p.name):continue
        try:
            d=read_metadata(p)
            if p.stem!=str(d.get("pid")) or d.get("peerProtocol")!=1:continue
            address=Path(d["messagingSocketPath"])
            allowed=[Path("/private/tmp/cc-socks"),Path("/private/tmp/cc-socks-"+str(os.getuid()))]
            if address.parent.resolve() not in allowed:continue
            if not re.fullmatch(str(d["pid"])+r"(?:-[0-9a-f]{8})?\.sock",address.name):continue
            private_path(address.parent,"directory");private_path(address,"socket")
            if not process_is_live(d):continue
            found.append(dict(family="claude",session_id=d["sessionId"],title=d.get("name") or d["sessionId"],
                              workspace=d.get("cwd",""),roots=[d.get("cwd","")],status=d.get("status","unknown"),
                              pid=d["pid"],procStart=d["procStart"],socket=str(address),version=d.get("version"),
                              host_session_id=d.get("hostSessionId"),live=True))
        except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):
            continue
    return found


def codex_metadata(limit=250,session_id=None,session_hint=None):
    paths=list(codex_home().glob("state_*.sqlite"))
    if not paths:return []
    path=max(paths,key=lambda p:int(re.search(r"state_(\d+)",p.name).group(1)))
    roots={};known=set()
    p=codex_home()/".codex-global-state.json"
    if p.exists():
        # Metadata only. Never modify this application-owned file.
        d=read_metadata(p,16*1024*1024)
        roots=d.get("thread-writable-roots",{})
        activity=d.get("electron-persisted-atom-state",{}).get("thread-user-activity-times-v1",{})
        for item in activity:
            try:
                host,tid=json.loads(item)
                if host=="local":known.add(tid)
            except (TypeError,ValueError):continue
    with read_db(path) as c:
        cols={x[1] for x in c.execute("PRAGMA table_info(threads)")}
        if not {"id","title","cwd","archived","updated_at"}<=cols:raise ValueError("Esquema de conversaciones de Codex desconocido")
        title="COALESCE(NULLIF(name,''),title)" if "name" in cols else "title"
        if session_id:where="id=?";args=[session_id]
        elif session_hint:
            c.create_function("coordina_casefold",1,lambda value:(value or "").casefold(),deterministic=True)
            where="(id=? OR INSTR(coordina_casefold("+title+"),?)>0)";args=[session_hint,session_hint.casefold()]
        elif known:
            if len(known)>limit:raise ValueError("El inventario excede el límite de comprobación; selecciona una conversación por nombre")
            where="id IN ("+",".join("?" for _ in known)+")";args=sorted(known)
        else:raise ValueError("No se pudo identificar el inventario de conversaciones del escritorio; se requiere un destino explícito")
        # Filter before limiting: an older open or explicitly named chat must not
        # disappear just because many other conversations were updated recently.
        rows=[dict(r) for r in c.execute("SELECT id,"+title+" AS title,cwd,updated_at FROM threads WHERE archived=0 AND "+where+" ORDER BY updated_at DESC LIMIT ?",args+[limit+1])]
        if len(rows)>limit:raise ValueError("Demasiadas coincidencias; precisa el nombre de la conversación")
    return [dict(family="codex",session_id=r["id"],title=r["title"],workspace=r["cwd"],
                 roots=[r["cwd"]]+(roots.get(r["id"],[]) if isinstance(roots.get(r["id"],[]),list) else []),
                 updated_at=r["updated_at"],status="open",live=True) for r in rows]


AGY_SERVICE="exa.language_server_pb.LanguageServerService/"
AGY_EXE=re.compile(r"(/.+?/Antigravity[^/]*\.app/Contents/Resources/bin/language_server)(?: |$)")
AGY_IDLE="CASCADE_RUN_STATUS_IDLE"
AGY_INPUTS=("CORTEX_STEP_TYPE_USER_INPUT","CORTEX_STEP_TYPE_SYSTEM_MESSAGE")


def antigravity_servers():
    """Language servers of the running Antigravity app owned by this user.

    The app passes its CSRF token on the command line, as it does to its own
    sidecars; it is read only from processes of this user and this bundle.
    """
    env=dict(os.environ,TZ="UTC",LC_ALL="C")
    try:p=subprocess.run(["/bin/ps","-axww","-o","pid=","-o","uid=","-o","lstart=","-o","command="],capture_output=True,text=True,timeout=4,env=env)
    except subprocess.SubprocessError as e:raise OSError("No se pudo listar procesos: "+str(e))
    found=[]
    for line in p.stdout.splitlines():
        parts=line.split(None,7)
        if len(parts)<8 or not parts[0].isdigit() or parts[1]!=str(os.getuid()):continue
        m=AGY_EXE.match(parts[7]);csrf=re.search(r"--csrf_token[ =]([0-9A-Za-z-]{16,128})(?: |$)",parts[7])
        if not m or not csrf:continue
        exe=Path(m.group(1))
        try:
            s=exe.lstat()
            listen=subprocess.run(["/usr/sbin/lsof","-nP","-a","-p",parts[0],"-iTCP","-sTCP:LISTEN","-Fn"],capture_output=True,text=True,timeout=4)
        except (OSError,subprocess.SubprocessError):continue
        if not stat.S_ISREG(s.st_mode) or s.st_uid not in (0,os.getuid()) or s.st_mode & 0o022:continue
        for host,port in re.findall(r"^n(127\.0\.0\.1|\[::1\]):(\d+)$",listen.stdout,re.M):
            server=dict(pid=int(parts[0]),procStart=" ".join(parts[2:7]),exe=str(exe),csrf=csrf.group(1),address=host+":"+port)
            try:
                # Only the plain local port speaks both Connect JSON and agentapi's gRPC.
                server["summaries"]=agy_rpc(server,"GetAllCascadeTrajectories",{},4).get("trajectorySummaries") or {}
            except (OSError,ValueError,http.client.HTTPException):continue
            found.append(server);break
    return found


def agy_rpc(server,method,body,timeout=10):
    request=urllib.request.Request("http://"+server["address"]+"/"+AGY_SERVICE+method,data=b.dumps(body).encode(),method="POST",
        headers={"Content-Type":"application/json","x-codeium-csrf-token":server["csrf"]})
    # Loopback only; never route the token through a configured proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=timeout) as r:
        data=r.read(32*1024*1024+1)
    if len(data)>32*1024*1024:raise ValueError("Respuesta de Antigravity mayor de 32 MiB")
    d=json.loads(data)
    if not isinstance(d,dict):raise ValueError("Respuesta de Antigravity inesperada")
    return d


def file_uri_path(uri):
    u=urllib.parse.urlparse(uri or "")
    return urllib.parse.unquote(u.path) if u.scheme=="file" and u.path else ""


def iso_time(value):
    try:return datetime.datetime.fromisoformat(re.sub(r"(\.\d{6})\d*","\\1",str(value)).replace("Z","+00:00")).timestamp()
    except ValueError:return 0


def antigravity_sessions(servers=None):
    servers=antigravity_servers() if servers is None else servers
    if not servers:raise ValueError("Antigravity no está abierto o su servidor local no respondió; no se puede comprobar sus conversaciones")
    found=[];seen=set()
    for server in servers:
        for cid,s in server["summaries"].items():
            if cid in seen:continue
            seen.add(cid);meta=s.get("trajectoryMetadata") or {}
            # Subagent trajectories belong to their parent conversation.
            if s.get("trajectoryType","CORTEX_TRAJECTORY_TYPE_CASCADE")!="CORTEX_TRAJECTORY_TYPE_CASCADE" or meta.get("parentConversationId"):continue
            roots=[p for p in (file_uri_path(w.get("workspaceFolderAbsoluteUri")) for w in s.get("workspaces",[])) if p]
            found.append(dict(family="antigravity",session_id=cid,title=(s.get("annotations") or {}).get("title") or s.get("summary") or cid,
                              workspace=roots[0] if roots else "",roots=roots,status=s.get("status","unknown"),
                              updated_at=iso_time(s.get("lastModifiedTime")),project_id=meta.get("projectId"),
                              step_count=s.get("stepCount",0),live=True))
    return found


def antigravity_locate(session_id):
    """The live server that currently hosts a conversation, with its summary."""
    for server in antigravity_servers():
        if session_id in server["summaries"]:return server,server["summaries"][session_id]
    return None,None


def agentapi(server,args,project_id=None,timeout=60):
    env=dict(os.environ,ANTIGRAVITY_LS_ADDRESS=server["address"],ANTIGRAVITY_CSRF_TOKEN=server["csrf"])
    env.pop("ANTIGRAVITY_PROJECT_ID",None)
    if project_id:env["ANTIGRAVITY_PROJECT_ID"]=project_id
    # The app's published agent CLI (the same one its sidecars use); argv, no shell.
    try:p=subprocess.run([server["exe"],"agentapi"]+args,capture_output=True,text=True,timeout=timeout,env=env,stdin=subprocess.DEVNULL)
    except (OSError,subprocess.SubprocessError) as e:return {"error":"agentapi no terminó: "+str(e)}
    try:d=json.loads(p.stdout)
    except ValueError:d={"error":(p.stderr or p.stdout)[-2000:] or "agentapi terminó con código "+str(p.returncode)}
    if not isinstance(d,dict):d={"error":"Respuesta de agentapi inesperada"}
    return d


def antigravity_project(workspace):
    """Read-only lookup of the Antigravity project whose folder contains workspace."""
    root=Path(workspace).resolve();best=(None,-1)
    for p in (Path.home()/".gemini/config/projects").glob("*.json"):
        try:d=read_metadata(p,256*1024)
        except (OSError,ValueError):continue
        for r in (d.get("projectResources") or {}).get("resources",[]):
            folder=file_uri_path(r.get("folderUri"))
            if folder and (Path(folder).resolve()==root or Path(folder).resolve() in root.parents) and len(folder)>best[1]:best=(d.get("id"),len(folder))
    return best[0] or "outside-of-project"


def start_antigravity(spec,request_id=None,launch=True):
    """New-session route: a new Antigravity conversation, never a hidden executor."""
    if spec["rounds"]!=1:raise ValueError("Antigravity admite un turno por encargo; continúa el diálogo con otro encargo a la misma conversación")
    native_db()
    endpoint=dict(family="antigravity",session_id=None,new_conversation=True,title="Coordina (nueva conversación)",
                  workspace=spec["workspace"],roots=[spec["workspace"]],status="new",project_id=antigravity_project(spec["workspace"]),
                  task_title=spec["task"].strip().splitlines()[0][:60])
    job=b.enqueue_job(spec,request_id,launch=False)
    with b.database() as c:c.execute("INSERT OR IGNORE INTO native_exchanges(job_id,endpoint,reply_key) VALUES(?,?,?)",(job["id"],b.dumps(endpoint),secrets.token_hex(16)))
    if launch:launch_observer(job["id"])
    return b.job_status(job["id"])


def send_antigravity(endpoint,text,jid):
    if endpoint.get("new_conversation"):
        servers=antigravity_servers()
        if not servers:return {"state":"offline","submitted":False}
        server=servers[0];title=("Coordina: "+" ".join(endpoint.get("task_title","").split()))[:80].rstrip(": ")
        model=b.config().get("models",{}).get("antigravity") or "flash"
        d=agentapi(server,["new-conversation","--model="+model,"--title="+title,"--",text],endpoint.get("project_id"))
        cid=(d.get("response") or {}).get("newConversation",{}).get("conversationId")
        if d.get("error") or not cid:return {"state":"delivery_unknown","submitted":True,"detail":"Antigravity no confirmó la conversación nueva: "+str(d.get("error"))[:500]}
        return {"state":"accepted","submitted":True,"session_id":cid,"step_offset":0,"model":model,"title":title}
    server,summary=antigravity_locate(endpoint["session_id"])
    if not server:return {"state":"offline","submitted":False}
    if summary.get("status")!=AGY_IDLE:return {"state":"busy","submitted":False}
    offset=int(summary.get("stepCount") or 0)
    # Delivered as an agent message ("[Message] sender=system"), not as user input.
    d=agentapi(server,["send-message","--title=Coordina",endpoint["session_id"],text])
    if d.get("error") or not (d.get("response") or {}).get("sendMessage"):
        return {"state":"delivery_unknown","submitted":True,"detail":"Antigravity no confirmó la entrega: "+str(d.get("error"))[:500]}
    return {"state":"accepted","submitted":True,"step_offset":offset}


def step_text(step):
    if step.get("type")=="CORTEX_STEP_TYPE_USER_INPUT":
        return "\n".join(x.get("text","") for x in (step.get("userInput") or {}).get("items",[]) if isinstance(x,dict))
    if step.get("type")=="CORTEX_STEP_TYPE_SYSTEM_MESSAGE":
        return ((step.get("systemMessage") or {}).get("agentMessage") or {}).get("content","")
    return ""


def antigravity_response(endpoint,receipt,jid):
    try:
        server,summary=antigravity_locate(endpoint["session_id"])
        if not server:return None
        steps=agy_rpc(server,"GetCascadeTrajectorySteps",{"cascadeId":endpoint["session_id"],"stepOffset":receipt.get("step_offset",0)},20).get("steps") or []
    except (OSError,ValueError,http.client.HTTPException):
        # A restarting app is not a lost reply: keep waiting until the job timeout.
        return None
    marker="COORDINA["+jid+"]";start=None
    for i,step in enumerate(steps):
        if step.get("type") in AGY_INPUTS and marker in step_text(step):start=i;break
    if start is None:return None
    answers=[];waiting=False;errors=[]
    # The reply is bounded by the next input: later turns are never attributed.
    for step in steps[start+1:]:
        if step.get("type") in AGY_INPUTS:break
        if step.get("status")=="CORTEX_STEP_STATUS_WAITING":waiting=True
        if step.get("type")=="CORTEX_STEP_TYPE_ERROR_MESSAGE":errors.append(json.dumps(step.get("errorMessage",{}),ensure_ascii=False)[:500])
        r=(step.get("plannerResponse") or {}).get("response","")
        if step.get("type")=="CORTEX_STEP_TYPE_PLANNER_RESPONSE" and step.get("status")=="CORTEX_STEP_STATUS_DONE" and r.strip():answers.append(r)
    if summary.get("status")!=AGY_IDLE:return {"state":"waiting_permission"} if waiting else None
    if answers:
        tagged=[x for x in answers if marker in x]
        return {"state":"completed","text":(tagged or answers)[-1],"marker":bool(tagged)}
    if errors:return {"state":"failed","error":"Antigravity terminó el turno con error: "+errors[-1]}
    return None


# Optional remote-GPU agent client. Set joker.cli in config.json; this default is a placeholder.
JOKER_CLI_DEFAULT=""
JOKER_DONE=("done","completed")
JOKER_FAILED=("error","failed","cancelled")


def joker_cli(args,timeout=60):
    """THE JOKER through its own local client (an optional, user-provided client).

    The client holds THE JOKER's authentication; Coordina never reads it. It
    prints one JSON object on stdout. config.json may set joker.cli and joker.python.
    """
    cfg=b.config().get("joker") or {}
    cli=Path(cfg.get("cli") or JOKER_CLI_DEFAULT).expanduser()
    if not cli.is_file():raise ValueError("THE JOKER no tiene cliente local para Coordina en "+str(cli))
    cmd=[cfg.get("python") or sys.executable,str(cli)]+args
    for intento in range(5):
        try:p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,stdin=subprocess.DEVNULL)
        except (OSError,subprocess.SubprocessError) as e:raise OSError("El cliente de THE JOKER no terminó: "+str(e))
        try:d=json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError,IndexError):raise ValueError("Respuesta del cliente de THE JOKER no legible: "+(p.stderr or p.stdout)[-500:])
        if not isinstance(d,(dict,list)):raise ValueError("Respuesta del cliente de THE JOKER inesperada")
        # La CDN de Hostinger (hcdn) devuelve a veces un 307 transitorio. Reintentar es seguro: las consultas no cambian
        # nada y `enviar` lleva el request_id del encargo, que el servidor usa para no duplicar el trabajo.
        if isinstance(d,dict) and (d.get("error")=="redireccion_rechazada" or d.get("http_status")==307) and intento<4:
            time.sleep(3*(intento+1));continue
        return d


QWENCTL_DEFAULT=""  # external plugin must explicitly configure its controller
JOKER_MIN_REMAINING=10*60  # segundos de plazo que debe quedar en una sesión ajena para usarla sin renovarla


def qwen_status():
    """GPU principal de QWEN (la que usa THE JOKER), leída con qwenctl. Nunca cambia nada."""
    cfg=b.config().get("joker") or {};ctl=cfg.get("qwenctl") or QWENCTL_DEFAULT
    try:p=subprocess.run([ctl,"status","--node","principal"],capture_output=True,text=True,timeout=90,stdin=subprocess.DEVNULL)
    except (OSError,subprocess.SubprocessError) as e:raise OSError("qwenctl no respondió: "+str(e))
    d=None
    for line in reversed(p.stdout.strip().splitlines()):
        try:d=json.loads(line);break
        except ValueError:continue
    if not isinstance(d,dict):raise ValueError("Estado de la GPU no legible: "+(p.stderr or p.stdout)[-300:])
    t=d.get("telemetry") or {};pod=d.get("pod") or {}
    running=pod.get("status")=="RUNNING" and pod.get("runtime_status") in (None,"running")
    deadline=float((t.get("session") or {}).get("deadline_epoch") or 0)
    ready=running and bool(t.get("service_ready")) and (t.get("bootstrap") or {}).get("stage")=="ready"
    return dict(running=running,ready=ready,deadline=deadline,remaining=deadline-time.time() if deadline else 0,pod_id=pod.get("id"),rate=pod.get("rate"))


def qwen_start():
    """Pide encender la GPU principal. Devuelve el texto de qwenctl, que incluye el id de la operación en segundo plano."""
    cfg=b.config().get("joker") or {};ctl=cfg.get("qwenctl") or QWENCTL_DEFAULT;minutes=str(cfg.get("gpu_minutes") or 60)
    try:p=subprocess.run([ctl,"start","--node","principal","--minutes",minutes,"--background"],capture_output=True,text=True,timeout=180,stdin=subprocess.DEVNULL)
    except (OSError,subprocess.SubprocessError) as e:raise OSError("qwenctl start no terminó: "+str(e))
    return (p.stdout or p.stderr)[-500:]


def qwen_start_failed(salida):
    """True si la operación de encendido ya terminó con error (p. ej., el proveedor no tenía GPU disponible)."""
    m=re.search(r'"operation(?:_id)?"\s*:\s*"([0-9a-f]{32})"',salida or "") or re.search(r"\b([0-9a-f]{32})\b",salida or "")
    if not m:return False
    directory=(b.config().get("joker") or {}).get("operations_directory")
    if not directory:return False
    f=Path(directory)/(m.group(1)+".json")
    try:return json.loads(f.read_text()).get("state")=="failed"
    except (OSError,ValueError):return False


def qwen_stop():
    cfg=b.config().get("joker") or {};ctl=cfg.get("qwenctl") or QWENCTL_DEFAULT
    subprocess.run([ctl,"stop","--node","principal"],capture_output=True,text=True,timeout=180,stdin=subprocess.DEVNULL)


def joker_marker():
    return b.home()/"joker_gpu.json"


def joker_owned(pod_id):
    """La GPU encendida es la que encendió Coordina (marcador vigente para el mismo pod)."""
    try:d=json.loads(joker_marker().read_text())
    except (OSError,ValueError):return False
    return bool(pod_id) and d.get("pod_id")==pod_id


def joker_gpu(jid,deadline):
    """Coordina el uso de la GPU antes de entregar a THE JOKER.

    - Encendida, lista y con plazo suficiente: se usa tal cual, sin renovar plazos de nadie. Si la encendió
      Coordina (marcador joker_gpu.json), este encargo hereda la responsabilidad de apagarla.
    - Encendida pero con menos de 10 minutos de plazo: es la sesión de otro operador o está por cerrarse;
      se espera a que termine y no se renueva.
    - Apagada: se enciende por joker.gpu_minutes (60 por omisión; requiere autorización previa del propietario)
      y se espera a que esté lista.
    Más estrategias de eficiencia: docs/joker-eficiencia-gpu.md.
    Devuelve {"state": "ready", "started_by_coordina": bool} o {"state": "busy"|"offline", "detail": ...}.
    """
    started=False;log=[];pedido="";reintento=0
    while time.monotonic()<deadline:
        st=qwen_status()
        if st["ready"] and st["remaining"]>=JOKER_MIN_REMAINING:
            if started:joker_marker().write_text(b.dumps({"pod_id":st["pod_id"],"started_at":time.time(),"job_id":jid}))
            return {"state":"ready","started_by_coordina":started or joker_owned(st["pod_id"]),"pod_id":st["pod_id"],"log":log}
        if st["running"] and not started and st["remaining"]<JOKER_MIN_REMAINING:
            log.append("sesión ajena con poco plazo; se espera su cierre");time.sleep(30);continue
        if not st["running"] and (not started or (pedido and qwen_start_failed(pedido) and time.monotonic()>=reintento)):
            # Sin GPU disponible en el proveedor la operación falla en segundos: se reintenta cada 2 minutos dentro del plazo.
            if started:log.append("encendido fallido (¿sin GPU disponible?); se reintenta")
            pedido=qwen_start();log.append(pedido);started=True;reintento=time.monotonic()+120
            b.update_job(jid,state="starting_gpu")
        time.sleep(20)
    sin_gpu=any("encendido fallido" in str(x) for x in log) or (pedido and qwen_start_failed(pedido))
    return {"state":"busy" if not started else "offline","detail":"El proveedor de GPU no tuvo GPU disponible para encender THE JOKER dentro del plazo" if sin_gpu
            else "La GPU de THE JOKER no quedó lista dentro del plazo del encargo","started_by_coordina":started,"log":log[-6:]}


JOKER_ACTIVE=("queued","starting_native","running","starting_gpu","waiting_destination","awaiting_response")


def joker_pending(jid):
    """Otros encargos de Coordina a THE JOKER que todavía usarán la GPU (incluidos los de un lote en espera)."""
    with b.database() as c:
        rows=c.execute("SELECT id,spec FROM jobs WHERE id<>? AND state IN ("+",".join("?"*len(JOKER_ACTIVE))+")",(jid,)+JOKER_ACTIVE).fetchall()
    return [r[0] for r in rows if json.loads(r[1]).get("target")=="joker"]


def joker_release(started,jid=""):
    """Si Coordina encendió la GPU, la apaga al terminar, con un período de gracia.

    - Si hay otros encargos de Coordina a THE JOKER pendientes, la responsabilidad pasa a ellos y no se apaga.
    - Si no, espera joker.gracia_min minutos (10 por omisión) por si llega otro encargo que la aproveche.
    - Vencida la gracia, la apaga solo si THE JOKER no tiene otros trabajos, y verifica el apagado (EXITED).
    """
    if not started:return {"gpu":"sin cambios (no la encendió Coordina)"}
    gracia=float((b.config().get("joker") or {}).get("gracia_min",10))*60;fin=time.monotonic()+gracia
    while True:
        otros=joker_pending(jid)
        if otros:return {"gpu":"encendida: la responsabilidad de apagarla pasó al encargo "+otros[0],"traspaso":otros[0]}
        if time.monotonic()>=fin:break
        time.sleep(15)
    # Apagado atómico en el servidor de THE JOKER: deja de admitir trabajos, comprueba que no haya ninguno y apaga.
    # Así se evita la carrera entre «comprobar cero trabajos» y «apagar». Sin ese subcomando, solo se apaga con
    # trabajos_activos == 0 explícito; un valor desconocido (null) nunca autoriza el apagado.
    try:
        for _ in range(10):  # «admisiones en curso» o un trabajo que termina: se reintenta unos minutos antes de rendirse
            g=joker_cli(["apagar-si-libre"],timeout=180)
            if g.get("apagada") is not False or joker_pending(jid):break
            time.sleep(30)
        if joker_pending(jid):return {"gpu":"encendida: la responsabilidad de apagarla pasó a otro encargo de Coordina"}
        if g.get("apagada") is True:pass
        elif g.get("apagada") is False:return {"gpu":"encendida: THE JOKER tiene trabajos o no confirmó estar libre; queda a cargo de su guardián","detalle":g}
        else:raise ValueError("sin apagado atómico")
    except (OSError,ValueError):
        try:g=joker_cli(["gpu"],timeout=45)
        except (OSError,ValueError):return {"gpu":"encendida: no se pudo confirmar que estuviera libre; queda a cargo de su guardián"}
        if g.get("trabajos_activos")!=0 or g.get("ocupado_por"):
            return {"gpu":"encendida: THE JOKER tiene otros trabajos o no confirmó estar libre; queda a cargo de su guardián","detalle":g}
        qwen_stop()
    for _ in range(9):
        time.sleep(20)
        try:
            if not qwen_status()["running"]:
                try:joker_marker().unlink()
                except OSError:pass
                return {"gpu":"apagada y verificada (EXITED) tras "+str(round(gracia/60,1))+" min de gracia"}
        except (OSError,ValueError):pass
    return {"gpu":"se pidió apagar, pero no se pudo verificar; revisar con qwenctl status --node all"}


def joker_batch(target,tasks,workspace,mode="consult",timeout=600,route="auto",session_id=None,session_hint=None,launch=True,source_family=None,source_session_id=None):
    """Modo lote: varios encargos a THE JOKER en una sola sesión de GPU.

    Los encargos van, en orden, a la misma conversación (la elegida o una nueva que abre el primero) y comparten
    la GPU: se enciende como máximo una vez y se apaga, con gracia, después del último.
    """
    if b.backend(target)!="joker":raise ValueError("El modo lote es para THE JOKER (target=joker)")
    if not isinstance(tasks,list) or not 1<=len(tasks)<=20:raise ValueError("tasks: entre 1 y 20 encargos")
    first=coordinate(target,tasks[0],workspace,mode=mode,rounds=1,timeout=timeout,route=route,session_id=session_id,session_hint=session_hint,launch=False,source_family=source_family,source_session_id=source_session_id)
    if "id" not in first:return first  # elegir destino o descubrimiento fallido: no se crea nada
    ids=[first["id"]];base=exchange(first["id"])["endpoint"]
    for t in tasks[1:]:
        spec=dict(first["spec"],task=b.bounded_text(t));spec.pop("source",None);spec["source"]=first["spec"].get("source",{})
        job=b.enqueue_job(spec,None,launch=False)
        with b.database() as c:c.execute("INSERT OR IGNORE INTO native_exchanges(job_id,endpoint,reply_key) VALUES(?,?,?)",(job["id"],b.dumps(base),secrets.token_hex(16)))
        ids.append(job["id"])
    if launch:
        with open(b.home()/"worker.log","ab",buffering=0) as log:
            subprocess.Popen([sys.executable,b.SCRIPT,"lote-worker"]+ids,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
    return {"lote":ids,"jobs":[b.job_status(i) for i in ids]}


def batch_worker(ids):
    """Ejecuta un lote en orden. Si el primero abrió una conversación nueva, los siguientes van a esa misma."""
    session=None
    for jid in ids:
        if session:
            ex=exchange(jid);ep=ex["endpoint"]
            if ep.get("new_conversation"):
                ep=dict(ep,session_id=session["session_id"],title=session.get("title") or ep.get("title"),new_conversation=False,status="open")
                with b.database() as c:c.execute("UPDATE native_exchanges SET endpoint=? WHERE job_id=?",(b.dumps(ep),jid))
        if b.job_status(jid)["cancel_requested"]:b.update_job(jid,state="cancelled",error="Lote cancelado");continue
        worker(jid)
        ep=exchange(jid)["endpoint"]
        if ep.get("session_id") and not session:session={"session_id":ep["session_id"],"title":ep.get("title")}


def joker_sessions():
    d=joker_cli(["conversaciones"],timeout=45)
    rows=d if isinstance(d,list) else d.get("conversaciones") or []
    if isinstance(d,dict) and d.get("error"):raise ValueError("THE JOKER: "+str(d["error"])[:300])
    return [dict(family="joker",session_id=str(r["session_id"]),title=r.get("title") or str(r["session_id"]),workspace="",roots=[],
                 status=r.get("status","unknown"),updated_at=iso_time(r.get("updated_at")) if isinstance(r.get("updated_at"),str) else float(r.get("updated_at") or 0),live=True)
            for r in rows if isinstance(r,dict) and r.get("session_id")]


def start_joker(spec,request_id=None,launch=True):
    """New-session route: a new conversation in THE JOKER, one turn per encargo."""
    if spec["rounds"]!=1:raise ValueError("THE JOKER admite un turno por encargo; continúa el diálogo con otro encargo a la misma conversación")
    native_db()
    endpoint=dict(family="joker",session_id=None,new_conversation=True,title="Coordina (nueva conversación)",workspace=spec["workspace"],roots=[],
                  status="new",task_title=spec["task"].strip().splitlines()[0][:60])
    job=b.enqueue_job(spec,request_id,launch=False)
    with b.database() as c:c.execute("INSERT OR IGNORE INTO native_exchanges(job_id,endpoint,reply_key) VALUES(?,?,?)",(job["id"],b.dumps(endpoint),secrets.token_hex(16)))
    if launch:launch_observer(job["id"])
    return b.job_status(job["id"])


def send_joker(endpoint,text,jid):
    folder=b.home()/"runs"/jid;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    f=folder/"joker-message.txt";f.write_text(text);os.chmod(f,0o600)
    args=["enviar","--texto-archivo",str(f),"--request-id",jid]  # idempotente: un reintento no duplica el trabajo
    if endpoint.get("new_conversation"):args+=["--nueva","--titulo",("Coordina: "+" ".join(endpoint.get("task_title","").split()))[:80].rstrip(": ")]
    else:args+=["--session-id",endpoint["session_id"]]
    try:d=joker_cli(args,timeout=120)
    except (OSError,ValueError) as e:return {"state":"delivery_unknown","submitted":True,"detail":str(e)[:500]}
    state=str(d.get("state",""))
    if state in ("busy","offline"):return {"state":state,"submitted":False}
    if d.get("error") or not d.get("job_id"):
        return {"state":"delivery_unknown","submitted":True,"detail":"THE JOKER no confirmó el trabajo: "+str(d.get("error"))[:500]}
    receipt={"state":"accepted","submitted":True,"job_id":str(d["job_id"]),"joker_state":state}
    if endpoint.get("new_conversation") and d.get("session_id"):receipt.update(session_id=str(d["session_id"]),title=d.get("title"))
    return receipt


def joker_response(endpoint,receipt,jid):
    try:d=joker_cli(["estado","--job-id",receipt["job_id"]],timeout=45)
    except (OSError,ValueError):return None  # un cliente que no responde no es una respuesta perdida
    state=str(d.get("state",""))
    if state in JOKER_DONE and (d.get("answer") or "").strip():
        a=d["answer"];return {"state":"completed","text":a,"marker":("COORDINA["+jid+"]") in a}
    if state in JOKER_FAILED:return {"state":"failed","error":"THE JOKER terminó el trabajo como "+state+": "+str(d.get("error") or "")[:500]}
    return None


def discover(target,session_id=None,session_hint=None,wake=False):
    family=b.backend(target)
    if family == "cowork":
        raise ValueError("Cowork se descubre por interfaz y URL, no por receptores de Claude Code.")
    if family in ("claude","antigravity","joker"):
        rows={"claude":claude_sessions,"antigravity":antigravity_sessions,"joker":joker_sessions}[family]()
        if session_id:rows=[r for r in rows if r["session_id"]==session_id]
        return rows
    rows=codex_metadata(session_id=session_id,session_hint=session_hint)
    if not rows:return []
    # Only a delivery to one named thread may load it; plain discovery never touches the app.
    named=wake and (session_id or session_hint) and len(rows)==1
    try:
        with NativeIPC(timeout=OWNER_WAIT) as ipc:
            owners=ipc.owners([row["session_id"] for row in rows])
    except (OSError,TimeoutError,ConnectionError):
        if not named:raise
        owners={}  # The app may be closed; its thread link also opens it.
    result=[]
    for row in rows:
        owner=owners.get(row["session_id"]) or (wake_codex(row["session_id"]) if named else None)
        if owner:row.update(owner);result.append(row)
    return result


def codex_permission_class(session_id):
    """Describe the sender's verified current mode; unknown metadata stays unknown.

    Claude's peer protocol accepts the classes 'bypass' and 'prompting'. A
    Codex session with approval=never AND an explicitly disabled sandbox is
    equivalent to the first class. Restricted never-ask sessions remain in
    the prompting/dontAsk class. This does not change either session's mode.
    """
    if not re.fullmatch(r"[0-9a-f-]{36}",str(session_id or "")):
        return None
    path=codex_home()/"state_5.sqlite"
    if not path.exists():
        return None
    try:
        with read_db(path) as c:
            row=c.execute("SELECT approval_mode,sandbox_policy,archived FROM threads WHERE id=?",(session_id,)).fetchone()
        if row is None or row[2]:
            return None
        policy=json.loads(row[1])
        if not isinstance(policy,dict):
            return None
        sandbox=policy.get("type")
        if row[0]=="never" and sandbox in ("disabled","danger-full-access"):
            return "bypass"
        if row[0] in ("always","untrusted","on-request","on-failure"):
            return "prompting"
        if row[0]=="never" and sandbox in ("read-only","workspace-write"):
            return "prompting"
    except (OSError,ValueError,TypeError,sqlite3.Error):
        return None
    return None


def source_context():
    tid=os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    if tid:
        result={"family":"codex","session_id":tid}
        permission_class=codex_permission_class(tid)
        if permission_class is not None:result["permission_class"]=permission_class
        return result
    aid=os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
    if aid:return {"family":"antigravity","session_id":aid}
    sock=os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET","")
    sid=os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    sessions=claude_sessions()
    for row in sessions:
        if row["session_id"]==sid or (sock and row["socket"]==sock.removeprefix("uds:")):
            return {"family":"claude","session_id":row["session_id"]}
    ancestors=set();pid=os.getpid()
    for _ in range(12):
        ancestors.add(pid)
        r=subprocess.run(["/bin/ps","-p",str(pid),"-o","ppid="],capture_output=True,text=True,timeout=2)
        if not r.stdout.strip().isdigit():break
        pid=int(r.stdout.strip())
        if pid<=1:break
    for row in sessions:
        if row["pid"] in ancestors:return {"family":"claude","session_id":row["session_id"]}
    return {"family":"unknown","session_id":None}


def words(text):
    s=unicodedata.normalize("NFKD",str(text));s="".join(x for x in s if not unicodedata.combining(x))
    stop={"para","como","este","esta","coordina","claude","codex","gpt","antigravity","gemini","sobre","revisa","trabajo","quiero","conversacion","sesion"}
    return {x for x in re.findall(r"[a-z0-9]+",s.lower()) if len(x)>3 and x not in stop}


def rank(rows,workspace,task="",session_hint=None):
    root=Path(workspace).resolve();hint=(session_hint or "").casefold();tokens=words(task)
    result=[]
    for raw in rows:
        row=dict(raw);score=0;reasons=[]
        if hint and (hint==row["session_id"].casefold() or hint==row["title"].casefold()):score+=1000;reasons.append("nombre o identificador exacto")
        elif hint and hint in row["title"].casefold():score+=200;reasons.append("coincidencia de nombre")
        if hint and not score:continue
        for p in row.get("roots",[row.get("workspace","")]):
            if not p:continue
            candidate=Path(p).expanduser().resolve()
            if candidate==root:score+=45;reasons.append("misma carpeta de proyecto");break
            if candidate!=Path.home() and (candidate in root.parents or root in candidate.parents):
                score+=25;reasons.append("carpeta relacionada");break
        overlap=tokens & words(row.get("title",""))
        if overlap:score+=min(40,len(overlap)*10);reasons.append("tema: "+", ".join(sorted(overlap)))
        row.update(score=score,reasons=reasons,suitable=score>0)
        result.append(row)
    return sorted(result,key=lambda r:(-r["score"],-r.get("updated_at",0),r["title"]))


def public_candidate(row):
    return {k:row[k] for k in ("family","session_id","title","workspace","status","score","reasons","suitable") if k in row}


def propose(target,workspace,task="",session_hint=None,source_family=None,source_session_id=None):
    import routing
    root=b.workspace_path(workspace);family=b.backend(target);source=routing.source(source_family,source_session_id)
    if family == "freellm":
        import freellm_backend
        return dict(state="ready_api",target=family,**freellm_backend.status(),route="new",rounds=1,candidates=[],new_session_allowed=True)
    if family in ("deepseek-official", "deepseek-official-fast"):
        import deepseek_official
        return dict(state="ready_api",target=family,model=deepseek_official.MODEL,budget=deepseek_official.status(),route="new",rounds=1,candidates=[],new_session_allowed=True)
    if family in ("deepseek", "glm", "nemotron"):
        import opencode_backend
        return {"state":"ready_cli", "target":family, "model":opencode_backend.MODELS[family],
                "binary":opencode_backend.BINARY, "new_session_allowed":True, "candidates":[],
                "return_channel":"job_result", "route":"new", "rounds":1}
    if family == "cowork": return routing.ui_hint(source,session_hint)
    try:
        rows=discover(family,session_hint=session_hint)
    except (OSError,ValueError,TimeoutError,ConnectionError,sqlite3.Error) as e:
        return {"state":"discovery_unavailable","target":family,"error":str(e),"new_session_allowed":False,"candidates":[]}
    rows=[r for r in rows if not (r["family"]==source["family"] and r["session_id"]==source["session_id"])]
    ranked=rank(rows,root,task,session_hint)
    suitable=[r for r in ranked if r["suitable"]]
    return {"state":"choose_session" if suitable else "no_suitable_session","target":family,"source":source,
            "recommended_session_id":suitable[0]["session_id"] if suitable else None,
            "candidates":[public_candidate(r) for r in ranked],"new_session_allowed":False,
            "return_channel":"job_result",
            "discovery_scope":"claude_code_local_only" if family=="claude" else "native_local_receivers",
            "next_action":"Si el hilo de Claude está en Cowork, usa target=cowork y verifica su URL en la interfaz." if family=="claude" else None,
            "registration_required":False}


def native_db():
    with b.database() as c:
        c.executescript("""CREATE TABLE IF NOT EXISTS native_exchanges (
          job_id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, reply_key TEXT NOT NULL,
          receipt TEXT, reply_text TEXT);
          CREATE TABLE IF NOT EXISTS session_bindings (
          source TEXT NOT NULL, family TEXT NOT NULL, workspace TEXT NOT NULL,
          destination TEXT NOT NULL, PRIMARY KEY(source,family,workspace));""")


def source_key(source):
    return source["family"]+":"+source["session_id"] if source.get("session_id") else None


def coordinate(target,task,workspace,mode="consult",rounds=1,request_id=None,timeout=300,route="auto",session_id=None,session_hint=None,launch=True,source_family=None,source_session_id=None,workload="light",output_path=None,delivery_timeout=None,reasoning_effort=None,model=None):
    import routing
    if os.environ.get("COORDINA_WORKER")=="1":raise ValueError("No se permite delegación recursiva")
    family=b.backend(target);task=b.bounded_text(task);root=b.workspace_path(workspace)
    b.workload_profile({"workload":workload})
    if route not in ("auto","existing","new"):raise ValueError("route: auto, existing o new")
    if mode not in ("consult","edit") or type(rounds) is not int or not 1<=rounds<=4:
        raise ValueError("Modo o rondas inválidos")
    b.validate_codex_controls(family, route, rounds, timeout, reasoning_effort, model)
    if family == "grok" and route == "auto":
        route = "new"
    if family == "grok" and route != "new":
        raise ValueError("grok es un ejecutor independiente: usa route=new")
    if family == "meta-ai":
        raise ValueError("meta-ai está deshabilitado como destino: no se le asignan encargos.")
    if family == "muse" and route == "auto":
        route = "new"
    if family == "muse" and route != "new":
        raise ValueError("muse es un ejecutor de API independiente: usa route=new")
    if family == "muse" and mode != "consult":
        raise ValueError("muse solo consult: la API no edita archivos en este adaptador")
    if output_path is not None and family not in ("deepseek", "glm", "nemotron"):
        raise ValueError("output_path solo admite deepseek/glm/nemotron")
    if delivery_timeout is not None and (type(delivery_timeout) is not int or not 15<=delivery_timeout<=900):
        raise ValueError("delivery_timeout debe estar entre 15 y 900 segundos")
    source=routing.source(source_family,source_session_id);key=source_key(source)
    if family in ("deepseek", "glm", "nemotron", "deepseek-official", "deepseek-official-fast", "freellm"):
        if route=="existing" or session_id or session_hint:
            raise ValueError("deepseek es un ejecutor independiente: usa route=auto/new sin ID de chat desktop")
        options = {"model":model} if model is not None else {}
        return b.start_job(family,task,root,mode,rounds,request_id,timeout,launch,source=source,workload=workload,output_path=output_path,delivery_timeout=delivery_timeout,**options)
    if family=="cowork":
        if route=="new":raise ValueError("Cowork requiere un chat existente; no se abre otro ejecutor.")
        return routing.prepare_ui(task,root,mode,rounds,request_id,timeout,source,session_id,session_hint,delivery_timeout=delivery_timeout)
    if route=="new":
        if session_id or session_hint:raise ValueError("No combines una conversación existente con route=new")
        options = {"reasoning_effort": reasoning_effort} if reasoning_effort is not None else {}
        if model is not None: options["model"] = model
        return b.start_job(family,task,root,mode,rounds,request_id,timeout,launch,source=source,workload=workload,delivery_timeout=delivery_timeout,**options)
    native_db()
    # Idempotency is checked before discovery: an earlier delivery is never replayed.
    if request_id:
        with b.database() as c:old=c.execute("SELECT id,spec FROM jobs WHERE request_id=?",(request_id,)).fetchone()
        if old:
            s=json.loads(old["spec"])
            if s.get("delivery_timeout",timeout)!=(delivery_timeout if delivery_timeout is not None else timeout):raise ValueError("request_id ya usado con plazo de entrega distinto")
            if b.workload_profile(s)!=workload:raise ValueError("request_id ya usado con perfil de carga distinto")
            if any(s.get(k)!=v for k,v in {"target":family,"task":task,"workspace":root,"mode":mode,"rounds":rounds,"timeout":timeout}.items()) or (session_id and s.get("session_id")!=session_id) or source_key(s.get("source",{}))!=key or s.get("route") not in (("existing","new") if route=="auto" else ("existing",)):
                raise ValueError("request_id ya usado con parámetros distintos")
            return b.job_status(old["id"])
    try:rows=discover(family,session_id=session_id,session_hint=session_hint,wake=True)
    except (OSError,ValueError,TimeoutError,ConnectionError,sqlite3.Error) as e:
        return {"state":"discovery_unavailable","error":str(e),"new_session_allowed":False,"candidates":[]}
    rows=[r for r in rows if not (r["family"]==source["family"] and r["session_id"]==source["session_id"])]
    ranked=rank(rows,root,task,session_hint);suitable=[r for r in ranked if r["suitable"]]
    chosen=None
    if session_id:
        matches=[r for r in rows if r["session_id"]==session_id]
        if len(matches)!=1:return {"state":"session_unavailable","error":"La conversación elegida ya no tiene receptor activo; no se abrió otra.","candidates":[public_candidate(r) for r in ranked]}
        if session_hint and not suitable:raise ValueError("El título indicado no corresponde al identificador elegido.")
        chosen=matches[0]
    elif session_hint:
        exact=[r for r in suitable if session_hint.casefold() in (r['title'].casefold(),r['session_id'].casefold())]
        if len(exact)==1:chosen=exact[0]
        elif len(suitable)==1:chosen=suitable[0]
    elif key:
        with b.database() as c:
            binding=c.execute("SELECT destination FROM session_bindings WHERE source=? AND family=? AND workspace=?",(key,family,root)).fetchone()
        if binding:
            chosen=next((r for r in rows if r["session_id"]==binding[0]),None)
            if chosen is None and family=="codex":
                try:chosen=next(iter(discover(family,session_id=binding[0],wake=True)),None)
                except (OSError,ValueError,TimeoutError,ConnectionError,sqlite3.Error):chosen=None
            if chosen is None:return {"state":"session_unavailable","session_id":binding[0],"new_session_allowed":False,"error":"El destino fijado no está disponible; se conserva su identidad. No se seleccionó otro por tema o carpeta."}
    if chosen is None:
        if suitable:
            return {"state":"choose_session","recommended_session_id":suitable[0]["session_id"],"candidates":[public_candidate(r) for r in ranked],"new_session_allowed":False,"registration_required":False}
        if route=="auto" and not session_hint:
            result=b.start_job(family,task,root,mode,rounds,request_id,timeout,launch,source=source,workload=workload,delivery_timeout=delivery_timeout)
            result["routing_reason"]="Sin destino existente adecuado ni hilo exigido; se creó un ejecutor independiente. La respuesta se recupera con el ID del encargo."
            return result
        return {"state":"no_suitable_session","source":source,"candidates":[public_candidate(r) for r in ranked],"new_session_allowed":False,
                "discovery_scope":"claude_code_local_only" if family=="claude" else "native_local_receivers",
                "next_action":"Prueba el conector o la interfaz de Cowork si ese es el chat buscado." if family=="claude" else "Conserva el hilo elegido y prueba otro transporte disponible; no lo sustituyas por el más reciente."}
    spec=dict(target=family,task=task,workspace=root,mode=mode,rounds=rounds,timeout=timeout,
              route="existing",session_id=chosen["session_id"],source=source)
    if delivery_timeout is not None:spec["delivery_timeout"]=delivery_timeout
    if workload!="light":spec["workload"]=workload
    job=b.enqueue_job(spec,request_id,launch=False)
    with b.database() as c:
        c.execute("INSERT OR IGNORE INTO native_exchanges(job_id,endpoint,reply_key) VALUES(?,?,?)",(job["id"],b.dumps(chosen),secrets.token_hex(16)))
        if key:c.execute("INSERT OR REPLACE INTO session_bindings VALUES(?,?,?,?)",(key,family,root,chosen["session_id"]))
    if launch:launch_observer(job["id"])
    return b.job_status(job["id"])


def launch_observer(jid):
    with b.database() as c:
        claimed=c.execute("UPDATE jobs SET state='starting_native',updated=? WHERE id=? AND state='queued' AND pid IS NULL",(time.time(),jid)).rowcount
    if claimed:
        try:
            with open(b.home()/"worker.log","ab",buffering=0) as log:
                p=subprocess.Popen([sys.executable,b.SCRIPT,"live-worker",jid],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
            with b.database() as c:c.execute("UPDATE jobs SET pid=? WHERE id=? AND pid IS NULL AND state='starting_native'",(p.pid,jid))
        except Exception as e:b.update_job(jid,state="failed",error="No se pudo iniciar el observador local: "+str(e))


def exchange(jid):
    with b.database() as c:r=c.execute("SELECT * FROM native_exchanges WHERE job_id=?",(jid,)).fetchone()
    if not r:raise ValueError("No es un encargo a una conversación existente")
    d=dict(r);d["endpoint"]=json.loads(d["endpoint"]);return d


def reply(job_id,reply_key,text,response_kind="final",execution_ack=None):
    text=b.bounded_text(text,"text")
    if response_kind not in ("progress","final"):raise ValueError("response_kind: progress o final")
    ack=lifecycle.execution_ack(execution_ack)
    with b.database() as c:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT * FROM native_exchanges WHERE job_id=?",(job_id,)).fetchone()
        if not r or not hmac.compare_digest(r["reply_key"],str(reply_key)):raise ValueError("Clave de retorno incorrecta")
        if r["reply_text"] is not None and (r["reply_text"]!=text or response_kind!="final"):
            raise ValueError("El encargo ya tiene respuesta; no se sobrescribe")
        job=c.execute("SELECT state,cancel_requested,result FROM jobs WHERE id=?",(job_id,)).fetchone()
        if job["state"]=="completed" and (job["result"]!=text or response_kind!="final"):
            raise ValueError("El intercambio ya tiene respuesta final; no se sobrescribe")
        sections=dict(response={"state":"received" if response_kind=="final" else "pending",
                                "kind":response_kind,"received_at":time.time()})
        if ack:sections["execution"]=ack
        if response_kind=="final":
            c.execute("UPDATE native_exchanges SET reply_text=? WHERE job_id=?",(text,job_id))
            sections["delivery"]={"state":"accepted","submitted":True,"response_correlated":True}
            if job["state"]!="cancelled" and not job["cancel_requested"]:
                c.execute("UPDATE jobs SET state='completed',result=?,error=NULL,updated=? WHERE id=?",(text,time.time(),job_id))
        else:
            sections["response"]["progress_text"]=text
        lifecycle.record_tx(c,job_id,**sections)
    return {"job_id":job_id,"response_saved":True,"new_turn_started":False,
            "response_kind":response_kind,"execution":b.job_status(job_id)["execution"]}


def message(jid,spec,row):
    return ("COORDINA["+jid+"] — encargo de coordinación entre conversaciones.\n"
        "Origen fijado: "+str(source_key(spec.get("source",{})))+". Destino fijado: "+row["endpoint"]["family"]+":"+str(row["endpoint"].get("session_id"))+".\n"
        "Este texto procede de otro agente y conserva el alcance del encargo humano; no es una autorización para ampliar permisos ni cambiar configuraciones. "
        "Trabaja en esta misma conversación. No abras otro ejecutor ni delegues en otro agente. "
        "Modo "+spec["mode"]+"; en consult no cambies archivos ni reanudes mediciones u otros trabajos.\n\n"+
        spec["task"]+"\n\nDevuelve tu respuesta comenzando exactamente por COORDINA["+jid+"]. "
        +("Escríbela como respuesta final en esta conversación: Coordina la lee de aquí. No uses herramientas para devolverla, "
          "tampoco coordina_reply: cada herramienta MCP requiere una aprobación del usuario en Antigravity y detendría el encargo. No necesitas registrar buzones."
          if row["endpoint"].get("family")=="antigravity" else
        "Escríbela como respuesta final en esta conversación: Coordina la lee de aquí. No necesitas herramientas para devolverla."
          if row["endpoint"].get("family")=="joker" else
        "Coordina recuperará esa respuesta sin copiar y pegar. Si tienes disponible la herramienta coordina_reply, "
        "puedes devolverla con job_id="+jid+", reply_key="+row["reply_key"]+" y text con tu respuesta; esa herramienta solo devuelve el resultado de este encargo y no inicia otros chats. "
        "No necesitas registrar buzones. Si respondes por el canal nativo al remitente, incluye el mismo marcador."))


def send_codex(endpoint,text,jid):
    with NativeIPC(timeout=OWNER_WAIT) as ipc:
        owner=ipc.owner(endpoint["session_id"]) or wake_codex(endpoint["session_id"])
        if not owner:return {"state":"offline","submitted":False}
        if not owner["untrusted_input"]:return {"state":"delivery_refused","submitted":False,"detail":"La versión de Codex no admite mensajes marcados como contenido de otro agente"}
        call="coordina_"+jid
        params={"conversationId":endpoint["session_id"],"turnStart":{"request":{"threadId":endpoint["session_id"],"input":[]},
                "context":{"inheritThreadSettings":True,"responseItems":[
                    {"type":"function_call","call_id":call,"name":"untrusted_input","arguments":"{}"},
                    {"type":"function_call_output","call_id":call,"output":[{"type":"input_text","text":text}]}]}}}
        try:
            # The installed bundle uses v2 for local start-turn; v3 is for remote hosts.
            p=ipc.request("thread-follower-start-turn",params,2,owner["client_id"])
        except (OSError,ValueError,TimeoutError,ConnectionError):
            return {"state":"delivery_unknown","submitted":True,"detail":"Se envió la solicitud pero no llegó un recibo; no se repetirá automáticamente"}
        if p.get("resultType")=="success":
            turn=p.get("result",{}).get("result",{}).get("turn",{}).get("id")
            if turn:return {"state":"accepted","submitted":True,"turn_id":turn}
            return {"state":"delivery_unknown","submitted":True,"detail":"Recibo de Codex desconocido"}
        if p.get("error")=="App context must wait until the current turn finishes":return {"state":"busy","submitted":False}
        return {"state":"delivery_unknown","submitted":True,"detail":str(p.get("error","Codex no confirmó la entrega"))}


class ClaudeCallback:
    def __init__(self,endpoint):self.endpoint=endpoint;self.server=None;self.path=None
    def __enter__(self):
        directory=Path(self.endpoint["socket"]).parent;private_path(directory,"directory")
        self.path=directory/(str(os.getpid())+"-"+secrets.token_hex(4)+".sock")
        self.server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.server.bind(str(self.path));os.chmod(self.path,0o600);self.server.listen(4);self.server.setblocking(False)
        return self
    def __exit__(self,*_):
        if self.server:self.server.close()
        if self.path and self.path.exists():self.path.unlink()
    def receive(self,timeout=.3):
        if not select.select([self.server],[],[],timeout)[0]:return []
        conn,_=self.server.accept();conn.settimeout(.5);data=b""
        try:
            while len(data)<=128*1024:
                part=conn.recv(4096)
                if not part:break
                data+=part
                if data.endswith(b"\n"):break
            if len(data)>128*1024:return []
            return [json.loads(x) for x in data.splitlines() if x.strip()]
        except (OSError,ValueError):return []
        finally:conn.close()


def claude_peer_content(text,reply_address,source_mode=None,source_session=None):
    """Use Claude's canonical peer envelope, keeping the message agent-authored.

    User frames derive their mode from this envelope; from_mode on the outer
    JSON frame is used for control notices, not ordinary messages.
    """
    if source_mode not in ("bypass","prompting"):
        return text
    address=urllib.parse.quote(str(reply_address),safe=":/._-\\")
    attrs=[f'from="{address}"']
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}",str(source_session or "")):
        attrs.append(f'from-session="{source_session}"')
    attrs.extend(['from-name="Codex"',f'from-mode="{source_mode}"'])
    body=re.sub(r"<(?=/?cross-session-message(?:\s|>|/))",lambda m:"<\\",text,flags=re.I)
    return '<cross-session-message '+" ".join(attrs)+'>\n'+body+'\n</cross-session-message>'


def send_claude(endpoint,text,jid,callback,source_mode=None,source_session=None):
    if not process_is_live(endpoint):return {"state":"offline","submitted":False}
    private_path(endpoint["socket"],"socket")
    # The published capability hashes path.resolve (lexical), not realpath.
    digest=hashlib.sha256(os.path.abspath(endpoint["socket"]).encode()).hexdigest()
    keyfile=claude_home()/"sessions"/(str(endpoint["pid"])+"."+digest+".key")
    # Published peer capability only; never read or impersonate an own-child token.
    key=read_metadata(keyfile,4096,False)
    if key.get("procStart")!=endpoint["procStart"] or not re.fullmatch(r"[0-9a-f]{32}",key.get("peerToken","")):
        raise ValueError("La capacidad de recepción de Claude no corresponde al proceso actual")
    reply_address="uds:"+str(callback.path)
    content=claude_peer_content(text,reply_address,source_mode,source_session)
    packet=dict(type="user",msgV=1,msg_id=jid,session_id=endpoint["session_id"],
                **{"from":reply_address},priority="next",message={"role":"user","content":content})
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(3);s.connect(endpoint["socket"])
        try:s.sendall((b.dumps({"type":"auth","token":key["peerToken"]})+"\n"+b.dumps(packet)+"\n").encode())
        except OSError:return {"state":"delivery_unknown","submitted":True,"detail":"La conexión de Claude se perdió durante el envío; no se repetirá"}
    return {"state":"awaiting_receipt","submitted":True}


def codex_response(endpoint,turn_id):
    path=codex_home()/"thread_history_1.sqlite"
    if not path.exists():return None
    with read_db(path) as c:
        turn=c.execute("SELECT status,error_json,final_agent_item_id FROM thread_turns WHERE thread_id=? AND turn_id=?",(endpoint["session_id"],turn_id)).fetchone()
        if not turn or turn["status"] not in ("completed","failed","interrupted"):return None
        if turn["status"]!="completed":return {"state":"failed","error":"La conversación de Codex terminó con estado "+turn["status"]}
        r=c.execute("SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type='agentMessage' ORDER BY rollout_ordinal DESC LIMIT 1",(endpoint["session_id"],turn_id)).fetchone()
    if r:
        d=json.loads(r[0]);text=d.get("text","")
        if text:return {"state":"completed","text":text}
    return None


class ClaudeTranscript:
    def __init__(self,endpoint):
        sid=endpoint["session_id"]
        if not re.fullmatch(r"[0-9a-f-]{36}",sid):raise ValueError("ID de sesión no válido")
        candidates=list((claude_home()/"projects").glob("*/"+sid+".jsonl"))
        self.path=candidates[0] if len(candidates)==1 else None
        self.offset=self.path.stat().st_size if self.path else 0;self.partial=b""
    def response(self,jid):
        if not self.path or not self.path.exists():return None
        private_path(self.path,"file",True)
        if self.path.stat().st_size<self.offset:raise ValueError("El registro de Claude cambió de tamaño; no se atribuye una respuesta sin correlación")
        with self.path.open("rb") as f:
            f.seek(self.offset);data=f.read(1024*1024);self.offset+=len(data)
        lines=(self.partial+data).split(b"\n");self.partial=lines.pop()
        if len(self.partial)>2*1024*1024:self.partial=b""
        marker="COORDINA["+jid+"]"
        for line in lines:
            try:
                d=json.loads(line);m=d.get("message",{})
                if d.get("type")!="assistant" or m.get("stop_reason")!="end_turn":continue
                text="\n".join(x.get("text","") for x in m.get("content",[]) if x.get("type")=="text")
                if marker in text:return text
            except (ValueError,AttributeError,TypeError):continue
        return None


def save_receipt(jid, receipt):
    with b.database() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT reply_text FROM native_exchanges WHERE job_id=?",(jid,)).fetchone()
        if row and row[0] is not None:return
        c.execute("UPDATE native_exchanges SET receipt=? WHERE job_id=?",(b.dumps(receipt),jid))
        lifecycle.record_tx(c,jid,delivery={"state":receipt["state"],"submitted":receipt.get("submitted",False)})


def accept_delivery(jid, timeout):
    with b.database() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT reply_text FROM native_exchanges WHERE job_id=?",(jid,)).fetchone()
        if row and row[0] is not None:return time.monotonic()+timeout
        old=lifecycle.read(c,jid).get("delivery",{})
        accepted=old.get("accepted_at") or time.time()
        lifecycle.record_tx(c,jid,delivery={"state":"accepted","submitted":True,"accepted_at":accepted},
                            response={"state":"pending","started_at":accepted,"deadline_at":accepted+timeout})
    return time.monotonic()+max(0,accepted+timeout-time.time())


def response_expired(jid, receipt, waiting):
    """A concurrent correlated reply wins over the observer's timeout."""
    with b.database() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT state FROM jobs WHERE id=?",(jid,)).fetchone()
        reply=c.execute("SELECT reply_text FROM native_exchanges WHERE job_id=?",(jid,)).fetchone()
        if row["state"]=="completed" or reply[0] is not None:return
        if receipt.get("state")=="awaiting_receipt":
            state="delivery_unknown"
            error="El transporte envió el mensaje, pero no confirmó aceptación dentro del plazo de entrega; no reenviar"
            c.execute("UPDATE native_exchanges SET receipt=? WHERE job_id=?",(b.dumps(dict(receipt,state=state)),jid))
            lifecycle.record_tx(c,jid,delivery={"state":state,"submitted":True})
        else:
            state="response_pending"
            error="Venció el plazo de respuesta desde la aceptación; recuperar el mismo encargo sin reenviar."+waiting
            lifecycle.record_tx(c,jid,response={"state":"timed_out"})
        c.execute("UPDATE jobs SET state=?,error=?,updated=? WHERE id=?",(state,error,time.time(),jid))


def observer_failure(jid, state, error):
    with b.database() as c:
        c.execute("UPDATE jobs SET state=?,error=?,updated=? WHERE id=? AND state!='completed'",
                  (state,error,time.time(),jid))


def worker(jid):
    def interrupt(*_):raise InterruptedError("Se interrumpió el observador propio de Coordina")
    signal.signal(signal.SIGTERM,interrupt)
    with b.database() as c:
        c.execute("BEGIN IMMEDIATE")
        previous=c.execute("SELECT state FROM jobs WHERE id=?",(jid,)).fetchone()
        if not previous or previous[0] not in ("queued","starting_native"):return
        c.execute("UPDATE jobs SET pid=?,state='running',updated=? WHERE id=?",(os.getpid(),time.time(),jid))
    folder=b.home()/"runs"/jid;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    (folder/"memory-before.json").write_text(b.dumps(b.memory_snapshot()))
    receipt={};endpoint={}
    try:
        job=b.job_status(jid);spec=job["spec"];row=exchange(jid);endpoint=row["endpoint"]
        text=b.workload_instructions(spec)+message(jid,spec,row)
        delivery_seconds=spec.get("delivery_timeout",spec["timeout"])
        delivery_deadline=time.monotonic()+delivery_seconds
        with b.database() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT reply_text FROM native_exchanges WHERE job_id=?",(jid,)).fetchone()[0] is not None:return
            lifecycle.record_tx(c,jid,delivery={"state":"not_submitted","submitted":False,
                                          "started_at":time.time(),"deadline_at":time.time()+delivery_seconds},
                             response={"state":"not_started"})
        deadline=delivery_deadline
        callback_context=ClaudeCallback(endpoint) if endpoint["family"]=="claude" else contextlib.nullcontext(None)
        transcript=ClaudeTranscript(endpoint) if endpoint["family"]=="claude" else None
        with callback_context as callback:
            while True:
                saved=exchange(jid).get("reply_text")
                if saved is not None:
                    reply(jid,row["reply_key"],saved)
                    return
                if b.job_status(jid)["cancel_requested"]:raise InterruptedError("Se canceló la espera; un mensaje ya entregado no se retira")
                if receipt.get("state") in ("busy","offline") and time.monotonic()>=delivery_deadline:
                    lifecycle.record(jid,delivery={"state":"expired","submitted":False})
                    b.update_job(jid,state="session_busy" if receipt["state"]=="busy" else "session_unavailable",
                                 error="Venció el plazo de entrega sin envío; usa job_resume con la revisión vigente para reintentar el mismo encargo")
                    return
                # Gate new deliveries only. Never interrupt the user's existing app
                # or a turn already delivered because pressure subsequently changes.
                b.check_memory(spec)
                # A crash inside a transport is uncertain, never safe to replay.
                save_receipt(jid,{"state":"delivery_unknown","submitted":True,"detail":"Solicitud de entrega en curso"})
                if endpoint["family"]=="claude":
                    source=spec.get("source",{})
                    source_mode=codex_permission_class(source.get("session_id")) if source.get("family")=="codex" else None
                    receipt=send_claude(endpoint,text,jid,callback,source_mode=source_mode,source_session=source.get("session_id"))
                elif endpoint["family"]=="antigravity":receipt=send_antigravity(endpoint,text,jid)
                elif endpoint["family"]=="joker":
                    # El encendido tiene su propio presupuesto (joker.gpu_espera_min, 25 por omisión): una H100 puede tardar
                    # más de 10 minutos en estar lista y eso no debe consumir el plazo de la respuesta.
                    espera=float((b.config().get("joker") or {}).get("gpu_espera_min",25))*60
                    gpu=joker_gpu(jid,time.monotonic()+espera)
                    receipt=send_joker(endpoint,text,jid) if gpu["state"]=="ready" else {"state":gpu["state"],"submitted":False}
                    receipt["gpu"]={k:gpu.get(k) for k in ("state","started_by_coordina","pod_id","detail")}
                else:receipt=send_codex(endpoint,text,jid)
                saved=exchange(jid).get("reply_text")
                if saved is not None:
                    reply(jid,row["reply_key"],saved)
                    return
                if receipt.get("session_id"):
                    # A new Antigravity conversation now exists; record it before waiting.
                    endpoint=dict(endpoint,session_id=receipt["session_id"],new_conversation=False,status="open",title=receipt.get("title") or endpoint.get("title"))
                    with b.database() as c:c.execute("UPDATE native_exchanges SET endpoint=? WHERE job_id=?",(b.dumps(endpoint),jid))
                save_receipt(jid,receipt)
                if receipt["state"] not in ("busy","offline"):break
                b.update_job(jid,state="waiting_destination");time.sleep(2)
            if receipt["state"] in ("delivery_unknown","delivery_refused","held"):
                b.update_job(jid,state=receipt["state"],error=receipt.get("detail"));return
            if receipt["state"]=="accepted":deadline=accept_delivery(jid,spec["timeout"])
            b.update_job(jid,state="awaiting_response",transcript=b.dumps([{"event":"native_delivery","destination":public_candidate(endpoint),"receipt":receipt}]))
            while time.monotonic()<deadline:
                if b.job_status(jid)["cancel_requested"]:raise InterruptedError("Se canceló la espera; un mensaje ya entregado no se retira")
                saved=exchange(jid).get("reply_text")
                if saved:answer=saved;break
                if callback:
                    for packet in callback.receive():
                        if packet.get("type")=="control" and packet.get("action")=="peer_message_status" and packet.get("orig_msg_id")==jid:
                            status=packet.get("status")
                            receipt=dict(state=status,submitted=True)
                            save_receipt(jid,receipt)
                            if status=="accepted":deadline=accept_delivery(jid,spec["timeout"])
                            if status in ("held","refused","denied","dropped","expired"):
                                b.update_job(jid,state="held" if status=="held" else "delivery_refused",error="Claude "+str(status)+" el mensaje según sus controles de recepción; no se reenviará ni se cambiarán permisos")
                                return
                        elif packet.get("type")=="user" and packet.get("from")=="uds:"+endpoint["socket"]:
                            content=packet.get("message",{}).get("content","")
                            if isinstance(content,str) and "COORDINA["+jid+"]" in content:
                                reply(jid,row["reply_key"],content)
                    answer=transcript.response(jid)
                    if answer:break
                else:
                    agy=endpoint["family"]=="antigravity";jk=endpoint["family"]=="joker"
                    result=antigravity_response(endpoint,receipt,jid) if agy else joker_response(endpoint,receipt,jid) if jk else codex_response(endpoint,receipt["turn_id"])
                    if result and result["state"]=="waiting_permission":
                        if receipt.get("waiting")!="user_permission":
                            # Antigravity's own approval controls apply; Coordina never approves.
                            receipt=dict(receipt,waiting="user_permission")
                            with b.database() as c:c.execute("UPDATE native_exchanges SET receipt=? WHERE job_id=?",(b.dumps(receipt),jid))
                        result=None
                    if result:
                        if result["state"]!="completed":b.update_job(jid,state="failed",error=result["error"]);return
                        answer=result["text"]
                        if agy or jk:receipt=dict(receipt,marker_present=result["marker"])
                        break
                    time.sleep(5 if jk else 1 if agy else .4)
            else:
                waiting=" Antigravity espera una aprobación en su propia interfaz; Coordina no la concede." if receipt.get("waiting")=="user_permission" else ""
                response_expired(jid,receipt,waiting)
                return
            (folder/"answer.txt").write_text(answer)
            receipt=dict(receipt,response_correlated=True)
            with b.database() as c:c.execute("UPDATE native_exchanges SET receipt=? WHERE job_id=?",(b.dumps(receipt),jid))
            lifecycle.record(jid,response={"state":"received","kind":"final","received_at":time.time()})
            b.update_job(jid,state="completed",result=answer[:b.MAX_TEXT],error=None,transcript=b.dumps([
                {"event":"native_delivery","destination":public_candidate(endpoint),"receipt":receipt},
                dict({"agent":endpoint["family"],"session_id":endpoint["session_id"],"title":endpoint["title"],"answer":answer[:b.MAX_TEXT],"answer_file":str(folder/"answer.txt")},
                     **({"model_provider":"Google Gemini (Antigravity)"} if endpoint["family"]=="antigravity" else
                        {"model_provider":"Qwen en THE JOKER (OpenCode)"} if endpoint["family"]=="joker" else {}))]))
            if endpoint["family"]=="joker":
                # El encargo ya figura completo; la gracia de la GPU no demora la respuesta.
                receipt=dict(receipt,gpu_cierre=joker_release((receipt.get("gpu") or {}).get("started_by_coordina"),jid))
                with b.database() as c:c.execute("UPDATE native_exchanges SET receipt=? WHERE job_id=?",(b.dumps(receipt),jid))
                (folder/"gpu.json").write_text(b.dumps(receipt["gpu_cierre"]))
    except MemoryError as e:observer_failure(jid,"blocked_memory",str(e))
    except InterruptedError as e:observer_failure(jid,"cancelled",str(e))
    except BaseException as e:observer_failure(jid,"failed",type(e).__name__+": "+str(e))
    finally:
        if endpoint.get("family")=="joker" and (receipt.get("gpu") or {}).get("started_by_coordina") and "gpu_cierre" not in receipt:
            # El encargo no terminó bien: la GPU que encendió Coordina no queda ociosa.
            try:
                cierre=joker_release(True,jid)
                with b.database() as c:c.execute("UPDATE native_exchanges SET receipt=? WHERE job_id=?",(b.dumps(dict(receipt,gpu_cierre=cierre)),jid))
            except Exception:pass
        b.update_job(jid,pid=None)
        (folder/"memory-after.json").write_text(b.dumps(b.memory_snapshot()))
