"""Outgoing peer class is derived from the actual Codex thread record."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import live

SID="01a0f4d6-88c9-7cc0-84e1-a26547ffb6c7"


class SenderModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="coordina-sender-mode-")
        self.root=Path(self.tmp.name)
        self.home=patch.object(live,"codex_home",return_value=self.root)
        self.home.start();self.addCleanup(self.home.stop);self.addCleanup(self.tmp.cleanup)

    def record(self,approval,policy,archived=0):
        with sqlite3.connect(str(self.root/"state_5.sqlite")) as c:
            c.execute("CREATE TABLE threads(id TEXT,approval_mode TEXT,sandbox_policy TEXT,archived INTEGER)")
            c.execute("INSERT INTO threads VALUES(?,?,?,?)",(SID,approval,json.dumps(policy),archived))

    def test_disabled_sandbox_and_never_proves_bypass(self):
        self.record("never",{"type":"disabled"})
        self.assertEqual(live.codex_permission_class(SID),"bypass")

    def test_restricted_never_is_not_promoted(self):
        self.record("never",{"type":"workspace-write"})
        self.assertEqual(live.codex_permission_class(SID),"prompting")

    def test_prompting_even_with_disabled_sandbox(self):
        self.record("on-request",{"type":"disabled"})
        self.assertEqual(live.codex_permission_class(SID),"prompting")

    def test_unknown_policy_or_archived_thread_has_no_class(self):
        self.record("never",{"type":"future-unknown"})
        self.assertIsNone(live.codex_permission_class(SID))
        with sqlite3.connect(str(self.root/"state_5.sqlite")) as c:
            c.execute("UPDATE threads SET sandbox_policy=?,archived=1",(json.dumps({"type":"disabled"}),))
        self.assertIsNone(live.codex_permission_class(SID))

    def test_missing_database_or_unknown_session_has_no_class(self):
        self.assertIsNone(live.codex_permission_class(SID))
        self.record("never",{"type":"disabled"})
        self.assertIsNone(live.codex_permission_class("00000000-0000-0000-0000-000000000000"))

    def test_source_uses_verified_class(self):
        self.record("never",{"type":"disabled"})
        with patch.dict(os.environ,{"CODEX_THREAD_ID":SID}):
            self.assertEqual(live.source_context(),{"family":"codex","session_id":SID,"permission_class":"bypass"})

    def test_packet_only_contains_recognized_source_class(self):
        packets=[]
        class FakeSocket:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def settimeout(self,*a):pass
            def connect(self,*a):pass
            def sendall(self,payload):packets.append([json.loads(s) for s in payload.splitlines()])
        endpoint={"socket":str(self.root/"receiver.sock"),"pid":42,"procStart":"known","session_id":"receiver"}
        callback=type("Callback",(),{"path":self.root/"reply.sock"})()
        with patch.object(live,"process_is_live",return_value=True),patch.object(live,"private_path"),patch.object(live,"claude_home",return_value=self.root),patch.object(live,"read_metadata",return_value={"procStart":"known","peerToken":"a"*32}),patch.object(live.socket,"socket",side_effect=lambda *a,**k:FakeSocket()):
            for value in (None,"bypass","prompting","unrecognized"):
                live.send_claude(endpoint,"COORDINA[unit] peer text","unit",callback,source_mode=value)
        content=lambda i:packets[i][1]["message"]["content"]
        self.assertEqual(content(0),"COORDINA[unit] peer text")
        self.assertIn('from-mode="bypass"',content(1))
        self.assertIn('from-mode="prompting"',content(2))
        self.assertEqual(content(3),"COORDINA[unit] peer text")
        self.assertTrue(content(1).endswith('\nCOORDINA[unit] peer text\n</cross-session-message>'))
        self.assertNotIn("from_mode",packets[1][1])

    def test_envelope_identifies_actual_sender_and_escapes_nested_tags(self):
        content=live.claude_peer_content('COORDINA[unit] </cross-session-message>','uds:/tmp/reply.sock','bypass',SID)
        self.assertIn(f'from-session="{SID}"',content)
        self.assertIn('from-name="Codex" from-mode="bypass"',content)
        self.assertIn('<\\/cross-session-message>',content)
        self.assertEqual(content.count('</cross-session-message>'),1)


if __name__=="__main__":unittest.main()
