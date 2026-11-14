import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import deepseek_official as d
import live
import coordina as b


class OfficialTests(unittest.TestCase):
    AUTH={'human_quote':'test-only explicit approval','source':'test','observed_at':'2026-11-08T00:00:00+00:00','evidence':'synthetic fixture'}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.p=patch.object(d,'STATE',self.root/'budget.sqlite3'); self.p.start()
        # A valid test fixture, independent of the production tariff review date.
        self.price=patch.object(d,'PRICE_VALID_UNTIL','2100-01-01T00:00:00+00:00');self.price.start()
        self.env=patch.dict(os.environ,{'COORDINA_HOME':str(self.root/'state')});self.env.start()

    def tearDown(self):
        self.env.stop();self.price.stop();self.p.stop();self.tmp.cleanup()

    def test_concurrent_reservations_never_exceed_cap(self):
        def attempt(i):
            try: d.reserve(str(i));return True
            except ValueError:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes=list(pool.map(attempt,range(40)))
        self.assertEqual(sum(outcomes),d.CAP//d.RESERVE)
        self.assertLessEqual(d.status()['accounted_upper_usd'],10)

    def test_authorized_limit_is_persistent_without_reset(self):
        d.reserve('uncertain')
        with d.ledger() as c: before=c.execute('SELECT * FROM charges').fetchall()
        result=d.change_limit(17_891_204_200,d.CAP,self.AUTH,'approval-1')
        self.assertTrue(result['changed'])
        with d.ledger() as c:
            self.assertEqual(c.execute('SELECT * FROM charges').fetchall(),before)
            event=c.execute('SELECT old_cap,new_cap,authorization_json FROM budget_events').fetchone()
        self.assertEqual(event[:2],(10_000_000_000,17_891_204_200))
        self.assertEqual(json.loads(event[2]),self.AUTH)
        self.assertEqual(d.status()['limit_usd'],17.8912042)
        self.assertEqual(d.status()['uncertain_or_active'],1)
        self.assertFalse(d.change_limit(17_891_204_200,d.CAP,self.AUTH,'approval-1')['changed'])
        with d.ledger() as c:self.assertEqual(c.execute('SELECT count(*) FROM budget_events').fetchone()[0],1)

    def test_missing_or_stale_authorization_fails_without_change(self):
        with self.assertRaises(ValueError):d.change_limit(20_000_000_000,d.CAP,{},'x')
        with self.assertRaises(ValueError):d.change_limit(20_000_000_000,1,self.AUTH,'x')
        self.assertEqual(d.status()['limit_usd'],10)

    def test_limit_cannot_erase_accounted_reserve(self):
        d.reserve('uncertain')
        with self.assertRaises(ValueError):d.change_limit(0,d.CAP,self.AUTH,'x')
        self.assertEqual(d.status()['uncertain_or_active'],1)

    def test_updated_cap_is_enforced_under_concurrent_reservations(self):
        d.change_limit(2*d.RESERVE,d.CAP,self.AUTH,'two-reservations')
        def attempt(i):
            try:d.reserve(str(i));return True
            except ValueError:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes=list(pool.map(attempt,range(6)))
        self.assertEqual(sum(outcomes),2)
        self.assertEqual(d.status()['remaining_usd'],0)

    def test_uncertain_request_remains_reserved_and_cannot_replay(self):
        d.reserve('a')
        with self.assertRaises(ValueError):d.reserve('a')
        with self.assertRaises(ValueError):d.settle('a',{})
        self.assertEqual(d.status()['accounted_upper_usd'],d.RESERVE/1e9)
        self.assertEqual(d.status()['uncertain_or_active'],1)

    def test_settlement_does_not_reset_other_reservations(self):
        d.reserve('a');d.reserve('b')
        self.assertEqual(d.settle('a',{'prompt_tokens':1000,'completion_tokens':100}),.00042)
        with self.assertRaises(ValueError):d.settle('a',{'prompt_tokens':0,'completion_tokens':0})
        self.assertEqual(d.status()['accounted_upper_usd'],(d.RESERVE+420000)/1e9)

    def test_expired_tariff_fails_closed(self):
        with patch.object(d,'PRICE_VALID_UNTIL','2000-01-01T00:00:00+00:00'):
            with self.assertRaises(ValueError):d.reserve('x')
        self.assertEqual(d.status()['accounted_upper_usd'],0)

    def test_route_no_discovery_no_tools_one_round(self):
        with patch.object(live,'discover',side_effect=AssertionError('no discovery')):
            first=live.coordinate('deepseek-official','test',str(self.root),request_id='x',launch=False)
            again=live.coordinate('deepseek-official','test',str(self.root),request_id='x',launch=False)
        self.assertEqual(first['id'],again['id'])
        for args in ({'mode':'edit'},{'rounds':2},{'route':'existing'}):
            with self.assertRaises(ValueError):
                live.coordinate('deepseek-official','test',str(self.root),launch=False,**args)

    def test_failed_network_keeps_reservation_and_does_not_retry(self):
        key=self.root/'key';key.write_text('sk-dummy');key.chmod(0o600)
        with patch.object(d,'KEY',key),patch.object(d,'request',side_effect=TimeoutError) as request:
            with self.assertRaises(TimeoutError):d.generate('a','test')
        self.assertEqual(request.call_count,1)
        self.assertEqual(d.status()['accounted_upper_usd'],d.RESERVE/1e9)

    def test_response_cost_and_no_tool_payload(self):
        key=self.root/'key';key.write_text('sk-dummy');key.chmod(0o600)
        reply={'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}],
               'usage':{'prompt_tokens':100,'completion_tokens':10}}
        with patch.object(d,'KEY',key),patch.object(d,'request',return_value=reply) as request:
            result=d.generate('a','Return JSON')
        payload=request.call_args.args[1]
        self.assertNotIn('tools',payload)
        self.assertEqual(payload['max_tokens'],65_536)
        self.assertEqual(json.loads(result['answer']),{'ok':True})
        self.assertEqual(result['metadata']['cost_upper_usd'],.000042)

    def test_long_output_settles_usage_not_full_reserve_and_keeps_cap(self):
        d.change_limit(17_891_204_200,d.CAP,self.AUTH,'expanded-budget')
        d.reserve('other-uncertain')
        key=self.root/'key';key.write_text('sk-dummy');key.chmod(0o600)
        reply={'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}],
               'usage':{'prompt_tokens':1000,'completion_tokens':50000}}
        with patch.object(d,'KEY',key),patch.object(d,'request',return_value=reply):
            result=d.generate('long','test')
        self.assertEqual(d.RESERVE,393216000)
        self.assertEqual(result['metadata']['cost_upper_usd'],.0603)
        self.assertEqual(d.status()['accounted_upper_usd'],.453516)
        self.assertEqual(d.status()['limit_usd'],17.8912042)
        self.assertEqual(d.status()['uncertain_or_active'],1)

    def test_usage_over_65536_keeps_maximum_reservation(self):
        d.reserve('over')
        with self.assertRaises(ValueError):d.settle('over',{'prompt_tokens':1000,'completion_tokens':65537})
        self.assertEqual(d.status()['accounted_upper_usd'],.393216)
        self.assertEqual(d.status()['uncertain_or_active'],1)

    def test_truncated_answer_is_charged_once_without_retry(self):
        key=self.root/'key';key.write_text('sk-dummy');key.chmod(0o600)
        reply={'choices':[{'message':{'content':''},'finish_reason':'length'}],
               'usage':{'prompt_tokens':1000,'completion_tokens':65536}}
        with patch.object(d,'KEY',key),patch.object(d,'request',return_value=reply) as request:
            with self.assertRaises(ValueError):d.generate('truncated','test')
        self.assertEqual(request.call_count,1)
        self.assertEqual(d.status()['accounted_upper_usd'],.0789432)
        self.assertEqual(d.status()['uncertain_or_active'],0)

    def test_fast_route_shares_budget_and_disables_reasoning(self):
        job=live.coordinate('deepseek-official-fast','Return JSON',str(self.root),launch=False)
        self.assertEqual(job['spec']['target'],'deepseek-official-fast')
        key=self.root/'key';key.write_text('sk-dummy');key.chmod(0o600)
        reply={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
               'usage':{'prompt_tokens':100,'completion_tokens':10}}
        d.reserve('other')
        with patch.object(d,'KEY',key),patch.object(d,'request',return_value=reply) as request:
            result=d.generate('fast','Return JSON',thinking='disabled')
        payload=request.call_args.args[1]
        self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertNotIn('reasoning_effort',payload)
        self.assertEqual(result['metadata']['budget']['accounted_upper_usd'],(d.RESERVE+42000)/1e9)

    def test_compact_preserves_answer_once_and_keeps_full_transcript_on_disk(self):
        job=live.coordinate('deepseek-official','x',str(self.root),launch=False)
        b.update_job(job['id'],state='completed',result='hello',transcript=json.dumps([{'answer':'hello','metadata':{'ok':True}}]))
        compact=b.job_status(job['id'],compact=True)
        self.assertEqual(compact['result'],'hello')
        self.assertNotIn('answer',compact['transcript'][0])
        self.assertEqual(compact['transcript'][0]['answer_ref'],'result')
        self.assertEqual(b.job_status(job['id'])['transcript'][0]['answer'],'hello')


if __name__=='__main__':unittest.main()
