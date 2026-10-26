import os
import sys
import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../installer"))
)

import install


def test_xdm_config_directory_structure():
  # Comprobar que la función retorne una ruta válida basada en el home del usuario
  path = install.get_xdm_config_dir()
  assert path.endswith(os.path.join(".xdman", "companion"))
  assert os.path.isabs(path)
