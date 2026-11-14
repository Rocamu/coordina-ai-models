"""Black-box MCP regression: no local origin, remote ID, return and recovery.

The MCP, worker and adapter are real subprocesses; the model executable is a
fixture. This proves routing without a local origin, not live cloud hosting.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class CrossEnvironmentTests(unittest.TestCase):
    def test_mcp_without_local_origin_runs_and_recovers_remote_requests(self):
        with tempfile.TemporaryDirectory(prefix="coordina-cloud-caller-") as folder:
            root=Path(folder);state=root/"state";state.mkdir();work=root/"project";work.mkdir()
            fake=root/"codex-fixture"
            fake.write_text("#!"+sys.executable+"\nimport sys,json,pathlib\nprompt=sys.stdin.read()\npathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('5')\nprint(json.dumps({'type':'turn.completed'}))\n")
            fake.chmod(0o700)
            (state/"config.json").write_text(json.dumps({"binaries":{"codex":str(fake)}}))
            env=os.environ.copy()
            for k in ("CODEX_THREAD_ID","CODEX_SESSION_ID","CLAUDE_SESSION_ID","CLAUDE_CODE_SESSION_ID",
                      "CLAUDE_CODE_MESSAGING_SOCKET","ANTIGRAVITY_CONVERSATION_ID","COORDINA_WORKER"):
                env.pop(k,None)
            env.update(COORDINA_HOME=str(state),CODEX_HOME=str(root/"empty-codex"),
                       CLAUDE_CONFIG_DIR=str(root/"empty-claude"),PYTHONDONTWRITEBYTECODE="1")
            p=subprocess.Popen([sys.executable,"-B",str(ROOT/"coordina.py"),"mcp","--client","cloud-fixture"],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            def call(n,name,args):
                p.stdin.write(json.dumps({"jsonrpc":"2.0","id":n,"method":"tools/call",
                                         "params":{"name":name,"arguments":args}})+"\n");p.stdin.flush()
                response=json.loads(p.stdout.readline())["result"]
                self.assertFalse(response.get("isError"),response)
                return json.loads(response["content"][0]["text"])
            jobs=[]
            try:
                for i,source in enumerate(({},{"source_family":"claude-cloud","source_session_id":"cloud-not-on-this-mac"})):
                    args=dict(target="codex",task="Suma 2 y 3.",workspace=str(work),route="auto",
                              request_id="cloud-case-"+str(i),**source)
                    job=call(i*3+1,"coordinate",args)
                    self.assertIn("id",job,job);self.assertEqual(job["spec"]["route"],"new")
                    if source:
                        self.assertEqual(job["spec"]["source"]["identity_evidence"],"caller_declared")
                    else:
                        self.assertIsNone(job["spec"]["source"]["session_id"])
                    self.assertNotIn("permission_class",job["spec"]["source"])
                    done=call(i*3+2,"job_wait",{"job_id":job["id"],"seconds":10})
                    self.assertEqual(done["state"],"completed",done)
                    self.assertEqual(done["result"],"5")
                    again=call(i*3+3,"coordinate",args)
                    self.assertEqual(again["id"],job["id"])
                    self.assertEqual(again["state"],"completed")
                    jobs.append(job["id"])
            finally:
                p.stdin.close();p.wait(timeout=5);p.stdout.close();p.stderr.close()
            for jid in jobs:
                r=subprocess.run([sys.executable,"-B",str(ROOT/"coordina.py"),"show",jid],
                                 env=env,capture_output=True,text=True,check=True,timeout=5)
                self.assertEqual(json.loads(r.stdout)["result"],"5")


if __name__=="__main__":
    unittest.main()

