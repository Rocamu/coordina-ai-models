import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import coordina as b
import live

# Fake client with THE JOKER's contract: conversaciones, enviar, estado, gpu. It keeps its state in a JSON file.
FAKE='''import json,sys
from pathlib import Path
st=Path(__file__).with_suffix(".state.json");d=json.loads(st.read_text()) if st.exists() else {"jobs":{}}
a=sys.argv[1:]
def arg(k):return a[a.index(k)+1] if k in a else None
if a[0]=="conversaciones":print(json.dumps([{"session_id":"c1","title":"Contratos anteriores a 2022","updated_at":"2026-10-22T14:00:00Z","status":"idle"}]))
elif a[0]=="enviar":
    jid="j%d"%(len(d["jobs"])+1);txt=Path(arg("--texto-archivo")).read_text()
    marker=txt[txt.index("COORDINA["):txt.index("]")+1]
    d["jobs"][jid]={"marker":marker,"polls":0};st.write_text(json.dumps(d))
    print(json.dumps({"job_id":jid,"session_id":"c2" if "--nueva" in a else arg("--session-id"),"state":"queued"}))
elif a[0]=="estado":
    j=d["jobs"][arg("--job-id")];j["polls"]+=1;st.write_text(json.dumps(d))
    print(json.dumps({"state":"done","answer":j["marker"]+" hecho"} if j["polls"]>=2 else {"state":"running"}))
elif a[0]=="gpu":print(json.dumps({"principal":"RUNNING","trabajos_activos":0,"ocupado_por":None}))
'''


class JokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="coordina-joker-")
        self.root=Path(self.tmp.name);self.work=self.root/"project";self.work.mkdir()
        state=self.root/"state";state.mkdir()
        self.cli=self.root/"joker_cli.py";self.cli.write_text(FAKE)
        (state/"config.json").write_text(json.dumps({"joker":{"cli":str(self.cli),"python":sys.executable,"gracia_min":0}}))
        self.env=patch.dict(os.environ,{"COORDINA_HOME":str(state)});self.env.start()
        self.source=patch.object(live,"source_context",return_value={"family":"claude","session_id":"origin"});self.source.start()
        self.gpu={"running":True,"ready":True,"remaining":3000};self.calls=[]
        def status():return dict(self.gpu,deadline=0,pod_id="p",rate=3.99)
        def start():self.calls.append("start");self.gpu.update(running=True,ready=True,remaining=3600);return "ok"
        def stop():self.calls.append("stop");self.gpu.update(running=False,ready=False,remaining=0)
        self.patches=[patch.object(live,"qwen_status",side_effect=status),patch.object(live,"qwen_start",side_effect=start),patch.object(live,"qwen_stop",side_effect=stop)]
        for x in self.patches:x.start()

    def tearDown(self):
        for x in self.patches:x.stop()
        self.source.stop();self.env.stop();self.tmp.cleanup()

    def test_aliases_select_joker(self):
        for name in ("joker","The Joker","qwen","opencode"):self.assertEqual(b.backend(name),"joker")

    def test_discovery_lists_joker_conversations(self):
        rows=live.discover("joker")
        self.assertEqual([r["session_id"] for r in rows],["c1"]);self.assertEqual(rows[0]["family"],"joker")

    def test_missing_client_is_discovery_unavailable_not_a_new_session(self):
        self.cli.unlink()
        d=live.propose("joker",str(self.work),"Busca contratos")
        self.assertEqual(d["state"],"discovery_unavailable");self.assertFalse(d["new_session_allowed"])

    def run_job(self,**kw):
        d=live.coordinate("joker","Busca contratos anteriores a 2022",str(self.work),launch=False,timeout=60,**kw)
        with patch.object(live.time,"sleep"):live.worker(d["id"])
        return b.job_status(d["id"])

    def test_existing_conversation_round_trip(self):
        job=self.run_job(session_id="c1")
        self.assertEqual(job["state"],"completed");self.assertIn("COORDINA["+job["id"]+"] hecho",job["result"])
        self.assertEqual(job["transcript"][1]["model_provider"],"Qwen en THE JOKER (OpenCode)")

    def test_new_conversation_records_its_session(self):
        d=b.start_job("joker","Busca contratos",str(self.work),launch=False)
        with patch.object(live.time,"sleep"):live.worker(d["id"])
        self.assertEqual(b.job_status(d["id"])["state"],"completed")
        self.assertEqual(live.exchange(d["id"])["endpoint"]["session_id"],"c2")

    def test_message_asks_for_a_final_answer_without_tools(self):
        job=self.run_job(session_id="c1")
        sent=(b.home()/"runs"/job["id"]/"joker-message.txt").read_text()
        self.assertIn("No necesitas herramientas",sent);self.assertNotIn("reply_key=",sent)


    def test_gpu_on_is_used_without_starting_or_stopping(self):
        job=self.run_job(session_id="c1")
        self.assertEqual(job["state"],"completed");self.assertEqual(self.calls,[])

    def test_gpu_off_is_started_and_then_stopped_and_verified(self):
        self.gpu.update(running=False,ready=False,remaining=0)
        job=self.run_job(session_id="c1")
        self.assertEqual(job["state"],"completed");self.assertEqual(self.calls,["start","stop"])
        r=json.loads(live.exchange(job["id"])["receipt"])
        self.assertTrue(r["gpu"]["started_by_coordina"]);self.assertIn("EXITED",r["gpu_cierre"]["gpu"])

    def test_foreign_session_about_to_close_is_not_renewed(self):
        self.gpu.update(remaining=120)
        ticks=iter([0,0,0,0])
        with patch.object(live.time,"monotonic",side_effect=lambda:next(ticks,100000)):
            d=live.coordinate("joker","Busca contratos",str(self.work),launch=False,timeout=60,session_id="c1")
            with patch.object(live.time,"sleep"):live.worker(d["id"])
        self.assertNotIn("start",self.calls);self.assertIn(b.job_status(d["id"])["state"],("session_busy","session_unavailable"))


    def test_grace_hands_over_to_a_pending_joker_job(self):
        self.gpu.update(running=False,ready=False,remaining=0)
        d=live.coordinate("joker","Primero",str(self.work),launch=False,timeout=60,session_id="c1")
        waiting=live.coordinate("joker","Segundo",str(self.work),launch=False,timeout=60,session_id="c1")
        with patch.object(live.time,"sleep"):live.worker(d["id"])
        r=json.loads(live.exchange(d["id"])["receipt"])
        self.assertEqual(r["gpu_cierre"]["traspaso"],waiting["id"]);self.assertEqual(self.calls,["start"])
        with patch.object(live.time,"sleep"):live.worker(waiting["id"])
        r2=json.loads(live.exchange(waiting["id"])["receipt"])
        self.assertTrue(r2["gpu"]["started_by_coordina"]);self.assertEqual(self.calls,["start","stop"])

    def test_batch_uses_one_gpu_session_and_one_conversation(self):
        self.gpu.update(running=False,ready=False,remaining=0)
        d=live.joker_batch("joker",["Uno","Dos","Tres"],str(self.work),timeout=60,route="new",launch=False)
        self.assertEqual(len(d["lote"]),3)
        with patch.object(live.time,"sleep"):live.batch_worker(d["lote"])
        states=[b.job_status(i)["state"] for i in d["lote"]]
        self.assertEqual(states,["completed"]*3);self.assertEqual(self.calls,["start","stop"])
        self.assertEqual({live.exchange(i)["endpoint"]["session_id"] for i in d["lote"]},{"c2"})

    def test_grace_waits_and_a_late_job_keeps_the_gpu_on(self):
        cfg=json.loads((b.home()/"config.json").read_text());cfg["joker"]["gracia_min"]=10;(b.home()/"config.json").write_text(json.dumps(cfg))
        with patch.object(live,"joker_pending",side_effect=[[],[],["tardio"]]),patch.object(live.time,"sleep"):
            r=live.joker_release(True,"x")
        self.assertEqual(r["traspaso"],"tardio");self.assertNotIn("stop",self.calls)

    def test_grace_expires_then_stops(self):
        cfg=json.loads((b.home()/"config.json").read_text());cfg["joker"]["gracia_min"]=10;(b.home()/"config.json").write_text(json.dumps(cfg))
        clock=iter([0,100,700,700,700,700,700,700,700,700])
        with patch.object(live,"joker_pending",return_value=[]),patch.object(live.time,"sleep"),patch.object(live.time,"monotonic",side_effect=lambda:next(clock)):
            r=live.joker_release(True,"x")
        self.assertIn("EXITED",r["gpu"]);self.assertIn("10.0 min de gracia",r["gpu"]);self.assertEqual(self.calls,["stop"])

    def test_unknown_job_count_never_stops_the_gpu(self):
        with patch.object(live,"joker_pending",return_value=[]),patch.object(live,"joker_cli",side_effect=lambda a,timeout=60:{"error":"x"} if a[0]=="apagar-si-libre" else {"trabajos_activos":None}):
            r=live.joker_release(True,"x")
        self.assertNotIn("stop",self.calls);self.assertIn("guardián",r["gpu"])

    def test_atomic_server_stop_is_preferred(self):
        def cli(a,timeout=60):
            if a[0]=="apagar-si-libre":self.gpu.update(running=False);return {"apagada":True}
            raise AssertionError("no debe consultar gpu")
        with patch.object(live,"joker_pending",return_value=[]),patch.object(live,"joker_cli",side_effect=cli),patch.object(live.time,"sleep"):
            r=live.joker_release(True,"x")
        self.assertIn("EXITED",r["gpu"]);self.assertNotIn("stop",self.calls)

    def test_failed_start_is_retried(self):
        self.gpu.update(running=False,ready=False,remaining=0)
        intentos=[]
        def start():
            intentos.append(1)
            if len(intentos)==2:self.gpu.update(running=True,ready=True,remaining=3600)
            return "op"
        clock=iter(range(0,10000,60))
        with patch.object(live,"qwen_start",side_effect=start),patch.object(live,"qwen_start_failed",return_value=True),\
             patch.object(live.time,"sleep"),patch.object(live.time,"monotonic",side_effect=lambda:next(clock)):
            r=live.joker_gpu("x",5000)
        self.assertEqual(r["state"],"ready");self.assertEqual(len(intentos),2)

    def test_transient_cdn_redirect_is_retried(self):
        salidas=iter(['{"state":"error","http_status":307,"error":"redireccion_rechazada"}','{"principal":{"status":"EXITED"},"trabajos_activos":0}'])
        class P:pass
        def run(*a,**k):p=P();p.stdout=next(salidas);p.stderr="";return p
        with patch.object(live.subprocess,"run",side_effect=run),patch.object(live.time,"sleep"):
            d=live.joker_cli(["gpu"])
        self.assertEqual(d["trabajos_activos"],0)

    def test_batch_is_only_for_joker(self):
        with self.assertRaises(ValueError):live.joker_batch("gpt",["Uno"],str(self.work),launch=False)

    def test_batch_tool_is_published(self):
        self.assertIn("coordinate_batch",[n for n,_,_ in b.TOOLS])


if __name__=="__main__":
    unittest.main()
