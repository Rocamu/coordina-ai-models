"""Mailbox receipts must never turn durable storage into claimed delivery."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get("COORDINA_TEST_RUNTIME", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("peer_receipts_bridge", ROOT / "coordina.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class PeerReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coordina-peer-test-")
        self.env = patch.dict(os.environ, {"COORDINA_HOME":self.tmp.name})
        self.env.start()
        b.peer_register("sender", "Sender", "test")
        b.peer_register("recipient", "Recipient", "test")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def activity(self, seen):
        with b.database() as c:
            c.execute("UPDATE peers SET seen=? WHERE id='recipient'", (seen,))

    def test_disconnected_warns_and_preserves_original_unacked(self):
        self.activity(0)
        sent = b.send_message("sender", "Recipient", "original", "original-thread")
        self.assertEqual(sent["recipient_activity"]["state"], "disconnected")
        self.assertEqual(sent["delivery"], "stored")
        self.assertTrue(sent["needs_attention"])
        self.assertFalse(sent["wake_existing_chat"])
        self.assertFalse(sent["automatic_retry"])
        self.assertIn("desconectado", sent["warning"])
        for _ in range(3):
            status = b.peer_message_status("sender", sent["message_id"])
            self.assertFalse(status["recipient_acknowledged"])
        with b.database() as c:
            rows = c.execute("SELECT * FROM messages").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body"], "original")
        self.assertIsNone(rows[0]["acked"])

    def test_stale_does_not_claim_disconnection(self):
        with patch.object(b.time, "time", return_value=1000):
            self.activity(699)
            sent = b.send_message("sender", "Recipient", "test")
        self.assertEqual(sent["recipient_activity"]["state"], "stale")
        self.assertIn("no se confirma", sent["warning"])

    def test_recent_activity_is_not_read_receipt(self):
        sent = b.send_message("sender", "Recipient", "test")
        self.assertEqual(sent["recipient_activity"]["state"], "recent")
        self.assertFalse(sent["recipient_activity"]["reader_confirmed"])
        self.assertTrue(sent["needs_attention"])
        self.assertFalse(sent["response_verified"])

    def test_poll_then_ack_is_distinct_from_response(self):
        self.activity(0)
        sent = b.send_message("sender", "Recipient", "test")
        b.inbox("recipient")
        status = b.peer_message_status("sender", sent["message_id"])
        self.assertEqual(status["recipient_activity"]["state"], "recent")
        self.assertEqual(status["delivery"], "stored")
        b.ack("recipient", [sent["message_id"]])
        status = b.dispatch("peer_message_status", {"message_id":sent["message_id"]}, "sender", "test")
        self.assertEqual(status["delivery"], "acknowledged")
        self.assertFalse(status["response_verified"])
        self.assertFalse(status["needs_attention"])

    def test_status_scope_and_invalid_id(self):
        sent = b.send_message("sender", "Recipient", "secret body")
        with self.assertRaises(ValueError):
            b.peer_message_status("stranger", sent["message_id"])
        for value in [True, 0, -1, "1"]:
            with self.assertRaises(ValueError): b.peer_message_status("sender", value)
        self.assertNotIn("secret body", str(b.peer_message_status("recipient", sent["message_id"])))

    def test_missing_peer_and_tool_schema(self):
        sent = b.send_message("sender", "Recipient", "test")
        with b.database() as c: c.execute("DELETE FROM peers WHERE id='recipient'")
        status = b.peer_message_status("sender", sent["message_id"])
        self.assertEqual(status["recipient_activity"]["state"], "unknown")
        tool = next(s for n,d,s in b.TOOLS if n == "peer_message_status")
        self.assertEqual(tool["required"], ["message_id"])


if __name__ == "__main__": unittest.main()
