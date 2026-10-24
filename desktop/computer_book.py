"""User-managed computer names and endpoints; never stores connection passwords.

Writes use a flushed temporary file beside the registry and one atomic replacement.
Instances in the same process share a lock per path and reload before mutations, so
worker threads and multiple controller windows cannot silently lose each other's edits.
An unreadable or invalid existing file is reported and preserved, never reset to empty.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata
import uuid


SCHEMA = 1
MAX_COMPUTERS = 256
MAX_JSON_BYTES = 1024 * 1024
LOCK_TIMEOUT = 2.0
_FIELDS = {"id", "name", "address", "port", "created_at", "updated_at", "last_seen"}
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SCOPE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _control(value):
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)


def _name(value):
    if not isinstance(value, str) or _control(value):
        raise ValueError("Informe um nome amigável sem caracteres de controle.")
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= 80:
        raise ValueError("O nome amigável deve ter de 1 a 80 caracteres.")
    return cleaned


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("Informe uma porta inteira entre 1 e 65535.")
    return value


def _address(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 253 or _control(value) or any(char.isspace() for char in value):
        raise ValueError("Informe um endereço IP ou DNS de até 253 caracteres, sem espaços.")
    bracketed = value.startswith("[") or value.endswith("]")
    if bracketed:
        if not (value.startswith("[") and value.endswith("]")):
            raise ValueError("Use os dois colchetes para um endereço IPv6, ou retire ambos.")
        value = value[1:-1]
    if "%" in value:
        address, separator, scope = value.partition("%")
        if not _SCOPE.fullmatch(scope):
            raise ValueError("O identificador de interface do IPv6 é inválido.")
    else:
        address, scope = value, None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        if bracketed or ":" in address or scope is not None:
            raise ValueError("Informe um endereço IPv6 válido.") from None
    else:
        if (bracketed or scope is not None) and parsed.version != 6:
            raise ValueError("Colchetes e identificadores de interface são exclusivos de IPv6.")
        # Interface names can be case-sensitive; normalize the IPv6 address, not its scope.
        return parsed.compressed + ("%" + scope if scope is not None else "")
    if address.endswith("."):
        address = address[:-1]
    try:
        canonical = address.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Informe um nome DNS válido.") from None
    labels = canonical.split(".")
    if not canonical or len(canonical) > 253 or any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise ValueError("Informe somente um IP ou nome DNS, sem protocolo, caminho ou porta.")
    if len(labels) == 4 and all(label.isdecimal() for label in labels):
        raise ValueError("Informe um endereço IPv4 válido, sem zeros extras.")
    return canonical


def _identifier(value):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("O identificador do computador é inválido.")
    return value


def _timestamp(value=None):
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("Informe uma data ISO com horário e fuso, como 2026-10-10T09:00:00+00:00.") from None
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("Informe uma data ISO com horário e fuso.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("A data precisa incluir o fuso horário.")
    try:
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise ValueError("A data informada está fora do intervalo válido.") from None


class ComputerBook:
    def __init__(self, path=None):
        if path is None:
            local = os.environ.get("LOCALAPPDATA")
            base = Path(local) if local else Path.home() / ".config"
            path = base / "MiniAnyDesk" / "computers.json"
        self.path = Path(path).expanduser().absolute()
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        key = os.path.normcase(str(self.path.resolve(strict=False)))
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(key, threading.RLock())
        with self._lock:
            self._records = self._read()

    def _invalid_file(self, reason):
        return ValueError(
            f"O cadastro de computadores em {self.path} é inválido: {reason}. "
            "O arquivo foi preservado. Restaure uma cópia válida ou renomeie-o antes de criar outro cadastro."
        )

    def _read(self):
        try:
            with self.path.open("rb") as stream:
                contents = stream.read(MAX_JSON_BYTES + 1)
        except FileNotFoundError:
            return []
        except OSError as error:
            raise OSError(f"Não foi possível ler o cadastro em {self.path}: {error}") from error
        try:
            if len(contents) > MAX_JSON_BYTES:
                raise ValueError("arquivo maior que 1 MiB")
            data = json.loads(contents.decode("utf-8"))
            if not isinstance(data, dict) or set(data) != {"schema", "computers"} or type(data["schema"]) is not int or data["schema"] != SCHEMA:
                raise ValueError("formato ou versão de cadastro não reconhecidos")
            records = data["computers"]
            if not isinstance(records, list) or len(records) > MAX_COMPUTERS:
                raise ValueError("a lista deve conter no máximo 256 computadores")
            identifiers, endpoints = set(), set()
            for record in records:
                if not isinstance(record, dict) or set(record) != _FIELDS:
                    raise ValueError("campos de computador inválidos; senhas não pertencem ao cadastro")
                identifier = _identifier(record["id"])
                name = _name(record["name"])
                address, port = _address(record["address"]), _port(record["port"])
                if identifier in identifiers or (address, port) in endpoints:
                    raise ValueError("identificador ou endereço duplicado")
                identifiers.add(identifier)
                endpoints.add((address, port))
                if not isinstance(record["created_at"], str) or not isinstance(record["updated_at"], str):
                    raise ValueError("datas de criação e atualização ausentes ou inválidas")
                _timestamp(record["created_at"])
                _timestamp(record["updated_at"])
                if record["last_seen"] is not None:
                    if not isinstance(record["last_seen"], str):
                        raise ValueError("data da última conexão inválida")
                    _timestamp(record["last_seen"])
                # Normalize legitimate manual DNS/IP spelling without changing the existing file.
                record.update(name=name, address=address, port=port)
            return records
        except (ValueError, UnicodeError, TypeError, KeyError) as error:
            raise self._invalid_file(str(error)) from error

    def _write(self, records):
        temporary = None
        descriptor = None
        try:
            serialized = json.dumps({"schema": SCHEMA, "computers": records},
                ensure_ascii=False, allow_nan=False, indent=2) + "\n"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".computers-", suffix=".tmp", dir=self.path.parent)
            stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
            descriptor = None  # Ownership belongs to the stream, including on a failed write.
            with stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError as error:
            raise OSError(
                f"Não foi possível salvar o cadastro em {self.path}. "
                f"Confira as permissões e o espaço disponível; os dados anteriores foram mantidos. ({error})"
            ) from error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def entries(self):
        with self._lock:
            self._records = self._read()
            return deepcopy(self._records)

    def _restore_file_bytes(self, previous):
        """Rollback an exceptional partial commit without reusing a failed writer."""
        current = self.path.read_bytes() if self.path.exists() else None
        if current == previous:
            return
        if previous is None:
            self.path.unlink(missing_ok=True)
            if self.path.exists():
                raise OSError("O cadastro anterior não pôde ser confirmado.")
            return
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".computers-restore-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "wb") as output:
                output.write(previous); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if self.path.read_bytes() != previous:
                raise OSError("O cadastro anterior não pôde ser confirmado.")
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def _file_lock(self):
        """Bounded inter-process edit lock; the separate lock file survives atomic replacement."""
        descriptor = None
        acquired = False
        try:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
                # msvcrt locks an existing byte. Writing byte zero is harmless during first use.
                if os.fstat(descriptor).st_size < 1:
                    os.write(descriptor, b"\0")
                deadline = time.monotonic() + LOCK_TIMEOUT
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            os.lseek(descriptor, 0, os.SEEK_SET)
                            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except OSError as error:
                        import errno
                        if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EBUSY, errno.EDEADLK}:
                            raise
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise OSError(
                                "O cadastro está sendo usado por outro programa. "
                                "Aguarde alguns instantes ou feche a outra janela e tente novamente."
                            ) from error
                        time.sleep(min(0.025, remaining))
            except OSError as error:
                raise OSError(f"Não foi possível bloquear o cadastro em {self.path}: {error}") from error
            yield
        finally:
            if descriptor is not None:
                if acquired:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            os.lseek(descriptor, 0, os.SEEK_SET)
                            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                    except OSError:
                        pass
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _mutate(self, operation):
        with self._lock:
            with self._file_lock():
                candidate = deepcopy(self._read())
                result = operation(candidate)
                self._write(candidate)
                self._records = candidate
                return deepcopy(result)

    @staticmethod
    def _find(records, identifier):
        _identifier(identifier)
        for record in records:
            if record["id"] == identifier:
                return record
        raise ValueError("O computador não está mais neste cadastro. Atualize a lista e tente novamente.")

    @staticmethod
    def _unique(records, address, port, current_id=None):
        if any(record["id"] != current_id and (record["address"], record["port"]) == (address, port) for record in records):
            raise ValueError("Este IP ou DNS e porta já estão cadastrados. Renomeie o computador existente.")

    def add(self, name, address, port):
        name, address, port = _name(name), _address(address), _port(port)
        def operation(records):
            self._unique(records, address, port)
            if len(records) >= MAX_COMPUTERS:
                raise ValueError("O cadastro já contém 256 computadores. Remova um antes de adicionar outro.")
            timestamp = _timestamp()
            record = {"id": str(uuid.uuid4()), "name": name, "address": address, "port": port,
                "created_at": timestamp, "updated_at": timestamp, "last_seen": None}
            records.append(record)
            return record
        return self._mutate(operation)

    def update(self, identifier, name=None, address=None, port=None):
        clean_name = _name(name) if name is not None else None
        clean_address = _address(address) if address is not None else None
        clean_port = _port(port) if port is not None else None
        def operation(records):
            record = self._find(records, identifier)
            endpoint = clean_address if clean_address is not None else record["address"]
            number = clean_port if clean_port is not None else record["port"]
            self._unique(records, endpoint, number, identifier)
            record.update(address=endpoint, port=number, updated_at=_timestamp())
            if clean_name is not None:
                record["name"] = clean_name
            return record
        return self._mutate(operation)

    def remove(self, identifier):
        def operation(records):
            record = self._find(records, identifier)
            records.remove(record)
        return self._mutate(operation)

    def find_endpoint(self, address, port):
        endpoint = _address(address), _port(port)
        with self._lock:
            self._records = self._read()
            for record in self._records:
                if (record["address"], record["port"]) == endpoint:
                    return deepcopy(record)
        return None

    def get(self, identifier):
        """Reload by stable ID so another window's code update is immediately visible."""
        with self._lock:
            self._records = self._read()
            return deepcopy(self._find(self._records, identifier))

    def remember_pairing(self, pairing, config_dir, *, identifier=None, name=None):
        """Save a private code and its public list entry before authentication.

        The registry lock serializes both writes across controller windows. Write
        the protected code first; if the registry write fails, restore the old
        vault bytes. No password is accepted by this API or put in the registry.
        A replacement keeps the saved computer's ID, name and history.
        """
        import mesh
        if not isinstance(pairing, mesh.HostPairing) or not pairing.fingerprint:
            raise ValueError("Use um código atualizado que inclua o certificado do Hospedeiro.")
        address = f"mesh-{pairing.public_id}.invalid"
        if name is not None and not isinstance(name, str):
            raise ValueError("Informe um nome amigável válido.")
        clean_name = _name(name) if name and name.strip() else None
        vault = Path(config_dir) / "remembered" / f"{pairing.public_id}.bin"
        with self._lock, self._file_lock():
            records = deepcopy(self._read())
            previous_registry = self.path.read_bytes() if self.path.exists() else None
            matches = [item for item in records if item["address"] == address]
            if identifier is None and len(matches) > 1:
                raise ValueError("Há mais de um cadastro para este código. Selecione o computador e use Atualizar código.")
            record = self._find(records, identifier) if identifier is not None else (matches[0] if matches else None)
            self._unique(records, address, pairing.port, record["id"] if record else None)
            if record is None:
                if len(records) >= MAX_COMPUTERS:
                    raise ValueError("O cadastro já contém 256 computadores. Remova um antes de adicionar outro.")
                timestamp = _timestamp()
                record = {"id": str(uuid.uuid4()), "name": clean_name or "Computador pela internet",
                          "address": address, "port": pairing.port, "created_at": timestamp,
                          "updated_at": timestamp, "last_seen": None}
                records.append(record)
            else:
                record.update(address=address, port=pairing.port, updated_at=_timestamp())
                if clean_name:
                    record["name"] = clean_name
            try:
                if vault.is_symlink() or (vault.exists() and vault.stat().st_size > 16384):
                    raise mesh.MeshError("O cofre deste código não pôde ser atualizado com segurança.")
                previous = vault.read_bytes() if vault.exists() else None
            except OSError:
                raise mesh.MeshError("Não foi possível ler o armazenamento protegido deste código.") from None
            try:
                mesh.save_pairing_code(vault, pairing.code)
                if mesh.load_pairing_code(vault) != pairing.code:
                    raise mesh.MeshError("O código não pôde ser confirmado no cofre. O acesso não foi salvo.")
                self._write(records)
            except (OSError, ValueError, mesh.MeshError):
                rollback_failed = False
                try:
                    current_vault = vault.read_bytes() if vault.exists() else None
                    if current_vault != previous:
                        if previous is None:
                            vault.unlink(missing_ok=True)
                        else:
                            mesh._atomic_private_write(vault, previous)
                    if (previous is None and vault.exists()) or (previous is not None and vault.read_bytes() != previous):
                        raise OSError("O cofre anterior não pôde ser confirmado.")
                except (OSError, mesh.MeshError):
                    rollback_failed = True
                try:
                    self._restore_file_bytes(previous_registry)
                except OSError:
                    rollback_failed = True
                if rollback_failed:
                    raise OSError("Não foi possível confirmar a restauração do cadastro e do cofre. Nenhuma conexão foi iniciada. Confira o acesso salvo e atualize o código antes de usar.") from None
                raise
            self._records = records
            return deepcopy(record)

    def mark_seen(self, identifier, timestamp=None):
        seen = _timestamp(timestamp)
        def operation(records):
            record = self._find(records, identifier)
            record.update(last_seen=seen, updated_at=_timestamp())
            return record
        return self._mutate(operation)
