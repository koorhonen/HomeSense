"""Create a private CA, server certificate, and random device token without network calls."""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import re
import secrets


def create(host: str, output: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

    try:
        san = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?", host):
            raise ValueError("Provide a server IP address or DNS name, not a URL.")
        san = x509.DNSName(host)
    names = ["ca.crt", "server.crt", "server.key", "satellite-token.txt"]
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in names):
        raise ValueError("Credentials already exist. Use a new directory; existing credentials will not be overwritten.")
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HomeSense satellite CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False,
              content_commitment=False, data_encipherment=False, key_agreement=False,
              key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    server = (x509.CertificateBuilder().subject_name(server_name).issuer_name(ca_name)
          .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
          .add_extension(x509.SubjectAlternativeName([san]), critical=False)
          .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
          .sign(ca_key, hashes.SHA256()))
    data = [ca.public_bytes(serialization.Encoding.PEM), server.public_bytes(serialization.Encoding.PEM),
            server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()),
            (secrets.token_urlsafe(32) + "\n").encode("ascii")]
    for name, content in zip(names, data):
        descriptor = os.open(str(output / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    # Do not retain the CA private key; only the server key remains on the server.


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="The server’s fixed LAN IP address or DNS name")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent.parent / ".secrets")
    args = parser.parse_args()
    create(args.host, args.output)
    print("Credentials created. Securely copy only ca.crt and satellite-token.txt to the Pi.")
