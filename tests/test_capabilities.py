"""Additive inventory reflects loaded code without inference or live providers."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import coordina as b
import lifecycle

class CapabilitiesTests(unittest.TestCase):
    def test_status_and_mcp_dispatch_keep_legacy_fields_and_report_loaded_versions(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,COORDINA_HOME=folder),patch.object(b,'memory_snapshot',return_value={'pressure':1}):
            cli=b.status();mcp=b.dispatch('bridge_status',{},'test','test')
            for value in [cli,mcp]:
                for key in ['version','state_directory','binaries','memory','jobs','peers','desktop_delivery']:
                    self.assertIn(key,value)
                self.assertEqual(value['runtime_version'],b.VERSION)
                self.assertEqual(value['skill_revision'],b.SKILL_REVISION)
                projected=lifecycle.project({'id':'test','state':'queued','spec':{'route':'new'}},{})
                self.assertEqual(value['job_contract_version'],projected['contract_version'])
                self.assertIn('answering process',value['capabilities_scope'])
            self.assertEqual(cli['capabilities'],mcp['capabilities'])

    def test_scoped_codex_options_match_validator_and_mcp_schema(self):
        caps=b.capabilities();scope=caps['codex_long_timeout']['requires']
        maximum=caps['codex_long_timeout']['max_seconds']
        b.validate_codex_controls(timeout=maximum,**scope)
        with self.assertRaises(ValueError):b.validate_codex_controls(timeout=maximum+1,**scope)
        for route in ['auto','existing']:
            with self.assertRaises(ValueError):b.validate_codex_controls('codex',route,1,maximum)
        for effort in caps['codex_reasoning_effort']['values']:
            b.validate_codex_controls(timeout=maximum,reasoning_effort=effort,**scope)
        schema=next(s for name,desc,s in b.TOOLS if name=='coordinate')['properties']
        self.assertEqual(schema['timeout']['maximum'],maximum)
        self.assertEqual(schema['reasoning_effort']['enum'],list(b.ALL_EXECUTOR_EFFORTS))
        self.assertEqual(caps['executor_reasoning_effort']['values_by_target']['codex'],caps['codex_reasoning_effort']['values'])

    def test_no_claim_of_execution_verification_or_broader_output_targets(self):
        caps=b.capabilities()
        self.assertFalse(caps['phases']['execution_ack_verified'])
        self.assertEqual(caps['phases']['completed_means'],'response_received')
        self.assertEqual(caps['job_resume']['cas'],'expected_revision')
        self.assertTrue(caps['job_resume']['requires_not_submitted'])
        self.assertFalse(caps['output_path']['overwrite'])
        self.assertFalse(caps['output_path']['follows_symlinks'])
        self.assertEqual(set(caps['output_path']['targets']),{'deepseek','glm','nemotron'})

if __name__=='__main__':unittest.main()
