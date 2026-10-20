"""Instalação AliançaDX do computador que controla."""
from installer_python import Installer

if __name__ == "__main__":
    raise SystemExit(Installer(role="controller").run())
