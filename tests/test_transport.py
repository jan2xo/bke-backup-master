import hashlib
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import bke_backup_master as b


class TransportTests(unittest.TestCase):
    def test_rest_multipart_stream_and_download_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            src=root/"test.age"
            content=b"synthetic-encrypted-bytes-" * 1000
            src.write_bytes(content)
            key=b.make_key()
            class FakeResponse:
                status=200
                def read(self, maximum):
                    return json.dumps({"success":True,"result":{"key":key}}).encode()
            class FakeConn:
                def __init__(self, host, timeout):
                    self.headers={}
                    self.body=bytearray()
                def putrequest(self,method,path):
                    self.method=method
                    self.path=path
                def putheader(self,name,value):
                    self.headers[name.lower()]=value
                def endheaders(self):
                    pass
                def send(self,chunk):
                    self.body.extend(chunk)
                def getresponse(self):
                    return FakeResponse()
                def close(self):
                    pass
            connections=[]
            def connection(host,timeout):
                self.assertEqual(host,b.API_HOST)
                c=FakeConn(host,timeout)
                connections.append(c)
                return c
            with patch.object(b.http.client,"HTTPSConnection",side_effect=connection):
                b.upload_rest(src,key,account="a"*32,bucket="test-bucket",token="synthetic")
            self.assertEqual(len(connections),1)
            request=connections[0]
            self.assertEqual(request.method,"PUT")
            self.assertTrue(request.path.endswith("/"+key))
            self.assertEqual(request.headers["authorization"],"Bearer synthetic")
            self.assertEqual(len(request.body),int(request.headers["content-length"]))
            self.assertIn(content,request.body)
            self.assertIn(b'name="body"',request.body)
            destination=root/"download.age"
            class FakeDownload(io.BytesIO):
                def __enter__(self):
                    return self
                def __exit__(self,*args):
                    self.close()
            def get(req,timeout):
                self.assertIn("Bearer synthetic",list(req.headers.values()))
                return FakeDownload(content)
            with patch.object(b,"urlopen",side_effect=get):
                digest,size=b.download_rest(key,destination,account="a"*32,
                                            bucket="test-bucket",token="synthetic")
            self.assertEqual(digest,hashlib.sha256(content).hexdigest())
            self.assertEqual(size,len(content))
            self.assertEqual(destination.read_bytes(),content)

    def test_restore_rejects_nonlocal_docker_context(self):
        with patch.dict(b.os.environ,{},clear=True), \
             patch.object(b.shutil,"which",return_value="/usr/bin/fake"), \
             patch.object(b,"command",return_value='[{"Endpoints":{"docker":{"Host":"ssh://remote.example"}}}]'):
            with self.assertRaisesRegex(b.BackupError,"local Unix-socket"):
                b.isolation_preflight()

    def test_cleanup_failure_is_never_restore_pass(self):
        from types import SimpleNamespace
        def subproc(argv,**kwargs):
            return SimpleNamespace(returncode=1)
        inspect='[{"HostConfig":{"NetworkMode":"host"},"State":{"Running":true}}]'
        with patch.object(b.subprocess,"run",side_effect=subproc), \
             patch.object(b,"docker",side_effect=["volume-created","container-id",inspect]):
            with self.assertRaisesRegex(b.BackupError,"ISOLATED_RESTORE_CLEANUP_FAILED"):
                b.restore_isolated(Path("/unused.dump"))


if __name__=="__main__":
    unittest.main()
