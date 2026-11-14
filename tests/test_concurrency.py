"""Process-level admission and shared-budget tests. No real model calls."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import coordina as b

RUNTIME = str(Path(b.__file__).resolve().parent)
WORKER = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import coordina as b
b.memory_snapshot = lambda: {'pressure': 1}
def run(jid, number, agent, spec, prompt):
    folder = b.home()/'runs'/jid
    with (folder/'starts.jsonl').open('a') as out:
        out.write(json.dumps({'agent': agent, 'time': time.time()})+'\n')
    until = time.monotonic()+12
    while not (folder/'release').exists():
        if (folder/'crash').exists(): os._exit(23)
        if time.monotonic()>until: raise TimeoutError('test fixture')
        time.sleep(.02)
    (folder/'ended').write_text(str(time.time()))
    return {'agent':agent,'answer':'OK','metadata':{},'finished':time.time()}
b.run_turn = run
b.worker(sys.argv[2])
'''


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='coordina-concurrency-')
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, COORDINA_HOME=str(self.root/'state'))
        self.env.start()
        self.work = self.root/'workspace'
        self.work.mkdir()
        self.children = []

    def tearDown(self):
        for p in self.children:
            if p.poll() is None:
                p.terminate()
            p.communicate(timeout=5)
        self.env.stop()
        self.tmp.cleanup()

    def job(self, target='claude', **kw):
        return b.start_job(target, 'Fixture only', str(self.work), launch=False, **kw)['id']

    def folder(self, jid):
        return b.home()/'runs'/jid

    def start(self, jid):
        p = subprocess.Popen([sys.executable, '-B', '-c', WORKER, RUNTIME, jid],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(p)
        return p

    def wait_started(self, jid):
        until = time.monotonic()+6
        while time.monotonic()<until:
            if (self.folder(jid)/'starts.jsonl').exists(): return
            time.sleep(.025)
        self.fail('Worker did not start: '+str(b.job_status(jid)))

    def release(self, jid, p):
        (self.folder(jid)/'release').touch()
        _, err = p.communicate(timeout=6)
        self.assertEqual(p.returncode, 0, err)

    def test_two_models_overlap_third_queues_and_runs_when_slot_frees(self):
        a, c, third = self.job('claude'), self.job('codex'), self.job('claude')
        pa, pc = self.start(a), self.start(c)
        self.wait_started(a); self.wait_started(c)
        self.assertEqual(b.job_status(a)['state'], 'running')
        self.assertEqual(b.job_status(c)['state'], 'running')
        admissions = [json.loads((self.folder(j)/'admission.json').read_text()) for j in (a,c)]
        self.assertEqual({d['slot'] for d in admissions}, {0,1})
        p3 = self.start(third)
        time.sleep(.3)
        self.assertFalse((self.folder(third)/'starts.jsonl').exists())
        self.assertEqual(b.job_status(third)['state'], 'queued')
        self.release(a, pa)
        self.wait_started(third)
        self.release(c, pc); self.release(third, p3)
        for jid in (a,c,third): self.assertEqual(b.job_status(jid)['state'], 'completed')

    def test_legacy_exclusive_worker_blocks_new_and_new_blocks_legacy(self):
        jid = self.job()
        with open(b.home()/'worker.lock','a') as old:
            fcntl.flock(old,fcntl.LOCK_EX|fcntl.LOCK_NB)
            p = self.start(jid)
            time.sleep(.25)
            self.assertFalse((self.folder(jid)/'starts.jsonl').exists())
            fcntl.flock(old,fcntl.LOCK_UN)
            self.wait_started(jid)
            with self.assertRaises(BlockingIOError):fcntl.flock(old,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.release(jid,p)
            fcntl.flock(old,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_duplicate_launcher_never_runs_same_job_twice(self):
        jid = self.job(); p = self.start(jid); self.wait_started(jid)
        duplicate = self.start(jid)
        duplicate.communicate(timeout=4)
        self.assertEqual(duplicate.returncode,0)
        self.assertEqual(b.job_status(jid)['pid'],p.pid)
        self.release(jid,p)
        self.assertEqual(len((self.folder(jid)/'starts.jsonl').read_text().splitlines()),1)

    def test_queued_cancel_and_expiry_release_pid_without_model(self):
        with open(b.home()/'worker.lock','a') as old:
            fcntl.flock(old,fcntl.LOCK_EX|fcntl.LOCK_NB)
            jid = self.job(); p = self.start(jid)
            time.sleep(.2); b.update_job(jid,cancel_requested=1)
            p.communicate(timeout=4)
            self.assertEqual(b.job_status(jid)['state'],'cancelled')
            self.assertIsNone(b.job_status(jid)['pid'])
            expired = self.job()
            b.update_job(expired,created=time.time()-10000)
            p = self.start(expired); p.communicate(timeout=4)
            got=b.job_status(expired)
            self.assertEqual(got['state'],'session_busy')
            self.assertIsNone(got['pid'])
            self.assertFalse((self.folder(expired)/'starts.jsonl').exists())

    def test_crashed_worker_releases_slot_without_deleting_locks(self):
        a,c = self.job(),self.job('codex')
        pa,pc = self.start(a),self.start(c)
        self.wait_started(a);self.wait_started(c)
        (self.folder(a)/'crash').touch();pa.communicate(timeout=4)
        self.assertEqual(pa.returncode,23)
        third=self.job();p3=self.start(third);self.wait_started(third)
        self.release(c,pc);self.release(third,p3)

    def test_conflicting_edits_do_not_run_concurrently(self):
        a=self.job(mode='edit');pa=self.start(a);self.wait_started(a)
        other=self.job('codex',mode='edit');p=self.start(other)
        p.communicate(timeout=4)
        self.assertEqual(b.job_status(other)['state'],'failed')
        self.assertIn('Reserva en conflicto',b.job_status(other)['error'])
        self.assertFalse((self.folder(other)/'starts.jsonl').exists())
        self.release(a,pa)

    def test_two_processes_near_budget_cap_share_atomic_reservations(self):
        script = r'''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import deepseek_official as d
d.STATE=Path(sys.argv[2]);d.PRICE_VALID_UNTIL='2100-01-01T00:00:00+00:00'
while not Path(sys.argv[3]).exists():time.sleep(.01)
try:d.reserve(sys.argv[4]);print('reserved')
except ValueError:print('blocked')
'''
        import deepseek_official as d
        with patch.object(d,'STATE',self.root/'budget.sqlite3'):
            with d.ledger() as db:
                db.execute('INSERT INTO charges VALUES (?,?,?,?,?)',('prior',9_500_000_000,'settled',time.time(),'{}'))
            gate=self.root/'go'
            processes=[subprocess.Popen([sys.executable,'-B','-c',script,RUNTIME,str(d.STATE),str(gate),str(i)],
                                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(2)]
            self.children.extend(processes);gate.touch()
            replies=[]
            for p in processes:
                out,err=p.communicate(timeout=6);self.assertEqual(p.returncode,0,err);replies.append(out.strip())
            self.assertEqual(sorted(replies),['blocked','reserved'])
            self.assertEqual(d.status()['limit_usd'],10)
            self.assertEqual(d.status()['accounted_upper_usd'],9.5+d.RESERVE/1e9)


if __name__=='__main__':unittest.main()
