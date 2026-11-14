import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('COORDINA_TEST_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import coordina as b
import live

class CodexControlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='coordina-controls-')
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'COORDINA_HOME':str(self.root/'state')})
        self.env.start()
        self.work = self.root/'project'; self.work.mkdir()
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def submit(self, **kwargs):
        return live.coordinate('codex','Prueba local sin modelos',str(self.work),route='new',launch=False,**kwargs)
    def test_extended_turn_preserved_and_idempotent(self):
        opts=dict(timeout=3600,reasoning_effort='medium',request_id='same')
        a=self.submit(**opts)
        self.assertEqual(a['spec']['timeout'],3600)
        self.assertEqual(a['spec']['reasoning_effort'],'medium')
        self.assertEqual(a['id'],self.submit(**opts)['id'])
        with self.assertRaises(ValueError):self.submit(timeout=3600,reasoning_effort='high',request_id='same')
    def test_limits_and_scope(self):
        for target,route,rounds,timeout,effort in [('claude','new',1,901,None),('codex','existing',1,901,None),('codex','auto',1,3600,None),('codex','new',2,3600,None),('codex','new',1,3601,None),('codex','new',1,True,None),('codex','new',1,300,'bad'),('codex','existing',1,300,'medium')]:
            with self.subTest(target=target,route=route,rounds=rounds,timeout=timeout,effort=effort):
                with self.assertRaises(ValueError):b.validate_codex_controls(target,route,rounds,timeout,effort)
    def test_default_remains_unspecified(self):
        job=self.submit()
        self.assertNotIn('reasoning_effort',job['spec'])
        self.assertEqual(job['spec']['timeout'],300)
    def test_explicit_queue_deadline_and_cap(self):
        self.assertEqual(b.admission_timeout({'timeout':3600}),900)
        self.assertEqual(b.admission_timeout({'timeout':3600,'delivery_timeout':15}),15)
        self.assertEqual(b.admission_timeout({'timeout':120}),120)
    def test_queue_expiration_does_not_start_executor(self):
        import fcntl
        import time
        job=self.submit(timeout=3600,delivery_timeout=15)
        with b.database() as c:
            c.execute("UPDATE jobs SET created=? WHERE id=?",(time.time()-16,job['id']))
        with open(b.home()/'worker.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(b,'run_turn') as run:
                b.worker(job['id'])
                run.assert_not_called()
        result=b.job_status(job['id'])
        self.assertEqual(result['state'],'session_busy')
        self.assertIsNone(result['pid'])

    def test_real_subprocess_receives_effort_and_records_metadata(self):
        fake=self.root/'codex'
        fake.write_text('#!'+sys.executable+'\nimport sys,json,pathlib\na=sys.argv\nassert \'model_reasoning_effort="medium"\' in a\nsys.stdin.read()\npathlib.Path(a[a.index("-o")+1]).write_text("OK")\nprint(json.dumps({"type":"turn.completed","usage":{"input_tokens":1}}))\n')
        fake.chmod(0o700)
        (b.home()/'config.json').write_text(json.dumps({'binaries':{'codex':str(fake)},'models':{'codex':'test-model'}}))
        job=self.submit(timeout=1800,reasoning_effort='medium')
        with patch.object(b,'memory_snapshot',return_value={'pressure':1}):b.worker(job['id'])
        result=b.job_status(job['id'])
        self.assertEqual(result['state'],'completed',result.get('error'))
        meta=result['transcript'][0]['metadata']
        self.assertEqual(meta['requested_reasoning_effort'],'medium')
        self.assertEqual(meta['requested_model'],'test-model')
        self.assertEqual(meta['timeout_seconds'],1800)
        self.assertIsNone(result['pid'])
    def test_dispatch_schema_accepts_options_without_model(self):
        with patch.object(b,'start_job',wraps=b.start_job) as start,patch.object(b,'enqueue_job',return_value={'id':'dry-run'}):
            result=b.dispatch('coordinate',dict(target='codex',task='test',workspace=str(self.work),route='new',timeout=3600,reasoning_effort='medium'),'test','test')
        self.assertEqual(result['id'],'dry-run')
        self.assertEqual(start.call_args.kwargs['reasoning_effort'],'medium')

if __name__=='__main__':unittest.main()
