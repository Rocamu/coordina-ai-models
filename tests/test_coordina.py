import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coordina", ROOT/"coordina.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coordina-test-")
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"COORDINA_HOME":str(self.root/"state")})
        self.env.start()
        self.work = self.root/"project"
        self.work.mkdir()
        self.mem = patch.object(b,"memory_snapshot",return_value={"pressure":1})
        self.mem.start()

    def tearDown(self):
        self.mem.stop()
        self.env.stop()
        self.tmp.cleanup()

    def job(self, **kw):
        return b.start_job("claude","revisar",str(self.work),launch=False,**kw)

    def fake(self, agent, text="respuesta"):
        fake = self.root/agent
        fake.write_text("#!"+sys.executable+"\nimport sys,json,pathlib\n"
            "prompt=sys.stdin.read()\n"
            "answer="+repr(text)+"\n"
            "if '-o' in sys.argv:\n"
            " pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(answer)\n"
            " print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1}}))\n"
            "else: print(json.dumps({'type':'result','result':answer,'is_error':False}))\n")
        fake.chmod(0o700)
        return str(fake)

    def test_idempotency_and_spec_conflict(self):
        a=self.job(request_id="same")
        self.assertEqual(a["id"],self.job(request_id="same")["id"])
        with self.assertRaises(ValueError): self.job(request_id="same",rounds=2)

    def test_concurrent_submit_deduplicates(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids=list(pool.map(lambda _:self.job(request_id="concurrent")["id"],range(8)))
        self.assertEqual(len(set(ids)),1)

    def test_alternating_real_subprocess_adapters(self):
        (b.home()/"config.json").write_text(json.dumps({"binaries":{n:self.fake(n,n+" OK") for n in ("claude","codex")}}))
        d=self.job(rounds=2)
        b.worker(d["id"])
        got=b.job_status(d["id"])
        self.assertEqual(got["state"],"completed",got.get("error"))
        self.assertEqual([x["agent"] for x in got["transcript"]],["claude","codex"])
        self.assertEqual(got["result"],"codex OK")
        self.assertIsNone(got["pid"])

    def test_queued_cancellation_never_runs(self):
        d=self.job()
        b.update_job(d["id"],cancel_requested=1)
        b.worker(d["id"])
        self.assertEqual(b.job_status(d["id"])["state"],"cancelled")

    def test_memory_warning_does_not_launch_heavy_work(self):
        d=self.job(workload="heavy")
        with patch.object(b,"memory_snapshot",return_value={"pressure":2}),patch.object(b,"run_turn") as run:
            b.worker(d["id"])
            run.assert_not_called()
        self.assertEqual(b.job_status(d["id"])["state"],"blocked_memory")

    def test_light_work_completes_under_warning_with_real_child(self):
        (b.home()/"config.json").write_text(json.dumps({"binaries":{"claude":self.fake("claude")}}))
        d=self.job()
        with patch.object(b,"memory_snapshot",return_value={"pressure":2}):
            b.worker(d["id"])
        got=b.job_status(d["id"])
        self.assertEqual(got["state"],"completed",got.get("error"))

    def test_levels_three_four_five_block_both_profiles_before_launch(self):
        for level in (3,4,5):
            for profile in ("light","heavy"):
                with self.subTest(level=level,profile=profile):
                    d=self.job(workload=profile)
                    with patch.object(b,"memory_snapshot",return_value={"pressure":level}),patch.object(b,"run_turn") as run:
                        b.worker(d["id"])
                        run.assert_not_called()
                    self.assertEqual(b.job_status(d["id"])["state"],"blocked_memory")

    def test_warning_budget_counts_descendants_and_excludes_other_work(self):
        output="100 1 102400\n101 100 512000\n102 101 512000\n200 1 9999999\n"
        with patch.object(b.subprocess,"run",return_value=subprocess.CompletedProcess([],0,output)):
            self.assertEqual(b.process_tree_rss_mib(100),1100)
            with self.assertRaises(MemoryError):b.check_memory({}, {"pressure":2},100)
            b.check_memory({}, {"pressure":1},100)

    def test_light_runtime_stops_when_it_becomes_heavy_or_pressure_is_critical(self):
        fake=self.root/"slow"
        fake.write_text("#!"+sys.executable+"\nimport sys,time\nsys.stdin.read()\ntime.sleep(30)\n")
        fake.chmod(0o700)
        (b.home()/"config.json").write_text(json.dumps({"binaries":{"claude":str(fake)}}))
        for readings in ((2,2,2,2),(1,1,1,4)):
            d=self.job()
            with patch.object(b,"memory_snapshot",side_effect=[{"pressure":v} for v in readings]+[{"pressure":1}]),patch.object(b,"process_tree_rss_mib",return_value=1100):
                b.worker(d["id"])
            got=b.job_status(d["id"])
            self.assertEqual(got["state"],"blocked_memory",got.get("error"))
            self.assertIsNone(got["pid"])

    def test_invalid_profile_and_profile_idempotency(self):
        with self.assertRaises(ValueError):self.job(workload="anything")
        self.job(request_id="profile")
        with self.assertRaises(ValueError):self.job(request_id="profile",workload="heavy")

    def test_claim_overlap_atomic_release_and_traversal(self):
        b.claim_paths("one",str(self.work),["src"])
        with self.assertRaises(ValueError):b.claim_paths("two",str(self.work),["safe","src/a.py"])
        with b.database() as c:self.assertEqual(c.execute("SELECT count(*) FROM claims WHERE owner='two'").fetchone()[0],0)
        with self.assertRaises(ValueError):b.claim_paths("two",str(self.work),["../other"])
        b.release("one")
        b.claim_paths("two",str(self.work),["src/a.py"])

    def test_mail_recipient_scope_ack_and_no_silent_recipient_reuse(self):
        b.peer_register("one","Claude","test")
        b.peer_register("two","GPT","test")
        with self.assertRaises(ValueError):b.peer_register("three","Claude","test")
        m=b.send_message("one","GPT","hola")
        self.assertEqual(b.inbox("one")["messages"],[])
        got=b.inbox("two")["messages"][0]
        self.assertIsNone(got["acked"])
        self.assertEqual(b.ack("one",[m["message_id"]])["acked"],[])
        self.assertIsNone(b.inbox("two")["messages"][0]["acked"])
        b.ack("two",[m["message_id"]])
        self.assertIsNotNone(b.inbox("two")["messages"][0]["acked"])

    def test_no_recursive_or_unbounded_jobs(self):
        with self.assertRaises(ValueError): self.job(rounds=5)
        with self.assertRaises(ValueError): self.job(rounds=True)
        with self.assertRaises(ValueError): self.job(timeout=3600)
        with patch.dict(os.environ,{"COORDINA_WORKER":"1"}):
            with self.assertRaises(ValueError):self.job()

    def test_pending_queue_is_bounded(self):
        for _ in range(8):self.job()
        with self.assertRaises(ValueError):self.job()

    def test_orphan_worker_is_reported_without_hanging(self):
        import time
        d=self.job()
        with b.database() as c:
            c.execute("UPDATE jobs SET pid=?,state='running',updated=? WHERE id=?", (99999999,time.time()-20,d["id"]))
        self.assertEqual(b.job_status(d["id"])["state"],"interrupted")

    def test_authentication_error_is_actionable(self):
        raw=json.dumps({"is_error":True,"result":"401 OAuth token expired","usage":{"a":123}})
        msg=b.cli_error("claude",raw,"",1)
        self.assertIn("401 OAuth",msg)
        self.assertNotIn("usage",msg)

    def test_cli_argv_preserves_prompt_and_uses_normal_permissions(self):
        (b.home()/"config.json").write_text(json.dumps({"binaries":{"claude":self.fake("claude"),"codex":self.fake("codex")}}))
        s=self.job()["spec"]
        prompt='literal $(touch bad) `uname` "x"\nsegunda línea'
        for agent in ("claude","codex"):
            cmd,stdin=b.command_for(agent,s,self.root/"answer",prompt)
            self.assertEqual(stdin,prompt)
            self.assertNotIn(prompt,cmd)
            self.assertNotIn("--dangerously-skip-permissions",cmd)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox",cmd)
        cmd,_=b.command_for("claude",s,self.root/"answer",prompt)
        self.assertEqual(cmd[cmd.index("--tools")+1],"Read,Glob,Grep")

    def test_mcp_negotiation_tools_and_invalid_arguments(self):
        requests=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}},
                  {"jsonrpc":"2.0","method":"notifications/initialized"},
                  {"jsonrpc":"2.0","id":2,"method":"tools/list"},
                  {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"coordinate","arguments":{"target":"bad"}}}]
        p=subprocess.run([sys.executable,str(ROOT/"coordina.py"),"mcp","--client","test"],
                         input="\n".join(json.dumps(x) for x in requests)+"\n",capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        result=[json.loads(x) for x in p.stdout.splitlines()]
        self.assertEqual(len(result),3)
        self.assertEqual(result[0]["result"]["protocolVersion"],"2024-11-05")
        self.assertIn("coordinate",[x["name"] for x in result[1]["result"]["tools"]])
        self.assertTrue(result[2]["result"]["isError"])

    def test_two_mcp_clients_exchange_and_ack_a_message(self):
        clients=[]
        def call(p,i,name,args):
            p.stdin.write(json.dumps({"jsonrpc":"2.0","id":i,"method":"tools/call","params":{"name":name,"arguments":args}})+"\n")
            p.stdin.flush()
            result=json.loads(p.stdout.readline())["result"]
            self.assertFalse(result["isError"],result)
            return json.loads(result["content"][0]["text"])
        try:
            for name in ("Claude","GPT"):
                p=subprocess.Popen([sys.executable,str(ROOT/"coordina.py"),"mcp","--client",name],
                                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                clients.append(p)
                call(p,1,"peer_register",{"name":name})
            sent=call(clients[0],2,"peer_send",{"recipient":"GPT","message":"Revisa el plan"})
            received=call(clients[1],2,"peer_inbox",{})
            self.assertEqual(received["messages"][0]["body"],"Revisa el plan")
            confirmed=call(clients[1],3,"peer_ack",{"ids":[sent["message_id"]]})
            self.assertEqual(confirmed["acked"],[sent["message_id"]])
        finally:
            for p in clients:
                p.stdin.close()
                p.wait(timeout=5)
                p.stdout.close();p.stderr.close()


if __name__ == "__main__": unittest.main()
