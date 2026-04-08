"""
Encryption utilities for secure storage of session cookies and tokens.

Uses Fernet symmetric encryption (AES-128-CBC with HMAC).
"""

from cryptography.fernet import Fernet, InvalidToken
import json
import structlog
from typing import Any

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class EncryptionService:
    """
    Handles encryption and decryption of sensitive data.
    
    Uses Fernet symmetric encryption which provides:
    - AES-128-CBC encryption
    - HMAC-SHA256 authentication
    - Automatic timestamp validation
    """
    
    def __init__(self, key: str = None):
        """
        Initialize encryption service.
        
        Args:
            key: Fernet-compatible encryption key. If not provided, uses settings.
        """
        self._key = key or settings.encryption_key
        self._fernet = None
        self._initialize_fernet()
    
    def _initialize_fernet(self) -> None:
        """Initialize the Fernet cipher."""
        try:
            # Ensure key is bytes
            key_bytes = self._key.encode() if isinstance(self._key, str) else self._key
            self._fernet = Fernet(key_bytes)
            logger.debug("Encryption service initialized")
        except Exception as e:
            logger.error("Failed to initialize encryption", error=str(e))
            raise ValueError(
                "Invalid encryption key. Generate a new key with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
    
    def encrypt(self, data: str) -> str:
        """
        Encrypt a string.
        
        Args:
            data: Plain text string to encrypt
            
        Returns:
            Base64-encoded encrypted string
        """
        if not self._fernet:
            raise RuntimeError("Encryption service not initialized")
        
        encrypted = self._fernet.encrypt(data.encode())
        return encrypted.decode()
    
    def decrypt(self, encrypted_data: str) -> str:
        """
        Decrypt an encrypted string.
        
        Args:
            encrypted_data: Base64-encoded encrypted string
            
        Returns:
            Decrypted plain text string
            
        Raises:
            InvalidToken: If decryption fails (wrong key or corrupted data)
        """
        if not self._fernet:
            raise RuntimeError("Encryption service not initialized")
        
        try:
            decrypted = self._fernet.decrypt(encrypted_data.encode())
            return decrypted.decode()
        except InvalidToken:
            logger.error("Decryption failed - invalid token or key")
            raise
    
    def encrypt_json(self, data: Any) -> str:
        """
        Encrypt a JSON-serializable object.
        
        Args:
            data: Any JSON-serializable Python object
            
        Returns:
            Base64-encoded encrypted string
        """
        json_str = json.dumps(data)
        return self.encrypt(json_str)
    
    def decrypt_json(self, encrypted_data: str) -> Any:
        """
        Decrypt and parse a JSON object.
        
        Args:
            encrypted_data: Base64-encoded encrypted JSON string
            
        Returns:
            Parsed Python object
        """
        json_str = self.decrypt(encrypted_data)
        return json.loads(json_str)
    
    @staticmethod
    def generate_key() -> str:
        """
        Generate a new Fernet encryption key.
        
        Returns:
            Base64-encoded encryption key
        """
        return Fernet.generate_key().decode()


# Singleton instance
_encryption_service: EncryptionService = None


def get_encryption_service() -> EncryptionService:
    """Get the singleton encryption service instance."""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service
