"""IPv4 endpoint selection and useful errors for the native desktop apps.

Finding the preferred source address only asks the local routing table: a UDP
``connect`` without a subsequent ``send`` does not transmit a discovery packet.
These helpers neither scan the network nor require an internet service.
"""
from __future__ import annotations

import errno
import ipaddress
import re
import socket
import unicodedata


def _usable_ipv4(value: str) -> ipaddress.IPv4Address | None:
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError):
        return None
    if (address.is_loopback or address.is_multicast or address.is_unspecified
            or address == ipaddress.IPv4Address("255.255.255.255")):
        return None
    return address


def local_addresses() -> list[str]:
    """Return usable IPv4 addresses, preferred route first and APIPA last.

    A route may not exist while disconnected; hostname resolution can also
    fail independently. Each source is therefore attempted independently.
    Addresses from VPN interfaces and locally assigned public IPs are retained.
    APIPA remains available for a direct local link, but is never recommended
    ahead of an address outside the link-local range.
    """
    addresses: set[ipaddress.IPv4Address] = set()
    preferred = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            preferred = _usable_ipv4(probe.getsockname()[0])
            if preferred is not None:
                addresses.add(preferred)
    except OSError:
        pass
    try:
        records = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        for record in records:
            address = _usable_ipv4(record[4][0])
            if address is not None:
                addresses.add(address)
    except OSError:
        pass
    return [str(address) for address in sorted(
        addresses,
        key=lambda address: (address.is_link_local, address != preferred, int(address)),
    )]


def classify_address(address: str) -> str:
    """Classify an IPv4 address for display, without inferring reachability."""
    try:
        parsed = ipaddress.IPv4Address(address)
    except (ipaddress.AddressValueError, TypeError):
        raise ValueError("Informe um endereço IPv4 válido.") from None
    if parsed.is_link_local:
        return "link-local"
    return "local" if parsed.is_private else "network"


def normalize_target(value: str) -> str:
    """Validate one IPv4 address or DNS hostname; the port is a separate field.

    No DNS query happens during validation. Unicode DNS labels become IDNA,
    allowing name resolution to remain in the connection worker rather than
    blocking the interface. IPv6 is intentionally rejected because the current
    desktop listener is IPv4-only.
    """
    if not isinstance(value, str):
        raise ValueError("Informe um único IPv4 ou nome do Hospedeiro.")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("O endereço contém caracteres de controle inválidos.")
    value = value.strip()
    if not value:
        raise ValueError("Informe o IPv4 ou o nome do Hospedeiro.")
    if any(character.isspace() for character in value) or any(
            character in value for character in "/\\,;|@?#"):
        raise ValueError("Informe somente um IPv4 ou nome, sem URL ou lista de endereços.")
    if ":" in value or "[" in value or "]" in value:
        try:
            ipaddress.IPv6Address(value.strip("[]"))
        except ipaddress.AddressValueError:
            raise ValueError("Informe o endereço sem a porta; use o campo Porta separado.") from None
        raise ValueError("Este Hospedeiro aceita IPv4 ou nome de rede; IPv6 ainda não é suportado.")
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        if re.fullmatch(r"[0-9.]+", value):
            raise ValueError("O endereço IPv4 é inválido; confira os quatro números.") from None
    else:
        # Loopback is a valid explicit target for local diagnostics, although it
        # is excluded from addresses advertised to another computer.
        if parsed.is_multicast or parsed.is_unspecified or value == "255.255.255.255":
            raise ValueError("Use um IPv4 da rede ou VPN, sem multicast ou broadcast.")
        return str(parsed)
    absolute = value.endswith(".")
    hostname = value[:-1] if absolute else value
    try:
        hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("O nome do Hospedeiro é inválido.") from None
    if len(hostname) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")):
        raise ValueError("Use um nome de rede válido, com letras, números, pontos e hífens.")
    return hostname + ("." if absolute else "")


def connection_error_hint(error: BaseException, address: str = "") -> str:
    """Explain connection failures without treating authorization as transient."""
    if isinstance(error, PermissionError):
        detail = str(error).strip()
        return ("Acesso não autorizado. " + detail if detail else
                "Acesso não autorizado. Confira a senha e a aprovação no Hospedeiro.")
    code = getattr(error, "winerror", None) or getattr(error, "errno", None)
    if isinstance(error, socket.gaierror):
        message = ("Não foi possível localizar o Hospedeiro. Confira o nome ou use o IPv4 "
                   "da rede ativa ou da VPN mostrado no Hospedeiro.")
    elif isinstance(error, ConnectionRefusedError) or code in (errno.ECONNREFUSED, 10061):
        message = ("O Hospedeiro recusou a conexão. Abra o Hospedeiro, inicie o atendimento "
                   "e confira a mesma porta nos dois aplicativos e a permissão no firewall.")
    elif isinstance(error, TimeoutError) or code in (errno.ETIMEDOUT, 10060):
        message = ("O Hospedeiro não respondeu a tempo. Confira o endereço e a porta, "
                   "a rede ou VPN dos dois computadores e a permissão no firewall.")
    elif code in (errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH,
                  errno.EADDRNOTAVAIL, getattr(errno, "EHOSTDOWN", -1),
                  10049, 10050, 10051, 10064, 10065):
        message = ("A rede do Hospedeiro está indisponível ou não há uma rota até ele. "
                   "Conecte os computadores à mesma rede ou VPN e confira o endereço atual.")
    elif isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        message = ("A conexão foi interrompida. Confira a rede ou VPN e se o Hospedeiro "
                   "continua aberto antes de reconectar.")
    else:
        message = str(error).strip() or "Não foi possível estabelecer a conexão."
    try:
        link_local = ipaddress.IPv4Address(address).is_link_local
    except (ipaddress.AddressValueError, TypeError):
        link_local = False
    if link_local:
        message += (" O endereço 169.254 é de um enlace local; prefira o IPv4 da rede "
                    "ativa ou da VPN indicado no Hospedeiro.")
    return message
