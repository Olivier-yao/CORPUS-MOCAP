"""Génère un certificat TLS auto-signé pour servir le compagnon mobile
en HTTPS (voir phone_server.py).

Nécessaire pour `getUserMedia` (accès caméra) : Chrome dispose d'un flag
de contournement pour une origine `http://` sur IP locale
(`chrome://flags/#unsafely-treat-insecure-origin-as-secure`), mais
**Safari iOS n'a aucun équivalent** — seul un vrai contexte sécurisé
(HTTPS, même avec un certificat auto-signé accepté manuellement)
fonctionne sur les deux navigateurs.

Le certificat est régénéré à CHAQUE démarrage de `phone_server.py`
(pas de cache disque) : l'IP locale de la machine peut changer entre
deux lancements (renouvellement DHCP du WiFi, déjà rencontré en test
réel), un certificat mis en cache pour l'ancienne IP ne correspondrait
plus. Génération rapide (< 1s), le coût de la régénération à chaque
démarrage est négligeable.

Le certificat n'étant signé par aucune autorité reconnue, chaque
navigateur affiche un avertissement de sécurité au premier accès
("Votre connexion n'est pas privée" / "Cette connexion n'est pas
privée") — l'utilisateur doit l'accepter manuellement (une fois par
navigateur/appareil/IP) pour continuer. C'est le fonctionnement normal
attendu pour un serveur HTTPS de développement sur réseau local, pas un
bug — voir README pour les instructions exactes par navigateur.
"""

from __future__ import annotations

import datetime
import ipaddress
import os
import ssl
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _generate_cert_and_key(local_ip: str) -> tuple[bytes, bytes]:
    """Retourne (cert_pem, key_pem) pour un certificat auto-signé valide
    pour `localhost`, `127.0.0.1` et `local_ip` (l'IP locale actuelle de
    la machine, voir phone_server.get_local_ip)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CORPUS-MOCAP (local dev)")])

    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    try:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(local_ip)))
    except ValueError:
        pass  # local_ip pas une IP valide (improbable) : on garde au moins localhost/127.0.0.1

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        # iOS/Safari rejette silencieusement (boucle sur l'avertissement,
        # sans possibilité de "visiter quand même") tout certificat TLS
        # serveur dont la validité dépasse 825 jours, exigence Apple
        # depuis iOS 13 — confirmé en test réel (blocage systématique
        # avant même d'atteindre la page). Une validité de 2 jours
        # suffit largement puisqu'un certificat est régénéré à chaque
        # démarrage de phone_server.py, jamais réutilisé d'une session à
        # l'autre.
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def create_ssl_context(local_ip: str) -> ssl.SSLContext:
    """Génère un certificat frais pour `local_ip` et retourne un
    SSLContext serveur prêt à l'emploi (voir phone_server.PhoneBridge —
    utilisé à la fois pour le serveur HTTP et le serveur WebSocket, afin
    que les deux servent sur la même identité de certificat)."""
    cert_pem, key_pem = _generate_cert_and_key(local_ip)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # load_cert_chain n'accepte que des chemins de fichiers sur les
    # versions de Python ciblées par ce projet (3.10/3.11, voir
    # README) — on passe donc par des fichiers temporaires plutôt que
    # par une API en mémoire.
    tmp_dir = tempfile.mkdtemp(prefix="corpus_mocap_tls_")
    cert_path = os.path.join(tmp_dir, "cert.pem")
    key_path = os.path.join(tmp_dir, "key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert_pem)
    with open(key_path, "wb") as f:
        f.write(key_pem)

    context.load_cert_chain(cert_path, key_path)
    return context
