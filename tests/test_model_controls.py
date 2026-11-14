"""Per-job selectors and CLI metadata; isolated fakes, no provider calls."""
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


class ModelControlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='coordina-model-test-')
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'COORDINA_HOME':str(self.root/'state')})
        self.env.start()
        self.work = self.root/'project'; self.work.mkdir()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def submit(self, target, **kwargs):
        return live.coordinate(target,'Test without inference',str(self.work),route='new',launch=False,**kwargs)

    def config(self, target, script='', model='configured-model'):
        fake = self.root/target
        fake.write_text('#!'+sys.executable+'\n'+script)
        fake.chmod(0o700)
        cfg={'binaries':{target:str(fake)},'models':{target:model}}
        if target=='grok':cfg['auto_approve']=True  # Grok headless needs the explicit opt-in
        (b.home()/'config.json').write_text(json.dumps(cfg))
        return cfg

    def test_override_scoped_idempotent_no_global_mutation(self):
        for target in ['codex','claude','grok']:
            with self.subTest(target=target):
                self.config(target)
                original=(b.home()/'config.json').read_bytes()
                opts=dict(model=target+'-exact-1',reasoning_effort='medium',request_id=target)
                a=self.submit(target,**opts)
                self.assertEqual(a['id'],self.submit(target,**opts)['id'])
                self.assertEqual(a['spec']['model'],opts['model'])
                with self.assertRaises(ValueError):self.submit(target,**dict(opts,model='other-model'))
                with self.assertRaises(ValueError):self.submit(target,**dict(opts,reasoning_effort='high'))
                self.assertEqual((b.home()/'config.json').read_bytes(),original)

    def test_scope_rejected_before_discovery_or_launch(self):
        with patch.object(live,'propose') as discover,patch.object(b,'enqueue_job') as enqueue:
            for target,route,rounds in [('codex','existing',1),('claude','auto',1),('grok','auto',1),('gemini','new',1),('deepseek','new',1),('codex','new',2),('claude','new',2)]:
                with self.subTest(target=target,route=route):
                    with self.assertRaises(ValueError):live.coordinate(target,'x',str(self.work),route=route,rounds=rounds,model='exact',launch=False)
            discover.assert_not_called(); enqueue.assert_not_called()

    def test_validation_and_provider_specific_efforts(self):
        for model in ['', '--model', 'x y', 'x\ny', 'x,y', 'x'*201, True, 12, {}, ['a']]:
            with self.subTest(model=model):
                with self.assertRaises(ValueError):self.submit('claude',model=model)
        for target,values in b.EXECUTOR_EFFORTS.items():
            for value in values:b.validate_codex_controls(target,'new',1,300,value,'valid-model')
        for target,value in [('codex','max'),('claude','deep'),('grok','invalid')]:
            with self.assertRaises(ValueError):self.submit(target,reasoning_effort=value)

    def test_omitted_controls_keep_old_spec_and_command(self):
        for target in ['codex','claude','grok']:
            self.config(target)
            job=self.submit(target)
            self.assertNotIn('model',job['spec']);self.assertNotIn('reasoning_effort',job['spec'])
            cmd,_=b.command_for(target,job['spec'],self.root/'1.answer.txt','prompt')
            flag='-m' if target=='grok' else '--model'
            self.assertEqual(cmd[cmd.index(flag)+1],'configured-model')
            self.assertNotIn('--effort',cmd);self.assertNotIn('--reasoning-effort',cmd)

    def test_native_model_and_effort_flags(self):
        for target in ['codex','claude','grok']:
            self.config(target)
            job=self.submit(target,model='specific-version',reasoning_effort='high')
            cmd,_=b.command_for(target,job['spec'],self.root/'1.answer.txt','prompt')
            flag='-m' if target=='grok' else '--model'
            self.assertEqual(cmd[cmd.index(flag)+1],'specific-version')
            if target=='codex':self.assertIn('model_reasoning_effort="high"',cmd)
            else:
                flag='--effort' if target=='claude' else '--reasoning-effort'
                self.assertEqual(cmd[cmd.index(flag)+1],'high')

    def run_fake(self,target,payload,model='selected-model',extra_script=''):
        if target=='codex':
            output='pathlib.Path(a[a.index("-o")+1]).write_text("I claim to be fake-model")\n'
            payload=dict(type='turn.completed',**payload)
        else:
            output=''
            payload=dict({('result' if target=='claude' else 'text'):'I claim to be fake-model'},**payload)
        script='import sys,json,pathlib,os\na=sys.argv\nsys.stdin.read()\n'+output+extra_script+'\nprint('+repr(json.dumps(payload))+')\n'
        self.config(target,script)
        job=self.submit(target,model=model,reasoning_effort='medium')
        with patch.object(b,'memory_snapshot',return_value={'pressure':1}):b.worker(job['id'])
        result=b.job_status(job['id'])
        self.assertEqual(result['state'],'completed',result.get('error'))
        receipt=json.loads((b.home()/'runs'/job['id']/'1.selection.json').read_text())
        self.assertEqual(receipt['model_selection']['sent_to_cli'],model)
        return result['transcript'][0]['metadata']

    def test_claude_and_grok_preserve_structured_report_in_full(self):
        usage={'input_tokens':3,'output_tokens':7,'cache_read_input_tokens':2,'nested':{'thinking_tokens':4}}
        mu={'served-version':{'canonicalModel':'served-snapshot','provider':'firstParty','costBasis':'list','costUSD':0.0123}}
        for target in ['claude','grok']:
            with self.subTest(target=target):
                payload={'model':'served-version','modelUsage':mu,'canonicalModel':'served-snapshot','usage':usage,'total_cost_usd':0.0123,'costBasis':'list','reasoning_effort':'high'}
                meta=self.run_fake(target,payload)
                for k,v in payload.items():self.assertEqual(meta[k],v)
                self.assertEqual(meta['model_selection']['requested'],'selected-model')
                self.assertEqual(meta['model_selection']['configured'],'configured-model')
                self.assertEqual(meta['requested_reasoning_effort'],'medium')

    def test_codex_preserves_report_without_manufacturing_model_or_cost(self):
        usage={'input_tokens':10,'cached_input_tokens':4,'reasoning_output_tokens':2}
        meta=self.run_fake('codex',{'usage':usage})
        self.assertEqual(meta['usage'],usage)
        self.assertEqual(meta['cli_reports'],[{'usage':usage}])
        for k in ['model','modelUsage','canonicalModel','total_cost_usd','costBasis']:self.assertNotIn(k,meta)
        meta=self.run_fake('codex',{'model':'reported','canonicalModel':'canonical','costBasis':'list','total_cost_usd':0,'usage':usage})
        self.assertEqual(meta['model'],'reported');self.assertEqual(meta['total_cost_usd'],0)

    def test_missing_fields_and_self_declaration_not_evidence(self):
        for target in ['claude','grok']:
            meta=self.run_fake(target,{})
            for key in b.CLI_REPORT_FIELDS:self.assertNotIn(key,meta)
            self.assertNotIn('fake-model',json.dumps(meta))

    def test_launch_snapshot_survives_global_change(self):
        change='p=pathlib.Path(os.environ["COORDINA_HOME"])/"config.json"\nc=json.loads(p.read_text());c["models"]["codex"]="changed-after-launch";p.write_text(json.dumps(c))\n'
        meta=self.run_fake('codex',{'usage':{}},extra_script=change)
        self.assertEqual(meta['model_selection']['configured'],'configured-model')
        self.assertEqual(meta['requested_model'],'selected-model')

    def test_aliases_not_claimed_immutable(self):
        for name in ['opus','flash','model-latest']:
            selection=b.model_selection('claude',{'model':name},{})
            self.assertEqual(selection['selector_kind'],'known_moving_alias')
            self.assertFalse(selection['immutable_version_verified'])
        self.assertEqual(b.model_selection('claude',{}, {})['selector_kind'],'cli_default_unresolved')

    def test_mcp_dispatch_and_cli_expose_controls(self):
        schema=next(s for n,d,s in b.TOOLS if n=='coordinate')['properties']
        self.assertIn('model',schema)
        with patch.object(b,'enqueue_job',return_value={'id':'dry-run'}) as enqueue:
            result=b.dispatch('coordinate',dict(target='claude',task='x',workspace=str(self.work),route='new',model='claude-fixed',reasoning_effort='max'),'test','test')
        self.assertEqual(result['id'],'dry-run')
        self.assertEqual(enqueue.call_args.args[0]['model'],'claude-fixed')
        helptext=subprocess.run([sys.executable,str(ROOT/'coordina.py'),'submit','--help'],capture_output=True,text=True,check=True).stdout
        self.assertIn('--model',helptext);self.assertIn('max',helptext)


if __name__=='__main__':unittest.main()
