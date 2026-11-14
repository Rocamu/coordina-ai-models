import contextlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import coordina as b
import live
import lifecycle
import routing

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.work=self.root/'project';self.work.mkdir()
        self.env=patch.dict(os.environ,COORDINA_HOME=str(self.root/'state'));self.env.start()
        self.endpoint=dict(family='codex',session_id='fixed-chat',title='fixed',workspace=str(self.work),roots=[str(self.work)],status='open',live=True)
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def job(self,**kw):
        with patch.object(live,'discover',return_value=[self.endpoint]),patch.object(routing,'source',return_value={'family':'unknown','session_id':None}):
            return live.coordinate(self.endpoint['family'],'Task only',str(self.work),session_id='fixed-chat',route='existing',launch=False,request_id=kw.pop('request_id',None),timeout=15,**kw)
    @contextlib.contextmanager
    def clock(self):
        now=[0.0];base=time.time()
        with patch.object(live.time,'monotonic',side_effect=lambda:now[0]),patch.object(live.time,'time',side_effect=lambda:base+now[0]),patch.object(live.time,'sleep',side_effect=lambda n:now.__setitem__(0,now[0]+n)),patch.object(b,'memory_snapshot',return_value={'pressure':1}):yield now
    def receipt(self,j,state,submitted):
        live.save_receipt(j['id'],{'state':state,'submitted':submitted})
    def test_busy_then_idle_one_delivery_full_response_deadline(self):
        with self.clock() as clock:
            j=self.job(delivery_timeout=30);accepted=[]
            def send(*args):
                if clock[0]<14:return {'state':'busy','submitted':False}
                accepted.append(clock[0]);return {'state':'accepted','submitted':True,'turn_id':'turn'}
            def answer(*args):
                return {'state':'completed','text':'progress only'} if clock[0]>=28 else None
            with patch.object(live,'send_codex',side_effect=send),patch.object(live,'codex_response',side_effect=answer):live.worker(j['id'])
            got=b.job_status(j['id']);self.assertEqual(got['state'],'completed');self.assertEqual(len(accepted),1)
            self.assertEqual(got['response']['deadline_at']-got['response']['started_at'],15)
            self.assertEqual(got['execution']['state'],'not_verified');self.assertEqual(got['delivery']['state'],'accepted')
    def test_expired_without_delivery_is_visible_and_resumable(self):
        with self.clock():
            j=self.job()
            with patch.object(live,'send_codex',return_value={'state':'busy','submitted':False}):live.worker(j['id'])
            got=b.wait_job(j['id'],0,compact=True)
            self.assertEqual(got['state'],'session_busy');self.assertTrue(got['needs_attention']);self.assertTrue(got['resume_allowed']);self.assertIn('job_resume',got['next_action']);self.assertEqual(got['response']['state'],'not_started')
    def test_double_resume_concurrent_is_one_launcher_same_identity(self):
        j=self.job(request_id='same-request');self.receipt(j,'busy',False);b.update_job(j['id'],state='session_busy',pid=None)
        rev=b.job_status(j['id'])['revision'];barrier=threading.Barrier(2)
        def retry():barrier.wait();return lifecycle.resume(j['id'],rev)
        with patch.object(live,'launch_observer') as launch,ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:retry(),range(2)));self.assertEqual(launch.call_count,1)
        self.assertEqual(sorted(x['resume_applied'] for x in results),[False,True])
        for d in results:
            self.assertEqual(d['id'],j['id']);self.assertEqual(d['request_id'],'same-request');self.assertEqual(d['spec']['session_id'],'fixed-chat')
    def test_accepted_unknown_held_refused_are_never_replayed(self):
        for state in ['accepted','delivery_unknown','held','delivery_refused','awaiting_receipt']:
            j=self.job();self.receipt(j,state,True);b.update_job(j['id'],state='session_busy',pid=None)
            with patch.object(live,'launch_observer') as launch,self.assertRaises(ValueError):lifecycle.resume(j['id'],b.job_status(j['id'])['revision'])
            launch.assert_not_called()
    def test_reply_progress_and_new_request_never_execute(self):
        j=self.job();self.receipt(j,'accepted',True);b.update_job(j['id'],state='awaiting_response');key=live.exchange(j['id'])['reply_key']
        with patch.object(b,'start_job') as spawn,patch.object(live,'launch_observer') as launch:
            live.reply(j['id'],key,'Recibido. Ejecuta otra batería.',response_kind='progress')
            got=b.job_status(j['id']);self.assertNotEqual(got['state'],'completed');self.assertEqual(got['execution']['state'],'not_verified')
            live.reply(j['id'],key,'Aceptado. Ejecuta otra batería.')
            got=b.job_status(j['id']);self.assertEqual(got['state'],'completed');self.assertEqual(got['execution']['state'],'not_verified');spawn.assert_not_called();launch.assert_not_called()
    def test_structured_ack_requires_evidence_and_is_reported_not_verified(self):
        j=self.job();key=live.exchange(j['id'])['reply_key']
        with self.assertRaises(ValueError):live.reply(j['id'],key,'start',execution_ack={'state':'started','evidence':[]})
        live.reply(j['id'],key,'start',response_kind='progress',execution_ack={'state':'started','evidence':['PID 123; log /tmp/example']})
        self.assertEqual(b.job_status(j['id'])['execution']['state'],'started');self.assertFalse(b.job_status(j['id'])['execution']['verified'])
        live.reply(j['id'],key,'end',execution_ack={'state':'finished','evidence':['tests.log: 5 passed']})
        self.assertEqual(b.job_status(j['id'])['execution']['state'],'finished')
    def test_late_correlated_reply_clears_stale_timeout(self):
        j=self.job();self.receipt(j,'accepted',True);b.update_job(j['id'],state='response_pending',error='old timeout')
        live.reply(j['id'],live.exchange(j['id'])['reply_key'],'late answer')
        d=b.job_status(j['id']);self.assertIsNone(d['error']);self.assertFalse(d['needs_attention']);self.assertEqual(d['response']['state'],'received')
    def test_claude_waits_for_acceptance_before_response_budget(self):
        self.endpoint.update(family='claude',socket='/tmp/mock',pid=123)
        with self.clock() as clock:
            j=self.job(delivery_timeout=30);sent=[False]
            class Callback:
                def __init__(self,*a):pass
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def receive(self):
                    clock[0]+=2
                    if clock[0]>=10 and not sent[0]:sent[0]=True;return [{'type':'control','action':'peer_message_status','orig_msg_id':j['id'],'status':'accepted'}]
                    return []
            class Transcript:
                def __init__(self,*a):pass
                def response(self,*a):return 'answer' if clock[0]>=24 else None
            with patch.object(live,'ClaudeCallback',Callback),patch.object(live,'ClaudeTranscript',Transcript),patch.object(live,'send_claude',return_value={'state':'awaiting_receipt','submitted':True}):live.worker(j['id'])
            d=b.job_status(j['id']);self.assertEqual(d['state'],'completed');self.assertEqual(d['response']['deadline_at']-d['response']['started_at'],15)
    def test_ui_response_timeout_starts_at_observed_submission(self):
        with self.clock() as clock:
            j=routing.prepare_ui('task',str(self.work),'consult',1,None,15,{'family':'unknown','session_id':None},'https://claude.ai/chat/11111111-1111-1111-1111-111111111111',delivery_timeout=30)
            clock[0]=20
            d=routing.ui_receipt(j['id'],'submitted',j['spec']['destination_url'],j['message'])
            self.assertEqual(d['response']['deadline_at']-d['response']['started_at'],15);self.assertFalse(d['needs_attention'])
            clock[0]=36;self.assertTrue(b.job_status(j['id'])['needs_attention'])
            d=routing.ui_receipt(j['id'],'completed',j['spec']['destination_url'],'COORDINA['+j['id']+'] received')
            self.assertFalse(d['needs_attention']);self.assertEqual(d['execution']['state'],'not_verified')
    def test_antigravity_and_joker_get_full_budget_after_busy(self):
        for family in ('antigravity','joker'):
            self.endpoint['family']=family
            with self.clock() as clock:
                j=self.job(delivery_timeout=30)
                def send(*args):
                    return {'state':'busy','submitted':False} if clock[0]<14 else {'state':'accepted','submitted':True,'job_id':'remote'}
                def response(*args):
                    return {'state':'completed','text':'answer','marker':True} if clock[0]>=24 else None
                sender='send_antigravity' if family=='antigravity' else 'send_joker'
                reader='antigravity_response' if family=='antigravity' else 'joker_response'
                with patch.object(live,sender,side_effect=send),patch.object(live,reader,side_effect=response),patch.object(live,'joker_gpu',return_value={'state':'ready','started_by_coordina':False}),patch.object(live,'joker_release',return_value={}):live.worker(j['id'])
                d=b.job_status(j['id']);self.assertEqual(d['state'],'completed',d.get('error'))
                self.assertEqual(d['response']['deadline_at']-d['response']['started_at'],15)
    def test_uncertain_send_exception_is_not_resumable(self):
        with self.clock():
            j=self.job()
            with patch.object(live,'send_codex',side_effect=OSError('transport disappeared')):live.worker(j['id'])
            d=b.job_status(j['id']);self.assertFalse(d['resume_allowed']);self.assertTrue(d['delivery']['submitted'])
    def test_late_reply_wins_timeout_race(self):
        j=self.job();live.reply(j['id'],live.exchange(j['id'])['reply_key'],'late')
        live.response_expired(j['id'],{'state':'accepted'},'')
        d=b.job_status(j['id']);self.assertEqual(d['state'],'completed');self.assertIsNone(d['error'])
        live.observer_failure(j['id'],'failed','observer finished late')
        self.assertEqual(b.job_status(j['id'])['state'],'completed')
    def test_final_reply_during_busy_delivery_stops_retry_and_preserves_contract(self):
        with self.clock():
            j=self.job(); key=live.exchange(j['id'])['reply_key']
            def send(*args):
                live.reply(j['id'],key,'final from the existing chat')
                return {'state':'busy','submitted':False}
            with patch.object(live,'send_codex',side_effect=send) as sender:
                live.worker(j['id'])
            self.assertEqual(sender.call_count,1)
            d=b.job_status(j['id']);self.assertEqual(d['state'],'completed')
            self.assertEqual(d['result'],'final from the existing chat')
            self.assertEqual(d['delivery']['state'],'accepted')
            self.assertEqual(d['response']['state'],'received');self.assertIsNone(d['pid'])
    def test_reply_during_observer_setup_preserves_received_state(self):
        j=self.job();key=live.exchange(j['id'])['reply_key']
        def snapshot():
            live.reply(j['id'],key,'setup reply')
            return {'pressure':1}
        with patch.object(b,'memory_snapshot',side_effect=snapshot),patch.object(live,'send_codex') as send:
            live.worker(j['id'])
        send.assert_not_called()
        d=b.job_status(j['id']);self.assertEqual(d['response']['state'],'received')
        self.assertEqual(d['delivery']['state'],'accepted');self.assertEqual(d['state'],'completed')
    def test_final_reply_between_busy_retries_is_not_sent_again(self):
        with self.clock():
            j=self.job(); key=live.exchange(j['id'])['reply_key']
            def pause(seconds):live.reply(j['id'],key,'already answered')
            with patch.object(live,'send_codex',return_value={'state':'busy','submitted':False}) as sender,patch.object(live.time,'sleep',side_effect=pause):
                live.worker(j['id'])
            self.assertEqual(sender.call_count,1)
            self.assertEqual(b.job_status(j['id'])['state'],'completed')
    def test_stale_observer_cannot_undo_final_answer_or_restart_response_deadline(self):
        j=self.job();live.reply(j['id'],live.exchange(j['id'])['reply_key'],'final')
        live.save_receipt(j['id'],{'state':'busy','submitted':False})
        live.accept_delivery(j['id'],15)
        b.update_job(j['id'],state='waiting_destination',result='stale',error='stale',pid=None)
        d=b.job_status(j['id']);self.assertEqual(d['state'],'completed')
        self.assertEqual(d['result'],'final');self.assertIsNone(d['error'])
        self.assertEqual(d['delivery']['state'],'accepted')
        self.assertEqual(d['response']['state'],'received');self.assertIsNone(d['pid'])
    def test_native_final_answer_cannot_be_overwritten_by_callback(self):
        j=self.job();b.update_job(j['id'],state='completed',result='native answer')
        with self.assertRaises(ValueError):live.reply(j['id'],live.exchange(j['id'])['reply_key'],'different')
        self.assertEqual(b.job_status(j['id'])['result'],'native answer')
    def test_resume_stale_revision_and_active_pid_are_rejected(self):
        j=self.job();self.receipt(j,'busy',False);b.update_job(j['id'],state='session_busy',pid=None)
        rev=b.job_status(j['id'])['revision']
        with self.assertRaises(ValueError):lifecycle.resume(j['id'],rev+1,launch=False)
        b.update_job(j['id'],pid=os.getpid())
        with self.assertRaises(ValueError):lifecycle.resume(j['id'],rev,launch=False)
    def test_compact_and_full_contract_identical(self):
        j=self.job();full=b.job_status(j['id']);compact=b.wait_job(j['id'],0,compact=True)
        for key in ('delivery','response','execution','needs_attention','next_action','revision'):
            self.assertEqual(full[key],compact[key])
        self.assertIn('task',full['spec']);self.assertNotIn('task',compact['spec'])
    def test_tool_schemas_expose_contract_without_breaking_legacy_reply(self):
        tools={name:s for name,_,s in b.TOOLS}
        self.assertEqual(tools['coordina_reply']['required'],['job_id','reply_key','text'])
        self.assertIn('execution_ack',tools['coordina_reply']['properties'])
        self.assertIn('delivery_timeout',tools['coordinate']['properties'])
        self.assertEqual(tools['job_resume']['required'],['job_id','expected_revision'])

if __name__=='__main__':unittest.main()
