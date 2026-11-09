"""The simulator's Node.js process runs under Node's permission model: no reads outside its folder,
no writes, no child processes, no workers, no eval. Checked against the Node on this machine."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from benchforge import sim
from benchforge.toolchain import find_node, node_version

NODE = find_node()
PROBE = r"""
const out = {};
try { require('fs').readFileSync('/etc/hosts'); out.read = 'allowed'; } catch (e) { out.read = e.code; }
try { require('fs').writeFileSync(__dirname + '/x.txt', 'x'); out.write = 'allowed'; } catch (e) { out.write = e.code; }
try { require('child_process').execSync('echo hi'); out.exec = 'allowed'; } catch (e) { out.exec = e.code; }
try { new (require('worker_threads').Worker)('1', { eval: true }); out.worker = 'allowed'; } catch (e) { out.worker = e.code; }
try { eval('1'); out.eval = 'allowed'; } catch (e) { out.eval = e.name; }
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE and node_version(NODE)[:1] >= (20,), "needs Node.js 20+")
class NodeSandbox(unittest.TestCase):
    def test_engine_process_is_boxed_in(self):
        d = Path(tempfile.mkdtemp())
        (d / "probe.js").write_text(PROBE)
        flags = [f.replace(str(sim.ENGINE_DIR), str(d)) for f in sim.permission_flags(node_version(NODE))]
        p = subprocess.run([NODE, *flags, "--disallow-code-generation-from-strings", str(d / "probe.js")],
                           capture_output=True, text=True, timeout=30)
        got = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(got, {"read": "ERR_ACCESS_DENIED", "write": "ERR_ACCESS_DENIED", "exec": "ERR_ACCESS_DENIED",
                               "worker": "ERR_ACCESS_DENIED", "eval": "EvalError"})
        self.assertFalse((d / "x.txt").exists())


if __name__ == "__main__":
    unittest.main()
