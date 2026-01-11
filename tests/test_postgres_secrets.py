
import json
import base64
import unittest
from unittest.mock import MagicMock
from app.domain.secrets.kek_provider import EnvKekProvider, EncryptedEnvelope
from app.adapters.postgres.stores import PostgresSecretStore

class MockSecret:
    def __init__(self, name, ciphertext, nonce, tag, key_id, version=1):
        self.name = name
        self.ciphertext = ciphertext
        self.nonce = nonce
        self.tag = tag
        self.key_id = key_id
        self.version = version

class TestPostgresSecretStore(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.kek_provider = EnvKekProvider("test-master-key")
        self.store = PostgresSecretStore(self.db, self.kek_provider)

    def test_set_secret_encryption(self):
        # Mock DB query to return None (new secret)
        self.db.query.return_value.filter.return_value.first.return_value = None
        
        secret_name = "test-secret"
        secret_value = "my-secret-value"
        
        self.store.set_secret(secret_name, secret_value)
        
        # Verify db.add was called
        args, _ = self.db.add.call_args
        secret_obj = args[0]
        
        self.assertEqual(secret_obj.name, secret_name)
        
        # Verify fields are set
        self.assertTrue(secret_obj.ciphertext)
        self.assertTrue(secret_obj.nonce)
        self.assertTrue(secret_obj.tag)
        self.assertTrue(secret_obj.key_id)
        
        # Verify valid base64
        base64.b64decode(secret_obj.ciphertext)
        base64.b64decode(secret_obj.nonce)
        base64.b64decode(secret_obj.tag)
        
        print(f"\n[PASS] Secret stored with key_id={secret_obj.key_id}")

    def test_get_secret_decryption(self):
        secret_name = "test-secret"
        secret_value = "my-secret-value"
        
        # Pre-encrypt manually
        envelope = self.kek_provider.encrypt(secret_value.encode('utf-8'))
        
        mock_secret = MockSecret(
            name=secret_name, 
            ciphertext=base64.b64encode(envelope.ciphertext).decode('ascii'),
            nonce=base64.b64encode(envelope.nonce).decode('ascii'),
            tag=base64.b64encode(envelope.tag).decode('ascii'),
            key_id=envelope.key_id
        )
        self.db.query.return_value.filter.return_value.first.return_value = mock_secret
        
        decrypted = self.store.get_secret_value(secret_name)
        self.assertEqual(decrypted, secret_value)
        print(f"\n[PASS] Decrypted value matches: {decrypted}")

if __name__ == "__main__":
    unittest.main()
