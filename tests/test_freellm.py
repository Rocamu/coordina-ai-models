import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import coordina
import live
import freellm_backend as f


class FreeRouteTests(unittest.TestCase):
    def model(self):
        return dict(platform=f.PROVIDER, modelId=f.MODEL, enabled=True)

    def key(self):
        return dict(platform=f.PROVIDER, modelScope=[f.MODEL], enabled=True)

    def test_unrelated_catalog_updates_do_not_block_explicit_nvidia_route(self):
        available=f.validate_policy([self.model(),dict(platform='paid',modelId='anything',enabled=True)], [self.key()])
        self.assertEqual(available,[f.MODEL])

    def test_unknown_selected_model_never_calls_network(self):
        with patch.object(f,'request') as request:
            for model in ['auto','paid/other','nvidia:'+f.MODEL,'']:
                with self.assertRaises(ValueError):f.generate('job','hi',model=model)
            request.assert_not_called()

    def test_selected_model_must_be_enabled_unique_and_scoped(self):
        for models,keys in [([dict(self.model(),enabled=False)],[self.key()]),
                            ([self.model(),self.model()],[self.key()]),
                            ([dict(self.model(),endpointScope='https://other.invalid')],[self.key()]),
                            ([self.model()],[dict(self.key(),modelScope=[f.ALLOWED_MODELS[1]])]),
                            ([self.model()],[dict(self.key(),modelScope=[f.MODEL,'paid/other'])])]:
            with self.subTest(models=models,keys=keys),self.assertRaises(ValueError):f.validate_policy(models,keys)

    def test_provider_pinned_request_and_identity_verified_for_each_model(self):
        for model in f.ALLOWED_MODELS:
            response=dict(model=model,choices=[dict(finish_reason='stop',message=dict(content='{}'))])
            with patch.object(f,'status'),patch.object(f,'request',return_value=(response,{'X-Routed-Via':'nvidia/'+model},200)) as request:
                result=f.generate('job','hi',model=model)
                self.assertEqual(request.call_args.args[1]['model'],'nvidia:'+model)
                self.assertEqual(result['metadata']['model'],model)
                self.assertFalse(result['metadata']['paid_fallback'])

    def test_unification_disabled_fails_before_inference(self):
        replies=[([self.model()],{},200),([self.key()],{},200),({'enabled':False},{},200)]
        with patch.object(f,'request',side_effect=replies) as request:
            with self.assertRaises(ValueError):f.generate('job','hi')
            self.assertEqual(request.call_count,3)

    def test_coordinate_preserves_model_and_idempotency(self):
        import os
        with tempfile.TemporaryDirectory() as workspace,patch.dict(os.environ,COORDINA_HOME=workspace+'/state'):
            a=live.coordinate('freellm','hi',workspace,route='new',timeout=120,model=f.ALLOWED_MODELS[1],request_id='same',launch=False)
            b=live.coordinate('freellm','hi',workspace,route='new',timeout=120,model=f.ALLOWED_MODELS[1],request_id='same',launch=False)
            self.assertEqual(a['id'],b['id']);self.assertEqual(a['spec']['model'],f.ALLOWED_MODELS[1])
            with self.assertRaises(ValueError):live.coordinate('freellm','hi',workspace,route='new',timeout=120,model=f.MODEL,request_id='same',launch=False)
            for opts in [dict(route='auto'),dict(route='new',reasoning_effort='low')]:
                with self.assertRaises(ValueError):live.coordinate('freellm','hi',workspace,timeout=120,model=f.MODEL,launch=False,**opts)

    def test_new_key_or_unrestricted_key_blocks_the_call(self):
        for keys in [[self.key(),self.key()], [dict(self.key(),modelScope=None)]]:
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                f.validate_policy([self.model()], keys)

    def test_real_route_mismatch_rejects_answer(self):
        response = dict(model=f.MODEL,choices=[dict(finish_reason='stop',message=dict(content='hello'))])
        with patch.object(f,'status'),patch.object(f,'request',return_value=(response,{'X-Routed-Via':'paid/other'},200)):
            with self.assertRaises(ValueError): f.generate('job','hello')

    def test_failed_preflight_never_calls_inference(self):
        with patch.object(f,'status',side_effect=ValueError('changed')),patch.object(f,'request') as request:
            with self.assertRaises(ValueError): f.generate('job','hello')
            request.assert_not_called()

    def test_redirect_never_forwards_local_key(self):
        with self.assertRaises(ValueError):
            f.NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.invalid')

    def test_no_tools_rounds_or_long_timeout(self):
        with tempfile.TemporaryDirectory() as workspace:
            for options in [dict(mode='edit',timeout=120),dict(rounds=2,timeout=120),dict(timeout=121)]:
                with self.subTest(options=options),self.assertRaises(ValueError):
                    coordina.start_job('freellm','test',workspace,launch=False,**options)


if __name__ == '__main__': unittest.main()
