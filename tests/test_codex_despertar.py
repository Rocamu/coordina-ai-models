"""Entrega a hilos de Codex no cargados: espera correcta del propietario y despertar por enlace."""
import json, os, socket, struct, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
import live

CARGADO = "11111111-1111-1111-1111-111111111111"
DORMIDO = "22222222-2222-2222-2222-222222222222"
OTRO = "33333333-3333-3333-3333-333333333333"


class CodexFalso(threading.Thread):
    """Enrutador IPC mínimo: responde al instante por hilos cargados y con demora por los demás."""

    def __init__(self, ruta, demora=0.4):
        super().__init__(daemon=True)
        self.cargados, self.demora, self.consultas = {CARGADO}, demora, []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(ruta)); os.chmod(ruta, 0o600); self.srv.listen(8)

    def run(self):
        while True:
            try:
                con, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.atender, args=(con,), daemon=True).start()

    def atender(self, con):
        cerrojo = threading.Lock()

        def enviar(p):
            d = json.dumps(p).encode()
            with cerrojo:
                try:
                    con.sendall(struct.pack("<I", len(d)) + d)
                except OSError:
                    pass

        def leer(n):
            r = b""
            while len(r) < n:
                x = con.recv(n - len(r))
                if not x:
                    raise ConnectionError
                r += x
            return r

        try:
            while True:
                p = json.loads(leer(struct.unpack("<I", leer(4))[0]))
                if p["method"] == "initialize":
                    enviar(dict(type="response", requestId=p["requestId"], resultType="success", result={"clientId": "c"}))
                    continue
                hilo = p["params"]["conversationId"]; self.consultas.append(hilo)
                if hilo in self.cargados:
                    enviar(dict(type="response", requestId=p["requestId"], resultType="success", handledByClientId="dueno",
                                result={"supportsUntrustedAppInput": True}))
                else:
                    r = dict(type="response", requestId=p["requestId"], resultType="error", error="no-client-found")
                    threading.Timer(self.demora, enviar, (r,)).start()
        except (ConnectionError, OSError, struct.error):
            pass


class Pruebas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coordina-ipc-", dir="/tmp")
        d = Path(self.tmp.name); os.chmod(d, 0o700)
        self.ruta = d / "ipc.sock"
        self.codex = CodexFalso(self.ruta); self.codex.start()
        ruta = self.ruta

        def iniciar(ipc, path=None, timeout=2):
            ipc.path, ipc.timeout, ipc.sock, ipc.client_id = ruta, timeout, None, "initializing-client"

        self.parches = [patch.object(live, "codex_home", lambda: d), patch.object(live.NativeIPC, "__init__", iniciar),
                        patch.object(live, "OWNER_WAIT", 3)]
        for x in self.parches:
            x.start()
        live.WOKEN.clear()

    def tearDown(self):
        for x in self.parches:
            x.stop()
        self.codex.srv.close(); self.tmp.cleanup()

    def filas(self, *ids):
        return [dict(family="codex", session_id=i, title=i, workspace="/x", roots=["/x"], status="open", live=True) for i in ids]

    def test_varios_dormidos_cuestan_una_sola_espera(self):
        with live.NativeIPC(timeout=3) as ipc:
            t = time.monotonic()
            r = ipc.owners([DORMIDO, CARGADO, OTRO])
        self.assertLess(time.monotonic() - t, 0.9)  # en serie serían 0,8 s o más por los dos dormidos
        self.assertIsNone(r[DORMIDO]); self.assertIsNone(r[OTRO]); self.assertEqual(r[CARGADO]["client_id"], "dueno")

    def test_dormido_no_es_fallo_de_descubrimiento(self):
        with patch.object(live, "codex_metadata", return_value=self.filas(CARGADO, DORMIDO)), patch.object(live.subprocess, "run") as abrir:
            r = live.discover("codex")
        self.assertEqual([x["session_id"] for x in r], [CARGADO]); abrir.assert_not_called()

    def test_descubrir_sin_entrega_no_toca_la_app(self):
        with patch.object(live, "codex_metadata", return_value=self.filas(DORMIDO)), patch.object(live.subprocess, "run") as abrir:
            self.assertEqual(live.discover("codex", session_id=DORMIDO), [])
        abrir.assert_not_called()

    def despertar(self, *_a, **_k):
        self.codex.cargados.add(DORMIDO)
        return type("R", (), {"returncode": 0})()

    def test_entrega_a_hilo_nombrado_lo_despierta(self):
        with patch.object(live, "codex_metadata", return_value=self.filas(DORMIDO)), patch.object(live.subprocess, "run", side_effect=self.despertar) as abrir:
            r = live.discover("codex", session_id=DORMIDO, wake=True)
        self.assertEqual(r[0]["client_id"], "dueno")
        self.assertEqual(abrir.call_args[0][0], ["/usr/bin/open", "-g", "codex://threads/" + DORMIDO])

    def test_sin_hilo_nombrado_no_despierta_otro(self):
        with patch.object(live, "codex_metadata", return_value=self.filas(DORMIDO, OTRO)), patch.object(live.subprocess, "run") as abrir:
            self.assertEqual(live.discover("codex", wake=True), [])
        abrir.assert_not_called()

    def test_no_repite_el_enlace_en_cada_reintento(self):
        with patch.object(live.subprocess, "run", return_value=type("R", (), {"returncode": 0})()) as abrir:
            self.assertIsNone(live.wake_codex(DORMIDO, wait=0))
            self.assertIsNone(live.wake_codex(DORMIDO, wait=0))
        self.assertEqual(abrir.call_count, 1)

    def test_rechaza_identificador_invalido(self):
        with patch.object(live.subprocess, "run") as abrir:
            self.assertIsNone(live.wake_codex("x&open=calc"))
        abrir.assert_not_called()

    def test_app_cerrada_se_abre_con_el_enlace(self):
        self.codex.srv.close(); self.ruta.unlink()
        def abrir(*_a, **_k):
            self.codex = CodexFalso(self.ruta); self.codex.cargados.add(DORMIDO); self.codex.start()
            return type("R", (), {"returncode": 0})()
        with patch.object(live, "codex_metadata", return_value=self.filas(DORMIDO)), patch.object(live.subprocess, "run", side_effect=abrir):
            r = live.discover("codex", session_id=DORMIDO, wake=True)
        self.assertEqual(r[0]["session_id"], DORMIDO)


if __name__ == "__main__":
    unittest.main()
