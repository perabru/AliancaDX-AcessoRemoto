"""Instalação AliançaDX do computador que compartilha a tela."""
from installer_python import Installer

if __name__ == "__main__":
    raise SystemExit(Installer(role="host").run())
